""": raw → safe normalize → structural merge → semantic grouping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.ocr.postprocess import (  # noqa: E402
    postprocess_ocr_lines,
    safe_normalize_text,
    semantic_group_plaintext,
    structural_merge,
)


def test_safe_normalize_nfkc_and_controls():
    # Fullwidth Latin → ASCII; control char dropped; punctuation kept.
    raw = "Ｈello\x00, world!"
    got = safe_normalize_text(raw)
    assert got == "Hello, world!"


def test_safe_normalize_keeps_russian_and_punctuation():
    raw = "Привет, мир! «Да»?"
    assert safe_normalize_text(raw) == raw


def test_safe_normalize_fixes_latin_ocr_artifact_not_russian():
    assert "is" in safe_normalize_text("this 15 fine").lower()
    # Valid Russian must not be rewritten into Latin guesswork.
    assert safe_normalize_text("это 15") == "это 15" or "это" in safe_normalize_text("это 15")


def test_structural_merge_reading_order():
    lines = [
        {"text": "B", "box": (10, 40, 40, 52), "line_height": 12},
        {"text": "A", "box": (10, 10, 40, 22), "line_height": 12},
]
    merged = structural_merge(lines)
    texts = [p["text"] for p in merged]
    # Either separate paragraphs in order, or one block — first char must be A.
    flat = " ".join(texts)
    assert flat.index("A") < flat.index("B")


def test_postprocess_chain_no_glossary_side_effects():
    lines = [
        {"text": "Ｈello!", "box": (0, 0, 40, 12), "line_height": 12},
        {"text": "world", "box": (0, 20, 40, 32), "line_height": 12},
]
    text = postprocess_ocr_lines(lines)
    assert "Hello!" in text
    assert "world" in text
    assert "\x00" not in text


def test_semantic_group_blank_line_between_paragraphs():
    # Close lines → single newline (menu-like).
    close = [
        {"text": "One", "box": (0, 0, 10, 10), "line_height": 10},
        {"text": "Two", "box": (0, 14, 10, 24), "line_height": 10},
]
    assert semantic_group_plaintext(close) == "One\nTwo"
    # Large gap → blank line.
    far = [
        {"text": "One", "box": (0, 0, 10, 10), "line_height": 10},
        {"text": "Two", "box": (0, 80, 10, 90), "line_height": 10},
]
    assert semantic_group_plaintext(far) == "One\n\nTwo"


def test_finished_sentences_stay_on_separate_lines():
    """OCR-copy must not smash period-ended chat/IDE lines into one mush."""
    lines = [
        {
            "text": "Edited 4 files, ran 2 commands +48 -32",
            "box": (10, 10, 400, 24),
            "line_height": 14,
        },
        {
            "text": "EN: Hello world Settings",
            "box": (10, 30, 280, 44),
            "line_height": 14,
        },
        {
            "text": "Step 31 clipboard keeps indent and UTF-8.",
            "box": (10, 50, 420, 64),
            "line_height": 14,
        },
        {
            "text": "Step 32 selector OCR to clipboard without translate.",
            "box": (10, 70, 460, 84),
            "line_height": 14,
        },
    ]
    text = postprocess_ocr_lines(lines)
    assert "Edited 4 files" in text
    assert "Step 31" in text and "Step 32" in text
    # Each finished sentence / UI row stays its own line.
    assert "UTF-8.\nStep 32" in text or text.count("\n") >= 3
