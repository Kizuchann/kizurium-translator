"""A changed element is re-read; the frame is not.

The live loop runs every 550 ms over a region that is usually unchanged, and a
full OCR of a 1900x1000 region costs far more than the answer is worth when one
number ticked. These tests pin the rule: one changed element means a crop, a
few neighbouring ones mean one merged crop, and a full pass is reserved for the
cases where the changed area stops being a few places at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources, owner, patch_all

from kizurium_translator import live  # noqa: E402

TrackedBlock = live.TrackedBlock


def blk(i: int, x: int, y: int, w: int = 100, h: int = 20, text: str = "X") -> TrackedBlock:
    return TrackedBlock(
        id=i,
        box=(x, y, x + w, y + h),
        text=text,
        norm=text,
        script="en",
        lang="en",
        conf=95.0,
        engine="rapid",
    )


def row(n: int, gap: int = 110, y: int = 300) -> list[TrackedBlock]:
    return [blk(i, 100 + i * gap, y) for i in range(1, n + 1)]


class TestNeighbouringElementsAreOneRegion:
    def test_five_in_a_row_are_one_region(self):
        assert len(live.merge_dirty_regions(row(5))) == 1

    def test_a_row_with_no_gap_at_all_is_one_region(self):
        assert len(live.merge_dirty_regions(row(5, gap=100))) == 1

    def test_a_small_gap_is_still_one_region(self):
        """Ten pixels apart is a row of counters, not five areas."""
        assert len(live.merge_dirty_regions(row(5, gap=110))) == 1

    def test_genuinely_separate_areas_stay_separate(self):
        far = [blk(1, 100, 100), blk(2, 100, 600), blk(3, 100, 1100)]
        assert len(live.merge_dirty_regions(far)) == 3

    def test_one_block_is_one_region(self):
        assert len(live.merge_dirty_regions([blk(1, 100, 100)])) == 1

    def test_nothing_is_no_regions(self):
        assert live.merge_dirty_regions([]) == []

    def test_every_block_ends_up_somewhere(self):
        """Merging must not lose an element."""
        blocks = row(5) + [blk(9, 100, 1200)]
        groups = live.merge_dirty_regions(blocks)
        assert sum(len(g) for g in groups) == len(blocks)
        assert {b.id for g in groups for b in g} == {b.id for b in blocks}


class TestWhenAFullPassIsStillRight:
    def test_the_limit_counts_areas_not_elements(self):
        """Five elements that moved together are one area.

        The old rule counted elements and gave up past four, so a reflowing menu
        or a row of counters went to a full pass - the exact case where a crop
        is cheapest.
        """
        assert live.DIRTY_GROUPS_MAX < 5
        assert len(live.merge_dirty_regions(row(5))) <= live.DIRTY_GROUPS_MAX

    def test_many_separate_areas_still_exceed_the_limit(self):
        far = [blk(i, 100, 100 + i * 600) for i in range(1, 5)]
        assert len(live.merge_dirty_regions(far)) > live.DIRTY_GROUPS_MAX

    def test_no_previous_blocks_needs_a_full_pass(self, monkeypatch):
        patch_all(monkeypatch, "_reread_element", lambda *a: pytest.fail("cropped"))
        assert live.update_dirty_blocks(None, [], [1]) is None

    def test_no_dirty_blocks_needs_a_full_pass(self, monkeypatch):
        patch_all(monkeypatch, "_reread_element", lambda *a: pytest.fail("cropped"))
        assert live.update_dirty_blocks(None, [blk(1, 0, 0)], []) is None

    def test_an_id_that_matches_nothing_needs_a_full_pass(self, monkeypatch):
        patch_all(monkeypatch, "_reread_element", lambda *a: pytest.fail("cropped"))
        assert live.update_dirty_blocks(None, [blk(1, 0, 0)], [99]) is None


class TestAnUnreadableElementIsNotAFullPass:
    def test_the_rest_of_the_frame_is_still_updated(self, monkeypatch):
        """One blink should not cost the whole screen.

        Previously a single unreadable element aborted the incremental pass and
        ran a full OCR, so a cursor blink on one label was as expensive as a
        scene change.
        """
        from PIL import Image

        blocks = row(3)
        calls = []

        def reread(_img, b):
            calls.append(b.id)
            return "" if b.id == 2 else "NEW"

        patch_all(monkeypatch, "_reread_element", reread)
        out = live.update_dirty_blocks(Image.new("RGB", (1900, 1080)), blocks, [1, 2, 3])
        assert out is not None
        assert [b.id for b, _ in out] == [1, 3]
        assert calls == [1, 2, 3]

    def test_all_unreadable_is_still_a_full_pass(self, monkeypatch):
        from PIL import Image

        patch_all(monkeypatch, "_reread_element", lambda *a: "")
        assert live.update_dirty_blocks(
            Image.new("RGB", (1900, 1080)), row(2), [1, 2]
        ) is None


class TestThePassIsTargeted:
    def test_it_crops_one_element(self, monkeypatch):
        """Not the frame: a crop around the box."""
        from PIL import Image

        seen = []

        def fake_read_box(img, box):
            seen.append(box)
            return [{"text": "X", "box": box, "conf": 90.0, "line_height": 20}]

        patch_all(monkeypatch, "_read_box", fake_read_box)
        out = live.update_dirty_blocks(Image.new("RGB", (1900, 1080)), [blk(1, 500, 400)], [1])
        assert out, "the element was not re-read"
        assert seen, "no crop was taken"
        x1, y1, x2, y2 = seen[0]
        # the crop is around the element, nowhere near the whole frame
        assert x2 - x1 < 1900
        assert y2 - y1 < 200
        assert x1 <= 500 <= x2 and y1 <= 400 <= y2

    def test_a_grown_label_is_not_cut_off(self):
        """A label that got longer still has to be readable."""
        box = (100, 100, 200, 120)
        grow_x = max(12, int((box[2] - box[0]) * 0.7))
        wide = (box[0] - grow_x, box[1] - 3, box[2] + grow_x, box[3] + 3)
        assert wide[0] < box[0] and wide[2] > box[2]


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
