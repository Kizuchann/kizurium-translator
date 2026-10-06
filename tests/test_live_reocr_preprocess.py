"""Live reocr uses the adaptive PSM and the tiny upscale helpers."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_reocr_tesseract_none_psm_uses_adaptive(monkeypatch):
    from kizurium_translator.live import reconcile

    seen: dict = {}

    monkeypatch.setattr(reconcile, "pytesseract", type("T", (), {})())
    # Provide modules used inside
    class FakeTess:
        @staticmethod
        def image_to_string(img, lang, config):
            seen["config"] = config
            return "ok"

    monkeypatch.setattr(reconcile, "pytesseract", FakeTess)
    monkeypatch.setattr(reconcile, "tesseract_langs_for_script", lambda s: "eng")
    monkeypatch.setattr(
        reconcile,
        "_crop_box",
        lambda img, box, pad=0: Image.new("RGB", (40, 36), "white"),
    )

    img = Image.new("RGB", (200, 200), "white")
    assert reconcile.reocr_crop_tesseract(img, (0, 0, 40, 36), psm=None) == "ok"
    assert "--psm 8" in seen["config"]  # 40×36 → word-like


def test_reocr_rapid_runs_prepare_for_ocr(monkeypatch):
    from kizurium_translator.live import reconcile
    from kizurium_translator.ocr import preprocess

    calls: list[int] = []

    real_prepare = preprocess.prepare_for_ocr

    def wrapped(img, **kw):
        out, scale = real_prepare(img, **kw)
        calls.append(scale)
        return out, scale

    monkeypatch.setattr(preprocess, "prepare_for_ocr", wrapped)
    monkeypatch.setattr(
        reconcile,
        "_crop_box",
        lambda img, box, pad=0: Image.new("RGB", (80, 10), "white"),
    )
    monkeypatch.setattr(
        reconcile,
        "rapid_ocr_lines",
        lambda img, max_side=0: [
            {"text": "Hi", "box": (0, 0, 20, 10), "conf": 90.0, "line_height": 10}
        ],
    )
    img = Image.new("RGB", (100, 100), "white")
    text, conf = reconcile.reocr_crop_rapid(img, (0, 0, 80, 10))
    assert text == "Hi"
    assert calls  # prepare_for_ocr was invoked
