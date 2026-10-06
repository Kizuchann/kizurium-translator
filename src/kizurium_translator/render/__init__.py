"""Рисование: карточка, раскладка текста, окно оверлея.

Реэкспорт явный: звёздочка не достаёт имена с подчёркиванием, а тесты
пользуются ими. Список строится разбором модулей, а не пишется руками -
рукописный список однажды разошёлся с кодом и уронил импорт соседнего
пакета вместо своего.
"""

from __future__ import annotations

from .cards import (  # noqa: F401
    CARD_BG_DEFAULT,
    CARD_FG_DEFAULT,
    CARD_RADIUS,
    CARD_WIDTH_MAX_RATIO,
    COLOR_SPAN_MIN_SHARE,
    MIN_X_SCALE,
    card_quad,
    click_through,
    fit_layout,
    make_block,
    pango_layout,
)
from .overlay import (  # noqa: F401
    _fill_by_color_spans,
    _fit_between_neighbours,
    _free_width_near,
    _quad_far,
    _quads_overlap,
    _snap_to_glyph_edge,
    draw_blocks,
)

__all__ = [
    "CARD_BG_DEFAULT",
    "CARD_FG_DEFAULT",
    "CARD_RADIUS",
    "CARD_WIDTH_MAX_RATIO",
    "COLOR_SPAN_MIN_SHARE",
    "MIN_X_SCALE",
    "_fill_by_color_spans",
    "_fit_between_neighbours",
    "_free_width_near",
    "_quad_far",
    "_quads_overlap",
    "_snap_to_glyph_edge",
    "card_quad",
    "click_through",
    "draw_blocks",
    "fit_layout",
    "make_block",
    "pango_layout",
]
