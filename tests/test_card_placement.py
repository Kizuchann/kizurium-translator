"""A card is placed where its text is, and a slanted card is sized as drawn.

Both of these were wrong in the same way: the code reasoned about a card's
bounding box when it should have reasoned about the card. A card on a slanted
line has a bounding box several times its own height, so two cards on
neighbouring lines of the same row look like they collide when they are a
clear gap apart - and the collision-avoidance that then runs moves the card off
its own text entirely, which is worse than the overlap it was preventing.

The cases below are built from a result screen's numbers on purpose: a row of
judgement labels lettered at 13 degrees, each on its own line, where the
bounding boxes overlap by most of their height and the letters do not touch.
"""

from __future__ import annotations

from kizurium_translator.live import (
    _fit_between_neighbours,
    _quads_overlap,
    card_quad,
)


def card(x, y, w, h, angle=0.0):
    return {"x": x, "y": y, "src_w": w, "src_h": h, "angle": angle}


# ---------------------------------------------------------------- the shape


def test_a_slanted_card_is_not_its_bounding_box():
    """At 13 degrees a 30-pixel band is 100 pixels tall in its box."""
    quad = card_quad(200.0, 200.0, 180.0, 30.0, 13.0)
    top = min(p[1] for p in quad)
    bottom = max(p[1] for p in quad)
    assert bottom - top > 60, "the quad should be as tall as the drawn card"


def test_neighbouring_slanted_cards_do_not_collide():
    """Two labels on consecutive lines of a slanted row, a real gap apart.

    Their bounding boxes overlap through most of their height. The cards
    themselves do not touch, and treating that as a collision is what threw a
    card four hundred pixels up the screen.
    """
    upper = card_quad(150.0, 760.0, 180.0, 30.0, 13.0)
    lower = card_quad(120.0, 800.0, 130.0, 31.0, 13.5)
    # The bounding boxes, which is what the old test saw:
    assert min(p[1] for p in upper) < max(p[1] for p in lower) - 1
    upper_box = (0, 760 - 50, 180, 100)
    lower_box = (0, 800 - 45, 130, 90)
    assert (
        min(upper_box[1] + upper_box[3], lower_box[1] + lower_box[3])
        > max(upper_box[1], lower_box[1])
    ), "the boxes really do overlap"
    assert not _quads_overlap(upper, lower)


def test_two_cards_in_the_same_place_do_collide():
    a = card_quad(100.0, 100.0, 120.0, 40.0, 0.0)
    b = card_quad(110.0, 105.0, 120.0, 40.0, 0.0)
    assert _quads_overlap(a, b)


def test_a_rotation_of_the_same_card_still_overlaps_itself():
    """A quad test must not be so strict that a card misses itself."""
    q = card_quad(300.0, 300.0, 160.0, 44.0, 17.0)
    assert _quads_overlap(q, list(q))


def test_cards_differing_only_in_angle_are_judged_by_both_shapes():
    """A slanted card crossing a flat one overlaps when the shapes really meet."""
    slanted = card_quad(200.0, 200.0, 200.0, 40.0, 20.0)
    flat = card_quad(240.0, 230.0, 120.0, 60.0, 0.0)
    assert _quads_overlap(slanted, flat)
    far = card_quad(240.0, 400.0, 120.0, 60.0, 0.0)
    assert not _quads_overlap(slanted, far)


# ------------------------------------------------------------ the placement


def test_a_card_is_not_moved_off_its_own_text():
    """The defect: PASS moved from its line to the top of the screen.

    Its neighbour's bounding box sat above it, so the avoidance pushed it clear
    of that box - four hundred pixels up, over nothing, with the original PASS
    showing through underneath.
    """
    # A big slanted neighbour already drawn, whose bounding box hangs down over
    # where this card belongs.
    blocker = card_quad(146.0 + 235.0, 584.0 + 119.0, 471.0, 238.0, 13.0)
    placed = [blocker]
    blk = card(50, 788, 133, 31, 13.5)
    x, y, w, h = _fit_between_neighbours(50, 788, 133, 31, blk, placed, 1920, 1080)
    assert abs(y - 788) <= 40, f"card wandered to y={y}"
    assert abs(x - 50) <= 40, f"card wandered to x={x}"


def test_a_card_still_moves_away_when_it_can_do_so_without_losing_its_text():
    """The avoidance is still wanted - it just may not cost the element its text."""
    blocker = card_quad(300.0, 300.0, 200.0, 60.0, 0.0)
    placed = [blocker]
    blk = card(20, 290, 100, 40, 0.0)
    x, y, w, h = _fit_between_neighbours(20, 290, 100, 40, blk, placed, 1920, 1080)
    assert not _quads_overlap(
        card_quad(x + w * 0.5, y + h * 0.5, w, h, 0.0),
        card_quad(300.0, 300.0, 200.0, 60.0, 0.0),
    ), "the blocker is still covered"


def test_nothing_to_avoid_leaves_the_card_exactly_where_it_is():
    blk = card(50, 788, 133, 31, 13.5)
    assert _fit_between_neighbours(50, 788, 133, 31, blk, [], 1920, 1080) == (
        50,
        788,
        133,
        31,
    )


def test_a_moved_card_ends_inside_the_region_and_still_on_its_own_text():
    """Two things the move may not cost: the screen edge, and the element itself."""
    blocker = card_quad(300.0, 300.0, 200.0, 60.0, 0.0)
    blk = card(20, 320, 100, 40, 0.0)
    x, y, w, h = _fit_between_neighbours(20, 320, 100, 40, blk, [blocker], 1920, 1080)
    assert 0 <= x and 0 <= y and x + w <= 1920 and y + h <= 1080
    own = card_quad(20 + 50, 320 + 20, 100, 40, 0.0)
    assert _quads_overlap(card_quad(x + w * 0.5, y + h * 0.5, w, h, 0.0), own)
