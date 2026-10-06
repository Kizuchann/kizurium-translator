"""Распознавание: движки, выбор строк, качество чтения.

Реэкспорт явный: звёздочка не достаёт имена с подчёркиванием, а тесты
пользуются ими. Список строится разбором модулей, а не пишется руками -
рукописный список однажды разошёлся с кодом и уронил импорт соседнего
пакета вместо своего.
"""

from __future__ import annotations

from .engine import (  # noqa: F401
    _OCR_LOCK,
    CORE_REREAD_BELOW,
    DET_SIDE_LEN,
    MEIKI_INIT_FAILED,
    MEIKI_OCR,
    OCR_THREADS,
    RAPID_INIT_FAILED,
    RAPID_OCR,
    _rapid_rows,
    annotate_lines,
    collect_subtitle_blocks,
    english_ocr_quality,
    extract_dialogue_choice_lines,
    is_garbage_ocr,
    is_overlay_echo_ocr,
    japanese_blocks,
    live_dialogue_cards,
    looks_like_dialogue_choice,
    looks_like_gameplay_hud_line,
    looks_like_ocr_mojibake_of_russian,
    looks_like_subtitle_continue,
    normalize_japanese_text,
    ocr_lines,
    ocr_with,
    skip_source,
    stitch_rows_by_baseline,
    stitch_subtitle_fragments,
    subtitle_ocr_lines,
    tess_fill_sparse_ui,
    ui_line_fingerprints,
)

__all__ = [
    "CORE_REREAD_BELOW",
    "DET_SIDE_LEN",
    "MEIKI_INIT_FAILED",
    "MEIKI_OCR",
    "OCR_THREADS",
    "RAPID_INIT_FAILED",
    "RAPID_OCR",
    "_OCR_LOCK",
    "_rapid_rows",
    "annotate_lines",
    "collect_subtitle_blocks",
    "english_ocr_quality",
    "extract_dialogue_choice_lines",
    "is_garbage_ocr",
    "is_overlay_echo_ocr",
    "japanese_blocks",
    "live_dialogue_cards",
    "looks_like_dialogue_choice",
    "looks_like_gameplay_hud_line",
    "looks_like_ocr_mojibake_of_russian",
    "looks_like_subtitle_continue",
    "normalize_japanese_text",
    "ocr_lines",
    "ocr_with",
    "skip_source",
    "stitch_rows_by_baseline",
    "stitch_subtitle_fragments",
    "subtitle_ocr_lines",
    "tess_fill_sparse_ui",
    "ui_line_fingerprints",
]
