"""A gutter is a column, and a column is text that stands opposite other text.

The engine cuts a line of text where an empty vertical strip crosses it, because
on a page in two or three columns that strip is where one column ends. On a
screen that is not a page - a results board, a menu, anything laid out in whatever
place each thing wanted - an empty strip is just where nothing happened to be,
and cutting there is how "New Best!!" became "New" and "Best!!", two cards and
two translations for one line.
"""

from __future__ import annotations

from kizurium_translator.live import _flanked_by_columns


def line(x1, y1, x2, y2):
    return {"box": (x1, y1, x2, y2), "text": "x", "line_height": y2 - y1}


# -------------------------------------------------------------- it is a column


def test_a_two_column_page_has_a_gutter():
    """Rows of text on the left, rows at the same heights on the right."""
    lines = []
    for top in range(100, 900, 60):
        lines.append(line(40, top, 420, top + 24))
        lines.append(line(1100, top, 1500, top + 24))
    assert _flanked_by_columns(lines, (600, 1000))


def test_the_heights_have_to_line_up():
    """Text on both sides, but never at the same height, is not two columns."""
    lines = [line(40, 100 + i * 20, 420, 116 + i * 20) for i in range(20)]
    lines += [line(1100, 900 + i * 20, 1500, 916 + i * 20) for i in range(20)]
    assert not _flanked_by_columns(lines, (600, 1000))


def test_one_tall_box_each_side_is_not_a_column():
    """A single pair of blocks that happen to share a height range."""
    assert not _flanked_by_columns(
        [line(40, 100, 420, 900), line(1100, 100, 1500, 900)], (600, 1000)
    )


# ----------------------------------------------------------- it is not a column


def test_an_empty_strip_between_scattered_boxes_is_not_a_column():
    """The result screen: a gap where nothing was, with text at other heights."""
    lines = [
        line(40, 600, 420, 640),
        line(40, 700, 300, 730),
        line(150, 850, 420, 880),
        line(1200, 960, 1500, 1000),
    ]
    assert not _flanked_by_columns(lines, (600, 1100))


def test_two_words_of_one_line_do_not_make_a_gutter():
    """The case this exists for: one line, split either side of a space.

    Everything is at one height, so the strip is flanked - by the same line. What
    it is not flanked by is a *column*, because there is nothing at another height
    on the far side at all.
    """
    assert not _flanked_by_columns(
        [line(1306, 507, 1482, 721), line(1490, 507, 1650, 721)], (1483, 1489)
    )


def test_nothing_on_one_side_is_not_a_column():
    assert not _flanked_by_columns([line(40, 100, 420, 140)], (600, 1000))


def test_empty_input_is_not_a_column():
    assert not _flanked_by_columns([], (600, 1000))
