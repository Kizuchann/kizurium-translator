"""Screen to clipboard: OCR only, no translation and no live heuristics.

The region selector hands a picture to a recogniser and the recogniser
hands text to the clipboard. Nothing in that path may construct a
translator, load a glossary or start the overlay: a user pressing a
screenshot key wants the text they can see, and a backend that is not
installed should fall through to one that is rather than fail.
"""

from __future__ import annotations

# --- recogniser side ---
import sys
from argparse import Namespace
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import cli  # noqa: E402
from kizurium_translator.config import Config  # noqa: E402
from kizurium_translator.ocr import universal as uni  # noqa: E402
from kizurium_translator.ocr.universal import (  # noqa: E402
    reconstruct_plaintext,
    tesseract_lang_string,
    universal_ocr_text,
)


def test_tesseract_lang_string_uses_installed_not_hardcoded_en_ja():
    got = tesseract_lang_string(("eng", "rus", "deu", "jpn", "kor"))
    assert got.startswith("eng")
    assert "rus" in got and "deu" in got and "kor" in got
    assert "chi_sim" not in got


def test_reconstruct_reading_order():
    lines = [
        {"text": "second", "box": (10, 40, 80, 55), "line_height": 12},
        {"text": "first", "box": (10, 10, 80, 25), "line_height": 12},
]
    assert reconstruct_plaintext(lines).split() == ["first", "second"]


