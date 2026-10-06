"""Измерение написания: кегль, наклон, цвет и разрядка глифов.

Реэкспорт явный: звёздочка не достаёт имена с подчёркиванием, а тесты
пользуются ими. Список строится разбором модулей, а не пишется руками -
рукописный список однажды разошёлся с кодом и уронил импорт соседнего
пакета вместо своего.
"""

from __future__ import annotations

from .metrics import (  # noqa: F401
    COLOR_MIN_HUE_GAP,
    COLOR_MIN_SAT,
    NEUTRAL_HUE,
    RE_JPN,
    ColorSpan,
    GlyphMetrics,
    _dominant_color,
    _hue_gap,
    _ink_mask,
    _merge_vn_band_cluster,
    _metrics_crop,
    clear_measurement_cache,
    color_hue,
    dedupe_near_ui_lines,
    detect_overlay_typeface,
    estimate_italic_slant,
    ink_is_light,
    ink_line_count,
    is_ink_band_garbage,
    is_latin_label,
    looks_like_game_ui,
    looks_like_ui_prompt,
    measure_glyph_metrics,
    measure_slant_band,
    measure_word_colors,
    merge_subtitle_cluster,
    reread_core_ink,
    sample_outline_color,
    ui_text_fingerprint,
    unglue_english,
    vn_lines_should_merge,
)

__all__ = [
    "COLOR_MIN_HUE_GAP",
    "COLOR_MIN_SAT",
    "ColorSpan",
    "GlyphMetrics",
    "NEUTRAL_HUE",
    "RE_JPN",
    "_dominant_color",
    "_hue_gap",
    "_ink_mask",
    "_merge_vn_band_cluster",
    "_metrics_crop",
    "clear_measurement_cache",
    "color_hue",
    "dedupe_near_ui_lines",
    "detect_overlay_typeface",
    "estimate_italic_slant",
    "ink_is_light",
    "ink_line_count",
    "is_ink_band_garbage",
    "is_latin_label",
    "looks_like_game_ui",
    "looks_like_ui_prompt",
    "measure_glyph_metrics",
    "measure_slant_band",
    "measure_word_colors",
    "merge_subtitle_cluster",
    "reread_core_ink",
    "sample_outline_color",
    "ui_text_fingerprint",
    "unglue_english",
    "vn_lines_should_merge",
]
