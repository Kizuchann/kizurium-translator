""": Rapid primary, Meiki JP specialist, Tesseract fallback."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.config import Config  # noqa: E402
from kizurium_translator.core import text as core_text  # noqa: E402
from kizurium_translator.ocr.routing import (  # noqa: E402
    ENGINE_HIERARCHY,
    FALLBACK,
    JP_SPECIALIST,
    PRIMARY,
    available_engines,
    plan_engines,
)


def test_hierarchy_order():
    assert ENGINE_HIERARCHY == ("rapid", "meiki", "tesseract")
    assert PRIMARY == "rapid"
    assert JP_SPECIALIST == "meiki"
    assert FALLBACK == "tesseract"


def test_config_default_matches_hierarchy():
    assert Config().ocr_engines == ENGINE_HIERARCHY
    assert core_text.OCR_ENGINE_ORDER == ENGINE_HIERARCHY


def test_plan_does_not_force_all_engines_on_hot_path(monkeypatch):
    monkeypatch.setattr(
        "kizurium_translator.ocr.routing.available_engines",
        lambda: ("rapid", "meiki", "tesseract"),
)
    latin = plan_engines(cjk_hint=False, primary_empty=False)
    assert latin.engines == ("rapid",)
    assert "tesseract" not in latin.engines
    assert "meiki" not in latin.engines

    cjk = plan_engines(cjk_hint=True, primary_empty=False)
    assert cjk.engines == ("rapid", "meiki")
    assert FALLBACK not in cjk.engines

    empty = plan_engines(cjk_hint=False, primary_empty=True)
    assert FALLBACK in empty.engines
    assert PRIMARY in empty.engines or FALLBACK in empty.engines


def test_force_all_only_for_diagnostics(monkeypatch):
    monkeypatch.setattr(
        "kizurium_translator.ocr.routing.available_engines",
        lambda: ("rapid", "meiki", "tesseract"),
)
    diag = plan_engines(force_all=True)
    assert diag.engines == ("rapid", "meiki", "tesseract")
    assert "diagnostic" in diag.reason


def test_available_engines_respects_config_disable(monkeypatch):
    import kizurium_translator.ocr.routing as routing

    monkeypatch.setattr(routing, "engine_enabled", lambda name: name == "tesseract")
    monkeypatch.setattr(
        routing.shutil,
        "which",
        lambda name: "/bin/tesseract" if name == "tesseract" else None,
)
    assert available_engines() == ("tesseract",)
