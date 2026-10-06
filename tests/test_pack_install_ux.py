"""Convenient one-command offline pack install (URL / HF / all)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translation import packs as packs_mod  # noqa: E402
from kizurium_translator.translation.packs import (  # noqa: E402
    install_pack,
    install_recommended_packs,
    load_catalog,
)


def test_candidate_urls_follow_hf_endpoint(monkeypatch):
    from kizurium_translator.translation.packs import candidate_urls

    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")
    monkeypatch.delenv("HUGGINGFACE_HUB_ENDPOINT", raising=False)
    got = candidate_urls(
        "https://huggingface.co/ordois/opus-mt-en-ru-ctranslate2-int8/resolve/main/x.zip"
    )
    assert got == (
        "https://hf-mirror.com/ordois/opus-mt-en-ru-ctranslate2-int8/resolve/main/x.zip",
    )


def test_candidate_urls_add_mirror_when_unset(monkeypatch):
    from kizurium_translator.translation.packs import candidate_urls

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_ENDPOINT", raising=False)
    got = candidate_urls("https://huggingface.co/org/repo/resolve/main/a.zip")
    assert got[0] == "https://huggingface.co/org/repo/resolve/main/a.zip"
    assert "https://hf-mirror.com/org/repo/resolve/main/a.zip" in got


def test_download_tries_next_host_before_urllib(tmp_path, monkeypatch):
    from kizurium_translator.translation.packs import download_to_part

    aria_calls: list[str] = []

    def fake_aria2(url, part_path, **_kwargs):
        aria_calls.append(url)
        if "hf-mirror.com" not in url:
            raise RuntimeError(f"aria2c failed (code 2) for {url}")
        part_path.write_bytes(b"fast")
        return part_path

    def urllib_must_not_run(*_a, **_k):
        raise AssertionError("urllib ran before the mirror")

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_ENDPOINT", raising=False)
    monkeypatch.setattr(packs_mod, "_have_aria2", lambda: True)
    monkeypatch.setattr(packs_mod, "_download_aria2", fake_aria2)
    monkeypatch.setattr(packs_mod, "_download_urllib", urllib_must_not_run)
    dest = tmp_path / "a.bin"
    download_to_part("https://huggingface.co/org/repo/resolve/main/a.bin", dest)
    assert dest.read_bytes() == b"fast"
    assert len(aria_calls) == 2


def test_download_404_stops_without_mirror(tmp_path, monkeypatch):
    from kizurium_translator.translation.packs import DownloadNotFound, download_to_part

    calls: list[str] = []

    def fake_aria2(url, part_path, **_kwargs):
        calls.append(url)
        raise DownloadNotFound(f"404 not found: {url}")

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_ENDPOINT", raising=False)
    monkeypatch.setattr(packs_mod, "_have_aria2", lambda: True)
    monkeypatch.setattr(packs_mod, "_download_aria2", fake_aria2)
    dest = tmp_path / "a.bin"
    try:
        download_to_part("https://huggingface.co/org/repo/resolve/main/a.bin", dest)
    except DownloadNotFound:
        pass
    else:
        raise AssertionError("404 must not be treated as success")
    assert len(calls) == 1


def test_install_pack_from_url_without_from(tmp_path, monkeypatch):
    info = next(p for p in load_catalog() if p.id == "opus-mt-en-ru")
    assert info.url

    archive = tmp_path / "fake.zip"
    # Build a tiny zip that looks like a CT2 tree after extract+normalize.
    import zipfile

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("model/model.bin", b"fake")
        zf.writestr("model/source.spm", b"x")
        zf.writestr("model/target.spm", b"y")

    def fake_download(url, part_path, *, timeout=120.0, **_kwargs):
        assert url == info.url
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(archive.read_bytes())
        return part_path

    monkeypatch.setattr(packs_mod, "download_to_part", fake_download)
    # Skip sha for this unit test by clearing expected via install path that
    # still checks — patch sha256_file to match catalog.
    monkeypatch.setattr(packs_mod, "sha256_file", lambda p: info.sha256)

    dest = install_pack("opus-mt-en-ru", models_dir=tmp_path / "models")
    assert (dest / "model" / "model.bin").is_file()
    assert (dest / "manifest.toml").is_file()


def test_install_pack_from_hf_repo(tmp_path, monkeypatch):
    raw = tmp_path / "hf-raw"
    raw.mkdir()
    (raw / "model.bin").write_bytes(b"ct2")
    (raw / "source.spm").write_text("s", encoding="utf-8")
    (raw / "target.spm").write_text("t", encoding="utf-8")

    import shutil

    def fake_download(url, part_path, *, timeout=120.0, **_kwargs):
        # install_pack_from_hf pulls /resolve/<rev>/<file> via download_to_part.
        name = url.rsplit("/", 1)[-1]
        if name in ("model.bin", "source.spm", "target.spm"):
            part_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw / name, part_path)
            return part_path
        raise RuntimeError("HTTP Error 404: Not Found")

    monkeypatch.setattr(packs_mod, "download_to_part", fake_download)

    dest = install_pack("opus-mt-ja-ru", models_dir=tmp_path / "models")
    assert (dest / "model" / "model.bin").read_bytes() == b"ct2"


def test_install_recommended_skips_installed(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_install(pack_id, **kwargs):
        calls.append(pack_id)
        d = tmp_path / "models" / pack_id
        (d / "model").mkdir(parents=True)
        (d / "model" / "model.bin").write_bytes(b"x")
        from kizurium_translator.translation.packs import _write_manifest, load_catalog

        info = next(p for p in load_catalog() if p.id == pack_id)
        _write_manifest(d, info)
        return d

    monkeypatch.setattr(packs_mod, "install_pack", fake_install)
    first = install_recommended_packs(models_dir=tmp_path / "models")
    assert {r[0] for r in first} == {"opus-mt-en-ru", "opus-mt-ja-ru"}
    assert all(r[2] == "installed" for r in first)
    assert sorted(calls) == ["opus-mt-en-ru", "opus-mt-ja-ru"]

    calls.clear()
    second = install_recommended_packs(models_dir=tmp_path / "models")
    assert calls == []
    assert all(r[2] == "already installed" for r in second)
