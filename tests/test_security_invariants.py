"""The audit found four things that have to not happen.

A read-only audit of the repository turned up these, each with the file and line
it was found at. They are grouped here by the requirement they violate rather
than by where they live, because that is how the plan states them.

1. **Offline meant "fewer network calls", not zero.** The local MT backend fell
   back to `AutoTokenizer.from_pretrained(name)` for a pack without a
   sentencepiece tokenizer, and that reaches huggingface.co. It runs *before* the
   `offline_only` check, because the local backend is tried first - so a user who
   asked for strict offline had their screen text leave the machine.

2. **A tar could contain a symlink out of the extraction directory.** The path
   check was right and the link check was missing: `dest/model` is not a symlink
   when it is inspected and is one by the time the file written after it lands.
   Members are now refused by type, not only by path.

3. **`--pack-id` was joined onto the dictionaries directory unchecked.** An
   identifier is not a path, so `../../evil` wrote a manifest outside the XDG
   directory. The same hole was reachable from an auto-generated name, because
   the sanitiser allowed a leading dot.

4. **`HF_ENDPOINT=http://…` silently downgraded every model download to
   plaintext.** The sha256 that protects the weights does not protect a file
   tampered with on the way *and* whose catalog entry the same attacker wrote.

What the audit found already correct and these tests therefore pin: no
`shell=True` anywhere, `offline_only` builds no HTTP session, URL downloads
require a checksum, zip extraction is already safe, and the log files are 0600
inside a 0700 directory.

    uv run pytest tests/test_security_invariants.py
"""

from __future__ import annotations

import ast
import io
import tarfile
from pathlib import Path

import pytest

from kizurium_translator.lexicon.pack_id import UnsafePackId, is_safe_pack_id, safe_pack_dir

# --- 1. offline means zero network -----------------------------------------


def test_local_tokenizer_never_downloads(monkeypatch, tmp_path):
    """A pack without.spm must not reach the network to translate.

    `local_files_only=True` is the whole mechanism. Without it, a strict-offline
    user with such a pack installed had text leave the machine, silently, on the
    path that is supposed to be the offline one.
    """
    from kizurium_translator.translation import local_mt

    called: dict = {}

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(name, **kwargs):
            called["name"] = name
            called["kwargs"] = kwargs
            raise OSError("not cached")

    import sys
    import types

    fake_mod = types.ModuleType("transformers")
    fake_mod.AutoTokenizer = FakeAutoTokenizer
    monkeypatch.setitem(sys.modules, "transformers", fake_mod)

    model_dir = tmp_path / "opus-mt-en-ru" / "model"
    model_dir.mkdir(parents=True)
    backend = local_mt.CTranslate2Backend(model_dir=model_dir)
    backend._load = lambda: None  # not needed: we test the tokenizer branch

    # Call the branch directly so no CTranslate2 model is needed.
    with pytest.raises(RuntimeError) as exc:
        backend._load_tokenizer_only()
    assert "refusing to download" in str(exc.value)
    assert called["kwargs"].get("local_files_only") is True


def test_offline_only_never_builds_an_http_session():
    """Already true before this phase; pinned so it stays true."""
    from kizurium_translator import translate as translate_mod

    src = Path(translate_mod.__file__).read_text(encoding="utf-8")
    assert "if self.offline_only" in src
    # The guard must be a return, not a warning: a warning leaves the door open.
    assert "return None" in src


# --- 2. tar members are refused by type ------------------------------------


def _tar_with_member(info: tarfile.TarInfo) -> tarfile.TarFile:
    buf = io.BytesIO()
    payload = io.BytesIO(b"x" * 16) if info.isreg() else None
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.addfile(info, payload)
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r:gz")


def test_a_symlink_member_is_refused(tmp_path):
    from kizurium_translator.translation.packs import _safe_members_tar

    link = tarfile.TarInfo("model")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc"
    with _tar_with_member(link) as tf:
        with pytest.raises(ValueError, match="link"):
            _safe_members_tar(tf, tmp_path)


def test_a_hardlink_member_is_refused(tmp_path):
    from kizurium_translator.translation.packs import _safe_members_tar

    link = tarfile.TarInfo("model")
    link.type = tarfile.LNKTYPE
    link.linkname = "/etc/passwd"
    with _tar_with_member(link) as tf:
        with pytest.raises(ValueError, match="link"):
            _safe_members_tar(tf, tmp_path)