def test_universal_ocr_does_not_touch_translate_or_glossary(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(uni, "available_engines", lambda: ("rapid", "tesseract"))

    def fake_rapid(img):
        calls.append("rapid")
        return [{"text": "Hello world", "box": (0, 0, 40, 12), "line_height": 12, "conf": 0.9}]

    def fake_tess(img, langs=None):
        calls.append("tess")
        return [] # no extras — Rapid reading stands

    monkeypatch.setattr(uni, "_run_rapid", fake_rapid)
    monkeypatch.setattr(uni, "_run_tesseract", fake_tess)
    monkeypatch.setattr(
        uni,
        "_run_meiki",
        lambda img: (_ for _ in ()).throw(AssertionError("meiki")),
)

    import kizurium_translator.translation.service as svc

    monkeypatch.setattr(
        svc,
        "apply_game_glossary",
        lambda: (_ for _ in ()).throw(AssertionError("glossary")),
)
    monkeypatch.setattr(
        svc,
        "_ensure_game_glossary_applied",
        lambda: (_ for _ in ()).throw(AssertionError("glossary-ensure")),
)

    img = Image.new("RGB", (80, 40), "white")
    assert universal_ocr_text(img) == "Hello world"
    assert calls == ["rapid", "tess"]


def test_universal_falls_back_to_tesseract_when_rapid_empty(monkeypatch):
    monkeypatch.setattr(uni, "available_engines", lambda: ("rapid", "tesseract"))
    monkeypatch.setattr(uni, "_run_rapid", lambda img: [])
    monkeypatch.setattr(
        uni,
        "_run_tesseract",
        lambda img: [{"text": "Fallback", "box": (0, 0, 20, 10), "line_height": 10}],
)
    img = Image.new("RGB", (40, 20), "white")
    assert universal_ocr_text(img) == "Fallback"


def test_universal_fills_sparse_rapid_with_tesseract(monkeypatch):
    """Tall crop + 2 Rapid hits must still ask Tesseract for the missed body."""
    calls: list[str] = []
    monkeypatch.setattr(uni, "available_engines", lambda: ("rapid", "tesseract"))

    def fake_rapid(img):
        calls.append("rapid")
    return [
    {
            "text": "Edited 4 files, ran 2 commands +48 -32",
            "box": (10, 10, 400, 24),
            "line_height": 14,
            "conf": 0.9,
    },
    {
            "text": "EN: Hello world Settings",
            "box": (10, 30, 280, 44),
            "line_height": 14,
            "conf": 0.9,
    },
]

    def fake_tess(img, langs=None):
        calls.append("tess")
    return [
    {
    "text": " selector OCR to clipboard without translate.",
                "box": (10, 80, 460, 94),
            "line_height": 14,
                "conf": 0.85,
    },
    {
                "text": "If you selected a large region, every visible line should appear.",
                "box": (10, 100, 500, 114),
            "line_height": 14,
                "conf": 0.85,
    },
]

    monkeypatch.setattr(uni, "_run_rapid", fake_rapid)
    monkeypatch.setattr(uni, "_run_tesseract", fake_tess)
    monkeypatch.setattr(uni, "_run_meiki", lambda img: [])

    # Tall selection like a chat pane — coverage of 2 lines is thin.
    img = Image.new("RGB", (520, 240), "white")
    text = universal_ocr_text(img)
    assert "rapid" in calls and "tess" in calls
    assert "Edited 4 files" in text
    assert "" in text
    assert "every visible line" in text


def test_merge_prefers_russian_tess_over_rapid_soup():
    rapid = [
    {
            "text": "Edited 2 files, explored 2 files, ran 1 command +36 -18",
            "box": (40, 18, 476, 40),
            "line_height": 14,
            "conf": 0.98,
    },
    {
            "text": "a 37: B ep (nion current+previos, pd 4-8).",
            "box": (40, 141, 575, 163),
            "line_height": 14,
            "conf": 0.89,
    },
    {
            "text": "□2m ago",
            "box": (40, 463, 202, 489),
            "line_height": 14,
            "conf": 0.9,
    },
]
    tess = [
    {
            "text": "Коммичу фикс OCR-copy и беру фазу 36.",
            "box": (44, 64, 368, 79),
            "line_height": 14,
            "conf": 0.93,
    },
    {
            "text": "Фаза 37: выношу маску оверлея (union",
            "box": (43, 145, 353, 161),
            "line_height": 14,
            "conf": 0.94,
    },
    {
            "text": "pad 4-8).",
            "box": (500, 145, 570, 161),
            "line_height": 14,
            "conf": 0.96,
    },
    {
            "text": "Edited",
            "box": (44, 23, 89, 35),
            "line_height": 12,
            "conf": 0.96,
    },
    {
            "text": "口 9 ロ マ",
            "box": (48, 470, 137, 481),
            "line_height": 12,
            "conf": 0.8,
    },
    {
            "text": "content_revision).",
            "box": (43, 429, 177, 444),
            "line_height": 14,
            "conf": 0.9,
    },
]
    merged = uni._merge_line_sets(rapid, tess)
    text = uni.reconstruct_plaintext(merged)
    assert "Коммичу" in text
    assert "Фаза 37" in text and "pad 4-8" in text
    assert "content_revision" in text
    assert "Edited 2 files" in text
    assert "口" not in text
    assert "2m ago" not in text
    assert "a 37:" not in text
    assert "\nEdited\n" not in f"\n{text}\n"


def test_cli_ocr_copy_uses_universal(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(cli, "ask_region", lambda args, **kw: "0,0 10x10")
    monkeypatch.setattr(
        cli.capture,
        "capture_image",
        lambda geom: Image.new("RGB", (10, 10), "white"),
)
    monkeypatch.setattr(uni, "universal_ocr_text", lambda img: "plain OCR")
    monkeypatch.setattr(cli, "copy_to_clipboard", lambda text: seen.setdefault("text", text) or True)

    import kizurium_translator.live as live

    monkeypatch.setattr(live, "configure", lambda conf, paths=None: None)

    args = Namespace(region=None, select=False, output=False, geom=None)
    assert cli.cmd_ocr_copy(args, Config()) == 0
    assert seen["text"] == "plain OCR"


    # --- recogniser side ---



import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import selector  # noqa: E402


def test_selector_exposes_ocr_action():
    assert selector.SEL_OCR == "ocr"
    assert selector.SEL_OCR in selector.DEFAULT_ACTIONS


def test_main_flow_ocr_routes_to_clipboard_not_live(monkeypatch):
    seen: list[str] = []

    monkeypatch.setattr(
        "kizurium_translator.paths.emit_storage_banner",
        lambda *a, **k: None,
)
    monkeypatch.setattr(selector, "have_selector", lambda: True)
    monkeypatch.setattr(
        selector,
        "select_region",
        lambda: (selector.SEL_OCR, "10,20 300x100"),
)
    monkeypatch.setattr(cli, "cmd_ocr_copy", lambda a, c: seen.append(("ocr", a.region)) or 0)
    monkeypatch.setattr(
        cli,
        "cmd_live",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not live-translate")),
)
    monkeypatch.setattr(
        cli,
        "cmd_text",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not open translator")),
)

    args = Namespace(region=None, select=False, output=False, geom=None)
    assert cli.cmd_main_flow(args, Config()) == 0
    assert seen == [("ocr", "10,20 300x100")]


def test_ocr_copy_never_instantiates_translator(monkeypatch):
    monkeypatch.setattr(cli, "ask_region", lambda args, **kw: "0,0 10x10")
    monkeypatch.setattr(
        cli.capture,
        "capture_image",
        lambda geom: Image.new("RGB", (20, 20), "white"),
)
    monkeypatch.setattr(
        uni,
        "universal_ocr_text",
        lambda img: "こんにちは Hello Привет 한글",
)
    monkeypatch.setattr(cli, "copy_to_clipboard", lambda text: True)

    import kizurium_translator.live as live
    import kizurium_translator.translate as translate_mod

    monkeypatch.setattr(live, "configure", lambda conf, paths=None: None)
    monkeypatch.setattr(
        translate_mod,
        "Translator",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Translator")),
)

    args = Namespace(region=None, select=False, output=False, geom=None)
    assert cli.cmd_ocr_copy(args, Config()) == 0


def test_languages_follow_installed_backends_not_en_ja_only():
    got = tesseract_lang_string(("eng", "rus", "kor", "deu", "jpn", "chi_sim"))
    for code in ("eng", "rus", "kor", "deu", "jpn", "chi_sim"):
        assert code in got
