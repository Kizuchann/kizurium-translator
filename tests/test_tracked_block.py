"""The block is the source of truth about itself.

Everything the overlay draws for one element lives in one TrackedBlock: what it
says, where it is, what language it is, how sure the engine was, which engine
read it, what it was translated to, when it was last seen and how it is
rendered. These tests pin that, and the two matching rules that were wrong -
size compared as width times the other box's height, and one previous block
claiming several new lines.
"""

from __future__ import annotations

import dataclasses
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402
from kizurium_translator.live import TrackedBlock, track_blocks  # noqa: E402


def blk(text: str, box=(10, 20, 200, 50), conf: float = 95.0, index: int = 1) -> TrackedBlock:
    return TrackedBlock(
        id=index,
        box=box,
        text=text,
        norm=live.normalize_for_compare(text),
        script=live.block_script(text),
        lang=live.block_lang(text),
        conf=conf,
        engine="rapidocr",
    )


def line(text: str, box=(10, 20, 200, 50), conf: float = 95.0) -> dict:
    return live.annotate_lines(
        [{"text": text, "box": box, "conf": conf}], "rapid"
    )[0]


class TestTheBlockHoldsEverything:
    @pytest.mark.parametrize(
        "name",
        [
            "id",
            "box",
            "text",
            "norm",
            "script",
            "lang",
            "conf",
            "engine",
            "translation",
            "last_seen",
            "params",
        ],
    )
    def test_the_field_is_there(self, name):
        assert name in {f.name for f in dataclasses.fields(TrackedBlock)}

    def test_a_fresh_block_comes_ready_made_from_an_ocr_line(self):
        block = live.make_block_from_line(line("こんにちは", conf=71.0), 4, 123.0)
        assert block.id == 4
        assert block.text == "こんにちは"
        assert block.norm == live.normalize_for_compare("こんにちは")
        assert block.lang == "ja"
        assert block.script == live.SCRIPT_JA
        assert block.conf == 71.0
        assert block.engine == "rapid"
        assert block.last_seen == 123.0
        assert block.translation == ""
        assert block.params == {}

    def test_the_language_survives_into_the_block(self):
        """The OCR pass decided it from the pixels; re-deriving it can disagree."""
        raw = {"text": "強化", "box": (0, 0, 10, 10), "conf": 50.0, "lang": "ja"}
        assert live.make_block_from_line(raw, 1, 0.0).lang == "ja"

    def test_a_block_with_no_language_still_gets_one(self):
        assert live.make_block_from_line({"text": "Live", "box": (0, 0, 1, 1)}, 1, 0.0).lang == "en"

    def test_the_fingerprint_separates_two_languages_of_one_text(self):
        """Same characters, different reading: a different element to the tracker."""
        a = blk("Live", index=1)
        b = dataclasses.replace(a, lang="ja")
        assert a.fingerprint != b.fingerprint

    def test_the_fingerprint_ignores_insignificant_spacing(self):
        assert blk("Start  now").fingerprint == blk("Start now").fingerprint

    def test_the_translation_and_render_params_live_on_the_block(self):
        block = blk("Live")
        block.translation = "Живой"
        block.params = {"size": 20, "wrap": True}
        assert block.translation == "Живой"
        assert block.params["size"] == 20


class TestSizeIsComparedAsArea:
    def test_the_same_shape_at_a_new_position_is_the_same_size(self):
        """This is the case the old arithmetic got wrong.

        It multiplied one box's width by the other box's height, so two boxes of
        exactly the same size, one row further down the screen, compared as
        100x50 against 100x50 offset by 10px - a ratio near 0.1. Every
        element that moved by a few pixels was therefore told it had changed
        size, and a real size change was compared just as badly.
        """
        a = (100, 200, 300, 260)
        b = (100, 210, 300, 270)
        assert live.box_size_ratio(a, b) == 1.0

    def test_a_genuinely_different_area_scores_lower(self):
        assert live.box_size_ratio((0, 0, 100, 100), (0, 0, 200, 200)) == pytest.approx(0.25)

    def test_area_alone_cannot_see_shape(self):
        """A known limit, stated so it is not mistaken for an accident.

        A 200x50 banner and a 100x100 square have the same area, so the size
        term says nothing about them. Overlap does most of the work - they share
        a third of the larger box, which is at the edge of what counts as a
        match - so a shape change that keeps the area and the corner is the one
        thing these two terms cannot see. The language tiebreak is the reason
        that is survivable rather than fatal: an element whose shape changed and
        whose language changed is a new element anyway.
        """
        wide = (0, 0, 200, 50)
        square = (0, 0, 100, 100)
        assert live.box_size_ratio(wide, square) == 1.0
        assert live.box_iou(wide, square) == pytest.approx(1 / 3)

    def test_the_ratio_is_the_smaller_area_over_the_larger(self):
        a = (0, 0, 100, 100)  # 10_000
        b = (0, 0, 50, 50)  # 2_500
        assert live.box_size_ratio(a, b) == pytest.approx(0.25)
        assert live.box_size_ratio(b, a) == pytest.approx(0.25)

    def test_equal_boxes_are_one(self):
        a = (10, 20, 200, 50)
        assert live.box_size_ratio(a, a) == 1.0

    def test_a_degenerate_box_does_not_divide_by_zero(self):
        assert live.box_size_ratio((0, 0, 0, 0), (0, 0, 10, 10)) == pytest.approx(0.01)

    def test_the_ratio_is_between_zero_and_one(self):
        for a, b in (
            ((0, 0, 3, 3), (0, 0, 900, 4)),
            ((0, 0, 10, 900), (5, 5, 6, 6)),
        ):
            assert 0.0 <= live.box_size_ratio(a, b) <= 1.0


