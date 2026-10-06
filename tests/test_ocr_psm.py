""": adaptive Tesseract PSM from crop geometry."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.ocr.psm import choose_tesseract_psm, psm_for_image  # noqa: E402


def test_word_like_crop_uses_psm_8():
    assert choose_tesseract_psm(32, 28) == "8"
    assert choose_tesseract_psm(64, 64) == "8"


def test_line_strip_uses_psm_7():
    assert choose_tesseract_psm(400, 24) == "7"
    assert choose_tesseract_psm(600, 40) == "7"
    assert choose_tesseract_psm(500, 50) == "7" # aspect >= 5, h <= 56


def test_block_uses_psm_6():
    assert choose_tesseract_psm(400, 200) == "6"
    assert choose_tesseract_psm(800, 400) == "6"


def test_large_sparse_canvas_uses_psm_11():
    assert choose_tesseract_psm(1280, 720) == "11"
    assert choose_tesseract_psm(1920, 1080) == "11"


def test_psm_for_image_matches_size():
    img = Image.new("RGB", (400, 24), "white")
    assert psm_for_image(img) == "7"


def test_ocr_with_none_picks_adaptive(monkeypatch):
    from kizurium_translator.ocr import engine

    seen: dict = {}

    def fake_image_to_data(img, lang, output_type, config):
        seen["config"] = config
    return {
            "text": [""],
            "left": [0],
            "top": [0],
            "width": [1],
            "height": [1],
            "conf": ["-1"],
            "block_num": [0],
            "par_num": [0],
            "line_num": [0],
            "word_num": [0],
            "level": [5],
}

    monkeypatch.setattr(engine.pytesseract, "image_to_data", fake_image_to_data)
    monkeypatch.setattr(engine, "ocr_lines", lambda data: [])
    img = Image.new("RGB", (40, 36), "white")
    engine.ocr_with(img, "eng", psm=None)
    assert "--psm 8" in seen["config"]
