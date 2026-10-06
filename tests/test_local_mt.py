"""CTranslate2 local MT wired ahead of gtx when a pack exists."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translate import Translator  # noqa: E402
from kizurium_translator.translation import local_mt  # noqa: E402
from kizurium_translator.translation.packs import install_pack_from_file, load_catalog  # noqa: E402


class _FakeCT2Result:
    def __init__(self, tokens):
        self.hypotheses = [tokens]


class _FakeCT2Translator:
    def translate_batch(self, batches, beam_size=1, return_scores=False):
        # Echo a marked translation so we know the local path ran.
        out = []
        for batch in batches:
            out.append(_FakeCT2Result([f"LOC:{t}" for t in batch] or ["LOC:"]))
        return out


def test_via_local_used_before_gtx(tmp_path, monkeypatch):
    local_mt.clear_local_backends()
    # Install a fake pack tree
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "model.bin").write_bytes(b"x")
    info = next(p for p in load_catalog() if p.id == "opus-mt-en-ru")
    install_pack_from_file(raw, info, models_dir=tmp_path / "models")

    monkeypatch.setattr(
        local_mt,
        "find_installed_pair",
        lambda source, target, models_dir=None, engine="ctranslate2": tmp_path
        / "models"
        / "opus-mt-en-ru"
        / "model",
    )

    class FakeBackend(local_mt.CTranslate2Backend):
        def translate(self, texts, source, target):
            return [f"локально:{t}" for t in texts]

    monkeypatch.setattr(
        local_mt,
        "local_backend_for",
        lambda source, target, models_dir=None, beam_size=1: FakeBackend(
            tmp_path / "models" / "opus-mt-en-ru" / "model"
        ),
    )

    tr = Translator(
        target="ru",
        source="en",
        use_gtx=True,
        cache_path=tmp_path / "c.sqlite",
        glossary={},
        log=lambda m: None,
    )
    tr.via_gtx = lambda text, source=None: (_ for _ in ()).throw(
        AssertionError("gtx must not run when local hits")
    )
    assert tr.translate("Hello local") == "локально:Hello local"


def test_local_backend_for_none_without_pack(tmp_path, monkeypatch):
    local_mt.clear_local_backends()
    monkeypatch.setattr(
        local_mt,
        "find_installed_pair",
        lambda *a, **k: None,
    )
    assert local_mt.local_backend_for("en", "ru", models_dir=tmp_path) is None


def test_ctranslate2_backend_encode_decode_with_spm(tmp_path, monkeypatch):
    # Avoid real ctranslate2: inject a fake translator and SPM-like encode/decode.
    model = tmp_path / "model"
    model.mkdir()
    backend = local_mt.CTranslate2Backend(model)

    class FakeSP:
        def encode(self, text, out_type=str):
            return text.split()

        def decode(self, tokens):
            cleaned = [t.replace("LOC:", "") for t in tokens if t not in {"</s>", "LOC:</s>"}]
            return " ".join(cleaned)

    backend._sp_source = FakeSP()
    backend._sp_target = FakeSP()
    backend._translator = _FakeCT2Translator()

    # Bypass _load
    backend._load = lambda: None
    assert backend._encode("one two") == ["one", "two", "</s>"]
    out = backend.translate(["one two"], "en", "ru")
    assert out == ["one two"]
