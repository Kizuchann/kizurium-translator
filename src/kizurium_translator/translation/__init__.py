"""Перевод: бэкенд, глоссарий, отсев плохих строк.

Реэкспорт явный: звёздочка не достаёт имена с подчёркиванием, а тесты
пользуются ими. Список строится разбором модулей, а не пишется руками -
рукописный список однажды разошёлся с кодом и уронил импорт соседнего
пакета вместо своего.
"""

from __future__ import annotations

from .service import (  # noqa: F401
    _GAME_GLOSSARY_APPLIED,
    GAME_GLOSSARY,
    GLOSSARY,
    TRANSLATION_DISABLED,
    _ensure_game_glossary_applied,
    _lookup,
    _numeric_guard,
    apply_game_glossary,
    drop_bad_ocr_boxes,
    drop_furigana_lines,
    filter_plausible_lines,
    finish_mixed_frame,
    glossary_translation,
    is_furigana_reading,
    is_garbage_japanese,
    is_speaker_name,
    is_translation_error,
    japanese_ocr_quality,
    meiki_ocr_lines,
    parse_marked_translation,
    refine_japanese_blocks,
    require_ocr,
    same_line,
)

__all__ = [
    "GAME_GLOSSARY",
    "GLOSSARY",
    "TRANSLATION_DISABLED",
    "_GAME_GLOSSARY_APPLIED",
    "_ensure_game_glossary_applied",
    "_lookup",
    "_numeric_guard",
    "apply_game_glossary",
    "drop_bad_ocr_boxes",
    "drop_furigana_lines",
    "filter_plausible_lines",
    "finish_mixed_frame",
    "glossary_translation",
    "is_furigana_reading",
    "is_garbage_japanese",
    "is_speaker_name",
    "is_translation_error",
    "japanese_ocr_quality",
    "meiki_ocr_lines",
    "parse_marked_translation",
    "refine_japanese_blocks",
    "require_ocr",
    "same_line",
]