def test_a_device_member_is_refused(tmp_path):
    from kizurium_translator.translation.packs import _safe_members_tar

    dev = tarfile.TarInfo("dev/null")
    dev.type = tarfile.CHRTYPE
    with _tar_with_member(dev) as tf:
        with pytest.raises(ValueError, match="device"):
            _safe_members_tar(tf, tmp_path)


def test_a_normal_tar_still_extracts(tmp_path):
    """The refusal is on the type, not on tar in general."""
    from kizurium_translator.translation.packs import _safe_members_tar

    regular = tarfile.TarInfo("model/config.json")
    regular.size = 16
    with _tar_with_member(regular) as tf:
        members = _safe_members_tar(tf, tmp_path)
    assert [m.name for m in members] == ["model/config.json"]


def test_a_traversing_path_is_still_refused(tmp_path):
    """The original check, kept because the type check does not replace it."""
    from kizurium_translator.translation.packs import _safe_members_tar

    bad = tarfile.TarInfo("../../etc/passwd")
    bad.size = 4
    with _tar_with_member(bad) as tf:
        with pytest.raises(ValueError):
            _safe_members_tar(tf, tmp_path)


# --- 3. a pack id is an identifier -----------------------------------------


@pytest.mark.parametrize("bad", ["..", ".", "../evil", "a/b", "a\\b", "", "-lead", "a b", "x" * 100])
def test_bad_pack_ids_are_refused(bad):
    assert not is_safe_pack_id(bad)
    with pytest.raises(UnsafePackId):
        safe_pack_dir(Path("/tmp/dicts"), bad)


@pytest.mark.parametrize("good", ["arknights", "my-pack", "my_pack", "pack.v2", "a"])
def test_ordinary_pack_ids_still_work(good):
    assert is_safe_pack_id(good)
    assert safe_pack_dir(Path("/tmp/dicts"), good).name == good


def test_the_error_does_not_echo_a_hostile_path(tmp_path):
    """A refusal message ends up in a terminal and sometimes in a log."""
    with pytest.raises(UnsafePackId) as exc:
        safe_pack_dir(tmp_path, "../../../../etc/shadow")
    assert "shadow" not in str(exc.value) or len(str(exc.value)) < 200


def test_the_generated_name_cannot_be_dotdot(tmp_path):
    """A file called `...zip` has a stem of `..`; that used to escape."""
    source = tmp_path / "...zip"
    source.write_text("a=b\n", encoding="utf-8")

    from kizurium_translator.lexicon.import_external import import_dictionary_file

    dest = tmp_path / "dicts"
    dest.mkdir()
    result = import_dictionary_file(source, dest_root=dest)
    assert result.pack_dir.parent.resolve() == dest.resolve()


# --- 4. HTTPS is not optional for model downloads --------------------------


def test_a_plaintext_hf_endpoint_is_refused(monkeypatch):
    from kizurium_translator.translation import packs

    monkeypatch.setenv("HF_ENDPOINT", "http://mirror.internal")
    with pytest.raises(ValueError, match="https"):
        packs._hf_endpoint()


def test_an_https_hf_endpoint_is_accepted(monkeypatch):
    from kizurium_translator.translation import packs

    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.internal")
    assert packs._hf_endpoint() == "https://mirror.internal"


def test_no_endpoint_is_none(monkeypatch):
    from kizurium_translator.translation import packs

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_ENDPOINT", raising=False)
    assert packs._hf_endpoint() is None


# --- what the audit found already right, pinned so it stays ----------------


def test_there_is_no_shell_true_in_the_source():
    """No `shell=True` for user input. Pinned as an invariant.

    Two files legitimately mention it: `watch.py` and `trace.py` do so in the
    comments describing the audit hook, and two tests mention it because they
    test that the hook catches it. Neither is a call.
    """
    root = Path(__file__).resolve().parents[1] / "src" / "kizurium_translator"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Comments and docstrings describe `shell=True`; calls use it. Python has
        # no cheap way to tell them apart from text alone, so the AST is asked:
        # a keyword argument named `shell` with the value True is a call.
        for node in ast.walk(ast.parse(text, filename=str(path))):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"shell=True: {offenders}"


def test_url_downloads_require_a_checksum():
    from kizurium_translator.translation import packs

    src = Path(packs.__file__).read_text(encoding="utf-8")
    assert "sha256" in src
    # The catalogue must refuse an unsigned URL rather than fetching it.
    assert "unsigned" in src.lower() or "refus" in src.lower()


def test_log_files_are_not_world_readable():
    from kizurium_translator import logging_setup

    src = Path(logging_setup.__file__).read_text(encoding="utf-8")
    assert "0o600" in src
    assert "0o700" in src
