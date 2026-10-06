"""Offline language packs.

Catalog + install flow:

```text
.part → stream + sha256 → verify → atomic rename → activate manifest
```

Checksum mismatch leaves the previously active version untouched. Packs live
under ``XDG_DATA_HOME/.../models/<pack-id>/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tarfile
    import zipfile
CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "language_packs" / "catalog.toml"


@dataclass(frozen=True)
class PackInfo:
    id: str
    version: str
    source: str
    target: str
    engine: str
    model_format: str
    quantization: str
    url: str
    sha256: str
    size_bytes: int
    license: str
    license_url: str
    source_url: str
    min_app_version: str
    label: str = ""
    hf_repo: str = ""
    hf_revision: str = ""

    @property
    def downloadable(self) -> bool:
        """True when install can proceed without a local --from path."""
        return bool((self.url or "").strip() or (self.hf_repo or "").strip())


@dataclass(frozen=True)
class PackStatus:
    info: PackInfo
    installed: bool
    path: Path | None
    installed_version: str | None


def _models_root(root: Path | None = None) -> Path:
    if root is not None:
        return root
    from ..paths import default_paths

    return default_paths().models_dir


# NLLB must not enter the default catalog without a legal decision.
_BLOCKED_DEFAULT_ENGINES = frozenset({"nllb", "nllb-200", "fairseq-nllb"})


def nllb_is_default_blocked(pack_id: str, engine: str = "") -> bool:
    """True when a pack would make NLLB a default/catalog backend."""
    pid = (pack_id or "").strip().lower()
    eng = (engine or "").strip().lower()
    if eng in _BLOCKED_DEFAULT_ENGINES or eng.startswith("nllb"):
        return True
    return pid.startswith("nllb") or "nllb" in pid.split("-")


def load_catalog(path: Path | None = None) -> tuple[PackInfo, ...]:
    catalog = path or CATALOG_PATH
    data = tomllib.loads(catalog.read_text(encoding="utf-8"))
    out: list[PackInfo] = []
    for row in data.get("pack", []):
        pack_id = str(row["id"])
        engine = str(row.get("engine", "ctranslate2"))
        if nllb_is_default_blocked(pack_id, engine):
            raise ValueError(
                f"catalog pack {pack_id!r} uses NLLB; it is not allowed as a "
                "default/catalog model until a separate legal decision"
            )
        out.append(
            PackInfo(
                id=pack_id,
                version=str(row.get("version", "1")),
                source=str(row["source"]),
                target=str(row["target"]),
                engine=engine,
                model_format=str(row.get("model_format", "ctranslate2")),
                quantization=str(row.get("quantization", "int8")),
                url=str(row.get("url", "")),
                sha256=str(row.get("sha256", "")),
                size_bytes=int(row.get("size_bytes", 0) or 0),
                license=str(row.get("license", "")),
                license_url=str(row.get("license_url", "")),
                source_url=str(row.get("source_url", "")),
                min_app_version=str(row.get("min_app_version", "0.1.0")),
                label=str(row.get("label", row["id"])),
                hf_repo=str(row.get("hf_repo", "")),
                hf_revision=str(row.get("hf_revision", "")),
            )
        )
    return tuple(out)


def _read_installed_manifest(pack_dir: Path) -> dict:
    path = pack_dir / "manifest.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def list_pack_status(
    *,
    models_dir: Path | None = None,
    catalog: Path | None = None,
) -> list[PackStatus]:
    root = _models_root(models_dir)
    statuses: list[PackStatus] = []
    for info in load_catalog(catalog):
        pack_dir = root / info.id
        man = _read_installed_manifest(pack_dir)
        installed = bool(man) and (pack_dir / "model").is_dir()
        statuses.append(
            PackStatus(
                info=info,
                installed=installed,
                path=pack_dir if installed else None,
                installed_version=str(man.get("version")) if installed else None,
            )
        )
    return statuses


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _write_manifest(pack_dir: Path, info: PackInfo, *, extra: dict | None = None) -> None:
    lines = [
        f'id = "{info.id}"',
        f'version = "{info.version}"',
        f'source = "{info.source}"',
        f'target = "{info.target}"',
        f'engine = "{info.engine}"',
        f'model_format = "{info.model_format}"',
        f'quantization = "{info.quantization}"',
        f'url = "{info.url}"',
        f'sha256 = "{info.sha256}"',
        f"size_bytes = {info.size_bytes}",
        f'hf_repo = "{info.hf_repo}"',
        f'license = "{info.license}"',
        f'license_url = "{info.license_url}"',
        f'source_url = "{info.source_url}"',
        f'min_app_version = "{info.min_app_version}"',
        f'label = "{info.label}"',
    ]
    if extra:
        for key, value in extra.items():
            if isinstance(value, str):
                lines.append(f'{key} = "{value}"')
            else:
                lines.append(f"{key} = {json.dumps(value)}")
    (pack_dir / "manifest.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_members_zip(zf: "zipfile.ZipFile", dest: Path) -> list:
    """Reject zip-slip (absolute /.. paths outside dest)."""
    dest_root = dest.resolve()
    good = []
    for info in zf.infolist():
        name = info.filename
        if not name or name.endswith("/"):
            good.append(info)
            continue
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest_root) + os.sep) and target != dest_root:
            raise ValueError(f"refusing unsafe zip path: {name!r}")
        good.append(info)
    return good


def _safe_members_tar(tf: "tarfile.TarFile", dest: Path) -> list:
    """Members of a tar we are willing to write out.

    Two separate attacks, and only the first was covered before:

    - a path that escapes the destination (`../../etc/passwd`) - checked by
      resolving the name against the destination
    - a *symlink* inside the archive whose target escapes, followed by a regular
      file written through it. At the moment the link is checked, `dest/model` is
      not yet a link and the resolved path looks safe; the file lands afterwards
      and the escape has already happened.

    So links are refused outright, along with devices, fifos and sockets. A model
    pack has no use for any of them, and a refusal costs a pack that nobody ships.
    """
    dest_root = dest.resolve()
    good = []
    for member in tf.getmembers():
        name = member.name
        if not name:
            continue
        if member.issym() or member.islnk():
            raise ValueError(f"refusing tar link member: {name!r}")
        if member.isdev() or member.isfifo():
            raise ValueError(f"refusing tar device member: {name!r}")
        if not (member.isfile() or member.isdir()):
            # `TarInfo` has no `issocket` in every supported Python, so the
            # allow-list below is what covers it: a member that is neither a
            # regular file nor a directory is refused, whatever it is called.
            raise ValueError(f"refusing unusual tar member type: {name!r}")
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest_root) + os.sep) and target != dest_root:
            raise ValueError(f"refusing unsafe tar path: {name!r}")
        good.append(member)
    return good


def _extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffixes[-2:] == [".tar", ".gz"] or archive.name.endswith(".tgz"):
        import tarfile

        with tarfile.open(archive, "r:gz") as tf:
            members = _safe_members_tar(tf, dest)
            # Python 3.12 has `filter="data"`, which is the belt; `_safe_members_tar`
            # is the braces. The filter keyword does not exist on 3.11, which the
            # project supports, so it is passed only where it is understood rather
            # than guarded by a version check the interpreter already does.
            kwargs: dict = {}
            if hasattr(tarfile, "data_filter"):
                kwargs["filter"] = "data"
            try:
                tf.extractall(dest, members=members, **kwargs)
            except TypeError:
                tf.extractall(dest, members=members)
        return
    if archive.suffix == ".zip" or _looks_like_zip(archive):
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            for info in _safe_members_zip(zf, dest):
                zf.extract(info, dest)
        return
    # Bare directory payload: copy as model/
    model = dest / "model"
    if archive.is_dir():
        if model.exists():
            shutil.rmtree(model)
        shutil.copytree(archive, model)
        return
    raise ValueError(f"unsupported pack archive: {archive}")


def _looks_like_zip(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as fh:
            return fh.read(2) == b"PK"
    except OSError:
        return False


def _normalize_model_dir(pack_dir: Path) -> None:
    """Ensure ``pack_dir/model`` exists after extract (handles nested folders)."""
    model = pack_dir / "model"
    if model.is_dir():
        return
    # Single top-level directory from the archive → rename to model/
    children = [p for p in pack_dir.iterdir() if p.name != "manifest.toml"]
    if len(children) == 1 and children[0].is_dir():
        children[0].rename(model)
        return
    # Files dumped flat into pack_dir
    if children:
        model.mkdir(parents=True, exist_ok=True)
        for child in children:
            child.rename(model / child.name)


def install_pack_from_file(
    archive_or_dir: Path,
    info: PackInfo,
    *,
    models_dir: Path | None = None,
    expected_sha256: str | None = None,
) -> Path:
    """Install from a local archive/directory with optional sha256 check."""
    root = _models_root(models_dir)
    root.mkdir(parents=True, exist_ok=True)
    expected = (expected_sha256 or info.sha256 or "").strip().lower()
    if archive_or_dir.is_file() and expected:
        digest = sha256_file(archive_or_dir)
        if digest != expected:
            raise ValueError(
                f"sha256 mismatch for {archive_or_dir.name}: got {digest}, want {expected}"
            )
    staging = Path(tempfile.mkdtemp(prefix=f".{info.id}.", dir=root))
    try:
        _extract_archive(archive_or_dir, staging)
        _normalize_model_dir(staging)
        if not (staging / "model").is_dir():
            raise ValueError("pack has no model/ directory after extract")
        _write_manifest(staging, info)
        final = root / info.id
        # Atomic replace: only swap after staging is complete. On mismatch above
        # we never reach here, so the previous final/ stays.
        backup = root / f".{info.id}.bak"
        if final.exists():
            if backup.exists():
                shutil.rmtree(backup)
            final.rename(backup)
        staging.rename(final)
        if backup.exists():
            shutil.rmtree(backup)
        return final
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KiB"
    if n < 1024**3:
        return f"{n / (1024**2):.1f} MiB"
    return f"{n / (1024**3):.2f} GiB"


def _have_aria2() -> bool:
    return bool(shutil.which("aria2c"))


# Same file path on each host. HF_ENDPOINT (or HUGGINGFACE_HUB_ENDPOINT) replaces
# this list — that is how people in regions where huggingface.co is blocked
# already point the Hugging Face tools at a mirror.
_HF_MIRRORS = (
    "https://huggingface.co",
    "https://hf-mirror.com",
)
_HF_HOSTS = _HF_MIRRORS


class DownloadNotFound(RuntimeError):
    """The URL answered 404 on every host we tried."""


def _hf_endpoint() -> str | None:
    """A user-supplied Hugging Face endpoint, or None.

    Only `https://`: HTTPS is enforced, and this is where
    every model download goes: an `http://` value silently downgrades the whole
    install path to plaintext, and the sha256 that protects the weights does not
    protect a file that was tampered with on the way *and* whose catalog entry the
    same attacker wrote.

    A mirror on `http://` is a real configuration in some networks, and it is
    refused anyway - the answer there is a proxy, and the message says so.
    """
    raw = (
        os.environ.get("HF_ENDPOINT")
        or os.environ.get("HUGGINGFACE_HUB_ENDPOINT")
        or ""
    ).strip().rstrip("/")
    if raw.startswith("https://"):
        return raw
    if raw.startswith("http://"):
        raise ValueError(
            f"HF_ENDPOINT must be https://, got {raw!r}; "
            "use a proxy for a plaintext network, not a plaintext endpoint"
        )
    return None


def _rewrite_hf_host(url: str, endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    for host in _HF_HOSTS:
        if url.startswith(host + "/") or url == host:
            return endpoint + url[len(host) :]
    return url


def candidate_urls(url: str) -> tuple[str, ...]:
    """Hosts to try for one artifact, in order.

    A custom ``HF_ENDPOINT`` is used alone. Otherwise the official host comes
    first and ``hf-mirror.com`` is the fallback for places where the US CDN
    is blocked or times out. Non-HF URLs are unchanged.
    """
    endpoint = _hf_endpoint()
    if endpoint:
        return (_rewrite_hf_host(url, endpoint),)
    if not any(url.startswith(host + "/") for host in _HF_HOSTS):
        return (url,)
    out: list[str] = []
    for host in _HF_MIRRORS:
        rewritten = _rewrite_hf_host(url, host)
        if rewritten not in out:
            out.append(rewritten)
    return tuple(out)


_progress_drawn = False


def _note_progress(label: str, done: int, total: int, speed: float) -> None:
    """One updating line. aria2's own summary is a wall of boxes; we don't use it."""
    import sys

    global _progress_drawn
    if not label or done < 32 * 1024:
        return
    if total > 0:
        pct = min(100.0, 100.0 * done / total)
        msg = (
            f"  {label}  {_fmt_bytes(done)} / {_fmt_bytes(total)}"
            f"  {pct:3.0f}%  {_fmt_bytes(int(speed))}/s"
        )
    else:
        msg = f"  {label}  {_fmt_bytes(done)}  {_fmt_bytes(int(speed))}/s"
    _progress_drawn = True
    if sys.stderr.isatty():
        print(f"\r{msg:<72}", end="", file=sys.stderr, flush=True)
    else:
        print(msg, file=sys.stderr, flush=True)


def finish_progress() -> None:
    import sys

    global _progress_drawn
    if _progress_drawn:
        print(file=sys.stderr, flush=True)
        _progress_drawn = False


def _download_aria2(
    url: str,
    part_path: Path,
    *,
    label: str = "",
    total_hint: int = 0,
) -> Path:
    """Multi-connection download. aria2 stays quiet; we print one progress line."""
    import subprocess
    import time

    part_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c",
        "--quiet=true",
        "--summary-interval=0",
        "--download-result=hide",
        "--console-log-level=error",
        "-x16",
        "-s16",
        "-k1M",
        "--max-tries=2",
        "--retry-wait=1",
        "--connect-timeout=15",
        "--timeout=60",
        "--file-allocation=none",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--user-agent=kizurium-translator/0.1",
        f"--dir={part_path.parent}",
        f"--out={part_path.name}",
        url,
    ]
    if part_path.is_file() and part_path.stat().st_size > 0:
        cmd.insert(1, "-c")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic()
    base = part_path.stat().st_size if part_path.is_file() else 0
    while proc.poll() is None:
        size = part_path.stat().st_size if part_path.is_file() else 0
        elapsed = max(0.001, time.monotonic() - started)
        _note_progress(label, size, total_hint, max(0, size - base) / elapsed)
        time.sleep(0.3)
    code = proc.returncode
    if code in {3, 4}:
        part_path.unlink(missing_ok=True)
        raise DownloadNotFound(f"404 not found: {url}")
    if code != 0 or not part_path.is_file() or part_path.stat().st_size <= 0:
        raise RuntimeError(f"aria2c failed (code {code}) for {url}")
    size = part_path.stat().st_size
    elapsed = max(0.001, time.monotonic() - started)
    _note_progress(label, size, total_hint or size, max(0, size - base) / elapsed)
    return part_path


def _download_urllib(
    url: str,
    part_path: Path,
    *,
    timeout: float = 120.0,
    label: str = "",
    total_hint: int = 0,
) -> Path:
    """Single-stream urllib fallback. Same one-line progress as aria2."""
    import time

    part_path.parent.mkdir(parents=True, exist_ok=True)
    existing = part_path.stat().st_size if part_path.is_file() else 0
    headers = {"User-Agent": "kizurium-translator/0.1"}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
    req = urllib.request.Request(url, headers=headers)
    started = time.monotonic()
    try:
        resp_cm = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            part_path.unlink(missing_ok=True)
            raise DownloadNotFound(f"404 not found: {url}") from exc
        raise RuntimeError(f"urllib HTTP {exc.code} for {url}") from exc
    with resp_cm as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        mode = "ab" if status == 206 else "wb"
        if status != 206:
            existing = 0
        length_hdr = resp.headers.get("Content-Length")
        chunk_len = int(length_hdr) if length_hdr and length_hdr.isdigit() else 0
        total = existing + chunk_len if chunk_len else 0
        done = existing
        shown_total = total_hint or total
        with part_path.open(mode) as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                elapsed = max(0.001, time.monotonic() - started)
                _note_progress(label, done, shown_total, (done - existing) / elapsed)
    return part_path


def download_to_part(
    url: str,
    part_path: Path,
    *,
    timeout: float = 120.0,
    label: str = "",
    total_hint: int = 0,
) -> Path:
    """Download ``url`` into ``part_path``.

    Prefer aria2c with 16 connections. A dead host is retried on the next
    mirror. A 404 is final: the file is not in the repo, and a mirror of the
    same repo does not have it either.
    """
    import sys

    urls = candidate_urls(url)
    errors: list[BaseException] = []

    def _fetch(candidate: str, *, via_aria: bool) -> Path:
        if via_aria:
            return _download_aria2(
                candidate, part_path, label=label, total_hint=total_hint
            )
        return _download_urllib(
            candidate,
            part_path,
            timeout=timeout,
            label=label,
            total_hint=total_hint,
        )

    methods = ("aria2", "urllib") if _have_aria2() else ("urllib",)
    for method in methods:
        if method == "urllib" and methods[0] == "aria2" and errors:
            print("  хост не ответил, повторяю одним потоком…", file=sys.stderr, flush=True)
        for index, candidate in enumerate(urls):
            if index and not isinstance(errors[-1], DownloadNotFound):
                print("  повторяю с зеркала…", file=sys.stderr, flush=True)
            try:
                return _fetch(candidate, via_aria=method == "aria2")
            except DownloadNotFound:
                raise
            except Exception as exc:  # noqa: BLE001 — next mirror
                errors.append(exc)
    detail = "; ".join(str(e) for e in errors) or "no url"
    raise RuntimeError(f"download failed for {url}: {detail}")


_HF_MODEL_ALLOW = (
    "model.bin",
    "config.json",
    "source.spm",
    "target.spm",
    "shared_vocabulary.json",
    "vocabulary.json",
    "vocab.json",
    "tokenizer_config.json",
    "manifest.json",
)


def install_pack_from_hf(
    info: PackInfo,
    *,
    models_dir: Path | None = None,
    repo_id: str | None = None,
) -> Path:
    """Download a ready CT2 tree from Hugging Face into the models dir.

    Files are fetched via ``/resolve/<rev>/<file>`` (same CDN as the zip packs)
    so aria2 multi-conn applies. ``snapshot_download`` is the fallback only.
    """
    import sys

    repo = (repo_id or info.hf_repo or "").strip()
    if not repo:
        raise ValueError(f"pack {info.id!r} has no hf_repo")
    rev = (info.hf_revision or "").strip()
    if not rev:
        raise ValueError(f"pack {info.id!r}: hf_repo install requires hf_revision pin")
    root = _models_root(models_dir)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{info.id}.hf.", dir=root))
    try:
        local_dir = staging / "raw"
        local_dir.mkdir(parents=True, exist_ok=True)
        got_any = False
        label = f"{info.source}→{info.target}"
        required = {"model.bin", "source.spm", "target.spm"}
        for name in _HF_MODEL_ALLOW:
            url = f"https://huggingface.co/{repo}/resolve/{rev}/{name}"
            dest = local_dir / name
            hint = int(info.size_bytes) if name == "model.bin" else 0
            try:
                download_to_part(url, dest, label=label, total_hint=hint)
            except DownloadNotFound:
                if name in required:
                    raise
                continue
            except Exception as exc:  # noqa: BLE001 — optional sidecars can miss
                if name in required:
                    raise
                print(f"  пропуск {name}: {exc}", file=sys.stderr, flush=True)
                continue
            if dest.is_file() and dest.stat().st_size > 0:
                got_any = True
        if not got_any or not (local_dir / "model.bin").is_file():
            # Last resort: hub library (single-connection / xet — often slower here).
            print(
                "    resolve/aria2 не собрал дерево — fallback snapshot_download…",
                file=sys.stderr,
                flush=True,
            )
            try:
                from huggingface_hub import snapshot_download
            except ImportError as exc:
                raise RuntimeError(
                    "нужен huggingface_hub или удачная aria2-загрузка model.bin"
                ) from exc
            snapshot_download(
                repo_id=repo,
                local_dir=str(local_dir),
                allow_patterns=list(_HF_MODEL_ALLOW),
                revision=rev,
            )
        cache = local_dir / ".cache"
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
        return install_pack_from_file(local_dir, info, models_dir=root)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install_pack(
    pack_id: str,
    *,
    models_dir: Path | None = None,
    catalog: Path | None = None,
    from_path: Path | None = None,
    url: str | None = None,
) -> Path:
    """Install a catalog pack from a local path, URL, or Hugging Face repo."""
    infos = {p.id: p for p in load_catalog(catalog)}
    if pack_id not in infos:
        raise KeyError(f"unknown pack id: {pack_id}")
    info = infos[pack_id]
    root = _models_root(models_dir)
    if from_path is not None:
        return install_pack_from_file(from_path, info, models_dir=root)

    source_url = (url or info.url or "").strip()
    if source_url:
        if not (info.sha256 or "").strip():
            raise ValueError(
                f"pack {pack_id!r}: url download requires sha256 in catalog "
                "(refuse unsigned downloads)"
            )
        root.mkdir(parents=True, exist_ok=True)
        part = root / f".{pack_id}.part"
        try:
            download_to_part(
                source_url,
                part,
                label=f"{info.source}→{info.target}",
                total_hint=int(info.size_bytes or 0),
            )
            digest = sha256_file(part)
            if digest != info.sha256.lower():
                part.unlink(missing_ok=True)
                raise ValueError(
                    f"sha256 mismatch downloading {pack_id}: got {digest}, want {info.sha256}"
                )
            return install_pack_from_file(part, info, models_dir=root, expected_sha256="")
        finally:
            finish_progress()
            part.unlink(missing_ok=True)
    if (info.hf_repo or "").strip():
        if not (info.hf_revision or "").strip():
            raise ValueError(
                f"pack {pack_id!r}: hf_repo install requires hf_revision pin in catalog"
            )
        try:
            return install_pack_from_hf(info, models_dir=root)
        finally:
            finish_progress()
    raise ValueError(
        f"pack {pack_id!r} has no download URL or hf_repo; pass --from PATH"
    )


def install_recommended_packs(
    *,
    models_dir: Path | None = None,
    catalog: Path | None = None,
    skip_installed: bool = True,
) -> list[tuple[str, Path | None, str]]:
    """Install every downloadable catalog pack. Returns (id, dest|None, note)."""
    results: list[tuple[str, Path | None, str]] = []
    for status in list_pack_status(models_dir=models_dir, catalog=catalog):
        info = status.info
        if skip_installed and status.installed:
            results.append((info.id, status.path, "already installed"))
            continue
        if not info.downloadable:
            results.append((info.id, None, "no download source in catalog"))
            continue
        try:
            dest = install_pack(info.id, models_dir=models_dir, catalog=catalog)
            results.append((info.id, dest, "installed"))
        except Exception as exc:  # noqa: BLE001 - report per-pack, keep going
            results.append((info.id, None, f"failed: {exc}"))
    return results


def find_installed_pair(
    source: str,
    target: str,
    *,
    models_dir: Path | None = None,
    engine: str = "ctranslate2",
) -> Path | None:
    """Return the model directory for an installed source→target pack, if any."""
    root = _models_root(models_dir)
    if not root.is_dir():
        return None
    for pack_dir in sorted(root.iterdir()):
        if not pack_dir.is_dir() or pack_dir.name.startswith("."):
            continue
        man = _read_installed_manifest(pack_dir)
        if not man:
            continue
        if str(man.get("engine", engine)) != engine:
            continue
        if str(man.get("source")) == source and str(man.get("target")) == target:
            model = pack_dir / "model"
            if model.is_dir():
                return model
    return None
