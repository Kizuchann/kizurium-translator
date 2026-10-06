"""Argos is an optional offline candidate, not a core backend.

"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translate import Translator  # noqa: E402
from kizurium_translator.translation import argos_probe  # noqa: E402
from kizurium_translator.translation.argos_probe import (  # noqa: E402
    ArgosProbeResult,
    _NetworkBlocked,
    probe_argos,
)

ROOT = Path(__file__).resolve().parent.parent


def test_probe_reports_missing_package(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("argostranslate"):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    got = probe_argos(force_offline=False)
    assert got.available is False
    assert got.strict_offline_allowed is False
    assert "not installed" in got.reason


def test_probe_flags_hidden_network(monkeypatch):
    pkg = types.ModuleType("argostranslate")
    pkg.__path__ = []  # mark as package
    translate = types.ModuleType("argostranslate.translate")

    def translate_hits_net(text, source, target):
        raise _NetworkBlocked("helper model download")

    translate.translate = translate_hits_net
    monkeypatch.setitem(sys.modules, "argostranslate", pkg)
    monkeypatch.setitem(sys.modules, "argostranslate.translate", translate)
    monkeypatch.setattr(
        argos_probe,
        "_block_network",
        lambda monkeypatch_setattr=None: (lambda: None),
    )

    got = probe_argos(force_offline=True)
    assert got.available is True
    assert got.offline_ok is False
    assert got.strict_offline_allowed is False
    assert "network" in got.reason.lower()


def test_probe_passes_when_offline_translation_works(monkeypatch):
    pkg = types.ModuleType("argostranslate")
    pkg.__path__ = []
    translate = types.ModuleType("argostranslate.translate")
    translate.translate = lambda text, source, target: "Привет"
    monkeypatch.setitem(sys.modules, "argostranslate", pkg)
    monkeypatch.setitem(sys.modules, "argostranslate.translate", translate)
    monkeypatch.setattr(
        argos_probe,
        "_block_network",
        lambda monkeypatch_setattr=None: (lambda: None),
    )
    got = probe_argos(force_offline=True)
    assert got == ArgosProbeResult(
        available=True,
        offline_ok=True,
        strict_offline_allowed=True,
        reason="argos translated with network blocked",
        sample="Привет",
    )


def test_translator_path_does_not_call_argos(tmp_path, monkeypatch):
    """Argos must not be on the live translate path (optional candidate only)."""
    calls: list[str] = []

    def boom(*a, **k):
        calls.append("argos")
        raise AssertionError("argos must not run")

    monkeypatch.setitem(
        sys.modules,
        "kizurium_translator.translation.argos_probe",
        types.SimpleNamespace(probe_argos=boom, argos_allowed_in_strict_offline=boom),
    )
    tr = Translator(
        target="ru",
        source="en",
        use_gtx=False,
        allow_slow=False,
        cache_path=tmp_path / "c.sqlite",
        glossary={"Hello": "Привет"},
        log=lambda m: None,
    )
    assert tr.translate("Hello") == "Привет"
    assert calls == []


def test_docs_and_inventory_mention_argos():
    assert (ROOT / "docs" / "argos.md").is_file()
    inv = (ROOT / "docs" / "licenses-inventory.toml").read_text(encoding="utf-8")
    assert "Argos-Translate" in inv
    low = inv.lower()
    assert "optional" in low or "candidate" in low, "инвентарь не называет Argos кандидатом"