class TestMatchingUsesMoreThanOverlap:
    def test_the_score_combines_overlap_and_centre_distance(self):
        src = inspect.getsource(track_blocks)
        assert "block_match_score(" in src
        assert "same_visual_block(" in src

    def test_a_short_line_inside_a_long_one_is_not_the_same_element(self):
        """IoU alone matched a label to the paragraph it sat inside."""
        paragraph = (0, 0, 600, 100)
        label = (10, 5, 100, 25)
        assert live.box_iou(paragraph, label) < 0.2
        assert not live.same_visual_block(paragraph, label)

    def test_the_same_element_in_the_same_place_matches(self):
        a = (100, 200, 400, 240)
        b = (102, 202, 398, 241)
        assert live.same_visual_block(a, b)

    def test_an_element_that_moved_far_does_not_match(self):
        assert not live.same_visual_block((0, 0, 200, 30), (900, 800, 1100, 830))

    def test_the_centre_check_is_part_of_it(self):
        src = inspect.getsource(live.same_visual_block)
        assert "_center_distance_px" in src
        assert "MATCH_CENTER_HEIGHT" in src

    def test_the_language_only_breaks_ties(self):
        """A weighted term, not a filter: a box can be re-translated into another language."""
        assert live.MATCH_LANG_BONUS > 0
        src = inspect.getsource(track_blocks)
        assert "block_match_score(" in src
        # Language is scored inside the composite, not used to skip candidates.
        assert "if lang and prev.lang and lang == prev.lang:\n                continue" not in src
        score_same = live.block_match_score(
            (0, 0, 40, 20), (0, 0, 40, 20),
            prev_lang="en", lang="en", prev_text="Hi", text="Hi",
        )
        score_diff = live.block_match_score(
            (0, 0, 40, 20), (0, 0, 40, 20),
            prev_lang="en", lang="ja", prev_text="Hi", text="你好",
        )
        assert score_same > score_diff


class TestOneToOneMatching:
    def test_one_previous_block_cannot_claim_two_new_lines(self):
        """A reflowing paragraph used to keep the id, and lose the rest."""
        previous = [blk("A long line of dialogue", (0, 0, 600, 60), index=1)]
        lines = [line("A long line of", (0, 0, 600, 30)), line("dialogue", (0, 30, 600, 60))]
        current, pairs, _ = track_blocks(previous, lines, 1.0, 2)
        assert len(pairs) == 1, pairs
        assert [b.id for b in current] == [1, 2]

    def test_a_new_line_cannot_claim_two_previous_blocks(self):
        previous = [blk("one", (0, 0, 200, 20), index=1), blk("two", (0, 20, 200, 40), index=2)]
        lines = [line("one two", (0, 0, 200, 40))]
        current, pairs, _ = track_blocks(previous, lines, 1.0, 3)
        assert len(pairs) == 1
        assert len(current) == 1

    def test_ids_survive_a_matched_frame(self):
        previous = [blk("Live", (0, 0, 200, 20), index=7)]
        current, pairs, _ = track_blocks(previous, [line("Live", (1, 1, 201, 21))], 1.0, 8)
        assert current[0].id == 7
        assert len(pairs) == 1

    def test_an_unchanged_frame_keeps_every_id(self):
        previous = [blk("A", (0, 0, 200, 20), index=1), blk("B", (0, 30, 200, 50), index=2)]
        lines = [line("A", (0, 0, 200, 20)), line("B", (0, 30, 200, 50))]
        current, pairs, next_id = track_blocks(previous, lines, 1.0, 3)
        assert [b.id for b in current] == [1, 2]
        assert len(pairs) == 2
        assert next_id == 3

    def test_a_block_that_disappeared_is_simply_gone(self):
        previous = [blk("A", (0, 0, 200, 20), index=1), blk("B", (0, 30, 200, 50), index=2)]
        current, pairs, next_id = track_blocks(previous, [line("A", (0, 0, 200, 20))], 1.0, 3)
        assert [b.id for b in current] == [1]
        assert next_id == 3

    def test_an_empty_frame_keeps_the_counter(self):
        previous = [blk("A", (0, 0, 200, 20), index=4)]
        current, pairs, next_id = track_blocks(previous, [], 1.0, 5)
        assert current == [] and pairs == []
        assert next_id == 5

    def test_no_previous_block_means_everything_is_new(self):
        current, pairs, next_id = track_blocks([], [line("A", (0, 0, 9, 9))], 1.0, 1)
        assert [b.id for b in current] == [1]
        assert pairs == []
        assert next_id == 2

    def test_matching_is_not_greedy_in_reading_order(self):
        """The best pair wins first, then the leftovers."""
        previous = [
            blk("left", (0, 0, 100, 20), index=1),
            blk("right", (100, 0, 200, 20), index=2),
        ]
        # The first new line overlaps the second block more than the first.
        lines = [line("right", (98, 0, 198, 20)), line("left", (0, 0, 100, 20))]
        current, _pairs, _ = track_blocks(previous, lines, 1.0, 3)
        by_text = {b.text: b.id for b in current}
        assert by_text["right"] == 2
        assert by_text["left"] == 1

    def test_the_returned_ids_are_unique(self):
        previous = [blk(f"L{i}", (0, i * 20, 200, i * 20 + 18), index=i + 1) for i in range(5)]
        lines = [line(f"L{i}", (1, i * 20, 201, i * 20 + 19)) for i in range(5)]
        current, _pairs, _ = track_blocks(previous, lines, 1.0, 6)
        assert len({b.id for b in current}) == 5


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
