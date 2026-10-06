"""What goes on the clipboard: reading order, indents, and paragraphs.

Reconstructing text from boxes is where a recogniser result turns into
something a person can paste. The order of the lines, the indent of a
sub-line, the blank line between paragraphs and the unicode that
survived OCR all have to come out right, because whatever is wrong here
is invisible in a screenshot and obvious in a terminal.
"""

from __future__ import annotations

# --- reconstruction ---
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.ocr.clipboard import (  # noqa: E402
    TAB_STOP_PX,
    clipboard_bytes,
    logical_indent,
    reconstruct_clipboard,
)
from kizurium_translator.ocr.postprocess import postprocess_ocr_lines  # noqa: E402


def test_logical_indent_uses_tab_stops_not_spaces():
    assert logical_indent(0) == ""
    assert logical_indent(32) == "\t"
    assert logical_indent(64, origin_x=0, tab_stop=32) == "\t\t"
    assert " " not in logical_indent(96)


def test_reconstruct_preserves_unicode_and_punctuation():
    paras = [
    {
            "text": "Привет, мир! «Да» — café ☕",
            "box": (0, 0, 200, 16),
        "line_height": 16,
}
]
    got = reconstruct_clipboard(paras)
    assert "Привет, мир!" in got
    assert "«Да»" in got
    assert "café" in got
    assert "☕" in got


def test_reconstruct_indent_and_paragraph_gap():
    paras = [
        {"text": "Title", "box": (0, 0, 40, 14), "line_height": 14},
        {"text": "indented", "box": (64, 20, 120, 34), "line_height": 14},
        {"text": "far", "box": (0, 120, 40, 134), "line_height": 14},
]
    got = reconstruct_clipboard(paras)
    assert got.startswith("Title")
    assert "\t\tindented" in got or "\tindented" in got
    # Large vertical gap → blank line before "far"; nearby lines stay single \n
    assert "\n\nfar" in got
    assert "Title\n" in got


def test_menu_lines_use_single_newlines_not_blank_each():
    paras = [
        {"text": "Продолжить", "box": (16, 16, 200, 40), "line_height": 24},
        {"text": "Настройки", "box": (16, 52, 200, 76), "line_height": 24},
        {"text": "Выход", "box": (16, 88, 120, 112), "line_height": 24},
]
    got = reconstruct_clipboard(paras)
    assert got == "Продолжить\nНастройки\nВыход\n"
    assert "\n\n" not in got


def test_clipboard_bytes_utf8_and_large():
    text = "漢字\n" + ("строка\n" * 5000)
    data = clipboard_bytes(text)
    assert isinstance(data, bytes)
    assert data.decode("utf-8") == text
    assert len(data) > 10_000


def test_empty_lines_between_columns_paragraphs():
    paras = [
        {"text": "Left A", "box": (0, 0, 40, 12), "line_height": 12},
        {"text": "Left B", "box": (0, 16, 40, 28), "line_height": 12},
        {"text": "Right", "box": (200, 0, 240, 12), "line_height": 12},
]
    got = reconstruct_clipboard(paras)
    assert "Left A" in got and "Right" in got
    assert "\n" in got


def test_postprocess_chain_still_clipboard_shaped():
    lines = [
        {"text": "Hello!", "box": (0, 0, 40, 12), "line_height": 12},
        {"text": "世界", "box": (0, 40, 40, 52), "line_height": 12},
]
    text = postprocess_ocr_lines(lines)
    assert "Hello!" in text
    assert "世界" in text


def test_tab_stop_constant():
    assert TAB_STOP_PX == 32
