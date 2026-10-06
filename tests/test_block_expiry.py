"""A card has to come down when its text does.

Text that vanishes leaves nothing to re-read. The incremental path only visits
elements whose own pixels moved enough to be scheduled, so an element that
disappeared was never visited, nothing noticed, and the card stayed where it was
over whatever replaced it. There was no state for that: the tracker had no way
to say "I have not seen this recently" and nothing counted passes.

The two questions are kept apart on purpose. Not being scheduled this pass means
the pixels did not move, which is also what a still-present element looks like,
so it is not evidence. Being scheduled and coming back empty is a look at the
box and finding nothing, and only that counts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from kizurium_translator import live  # noqa: E402
from kizurium_translator.live import State, TrackedBlock  # noqa: E402

SIZE = (600, 400)
BOX = (100, 100, 260, 140)


def block(text: str = "Line of dialogue", box: tuple = BOX, kind: str = "dialogue") -> TrackedBlock:
    return TrackedBlock(
        id=1,
        box=box,
        text=text,
        norm=text.lower(),
        script="en",
        lang="en",
        conf=90.0,
        engine="rapidocr",
        params={"kind": kind},
    )


def frame(with_text: bool) -> Image.Image:
    """A frame whose box holds either text or nothing."""
    img = Image.new("RGB", SIZE, (18, 18, 22))
    if with_text:
        d = ImageDraw.Draw(img)
        d.rectangle(BOX, fill=(210, 210, 215))
        d.text((BOX[0] + 4, BOX[1] + 8), "hello", fill=(10, 10, 10))
    return img


class TestInkTest:
    def test_text_is_detected(self):
        assert live._box_has_ink(frame(True), BOX) is True

    def test_flat_background_is_not(self):
        assert live._box_has_ink(frame(False), BOX) is False

    def test_the_box_is_origin_width_height(self):
        """TrackedBlock.box is (x, y, w, h).

        Read as (x1, y1, x2, y2) the width comes out negative, crop raises, and
        the handler reports "something is there" - so a block whose text had gone
        was never once seen to be gone, which is the failure this whole module
        exists to remove.
        """
        img = Image.new("RGB", SIZE, (18, 18, 22))
        d = ImageDraw.Draw(img)
        # A box at the far corner proves which interpretation is in use: under
        # (x1,y1,x2,y2) this crop is empty, under (x,y,w,h) it holds the text.
        far = (400, 300, 120, 60)
        # Glyphs, not a filled rectangle: the test is contrast inside the box, so
        # a uniform fill would answer "nothing there" under either reading.
        d.rectangle((far[0], far[1], far[0] + far[2], far[1] + far[3]),
                    fill=(210, 210, 215))
        for i in range(0, far[2] - 8, 10):
            d.line((far[0] + 4 + i, far[1] + 8, far[0] + 4 + i, far[1] + far[3] - 8),
                   fill=(0, 0, 0), width=3)
        assert live._box_has_ink(img, far) is True

    def test_a_box_partly_outside_the_frame_is_clamped(self):
        """Clamped to the frame, so a card near an edge is still measurable."""
        img = frame(True)
        assert isinstance(live._box_has_ink(img, (580, 390, 400, 400)), bool)

    def test_a_uniform_but_light_box_is_still_not_text(self):
        """Brightness is not the question; contrast is.

        A pale card on a dark screen and a dark card on a pale page both read the
        same way when the answer depends on how bright the region is, so a bright
        uniform box is background rather than ink.
        """
        img = Image.new("RGB", SIZE, (18, 18, 22))
        ImageDraw.Draw(img).rectangle((BOX[0], BOX[1], BOX[0] + BOX[2], BOX[1] + BOX[3]),
                                      fill=(250, 250, 250))
        assert live._box_has_ink(img, BOX) is False

    def test_no_image_keeps_the_card(self):
        """When it cannot tell, it keeps the card.

        Losing a card that was still there is a worse failure than keeping one
        that has gone for a pass.
        """
        assert live._box_has_ink(None, BOX) is True


class TestGrace:
    def test_dialogue_goes_sooner_than_a_label(self):
        dlg = block("I see... Pleased to meet you", kind="dialogue")
        ui = block("Settings", kind="ui")
        assert live.gone_grace(dlg) < live.gone_grace(ui)

    def test_spoken_text_counts_as_dialogue(self):
        assert live.gone_grace(block("I see... Pleased to meet you")) == live.GONE_GRACE_DIALOGUE

    def test_the_grace_is_short(self):
        """A card outliving its text is the failure this exists to stop."""
        assert live.GONE_GRACE_DIALOGUE <= 3
        assert live.GONE_GRACE_UI <= 4


class TestConfirmGone:
    def test_seen_blocks_are_reset_and_kept(self):
        b = block()
        b.missing_passes = 2
        gone, keep = live.confirm_gone(frame(True), [b], {1}, 0.0)
        assert gone == []
        assert b.missing_passes == 0
        assert keep == [b]

    def test_an_empty_read_keeps_the_block_until_the_grace_is_spent(self):
        """One miss is a missed frame, not a removal."""
        b = block(kind="ui")
        for _ in range(live.GONE_GRACE_UI - 1):
            gone, keep = live.confirm_gone(frame(False), [b], set(), 0.0)
            assert gone == []
        gone, keep = live.confirm_gone(frame(False), [b], set(), 0.0)
        assert gone == [1]

    def test_a_block_that_still_has_ink_is_reset(self):
        """Unreadable and gone are different answers."""
        b = block(kind="ui")
        b.missing_passes = live.GONE_GRACE_UI - 1
        gone, keep = live.confirm_gone(frame(True), [b], set(), 0.0)
        assert gone == []
        assert b.missing_passes == 0

    def test_clearing_counts_toward_the_grace(self):
        b = block(kind="ui")
        gone, _ = live.confirm_gone(frame(False), [b], set(), 0.0)
        assert b.missing_passes == 1
        gone, _ = live.confirm_gone(frame(False), [b], set(), 0.0)
        assert b.missing_passes == 2


class TestCardsComeDown:
    def test_only_the_named_card_is_removed(self):
        st = State()
        st.set(
            [
                {"x": 100, "y": 100, "src_w": 160, "src_h": 40, "text": "gone"},
                {"x": 300, "y": 200, "src_w": 200, "src_h": 30, "text": "stays"},
            ],
            None,
        )
        removed = st.drop_cards_at([(100, 100, 160, 40)])
        assert removed == 1
        left = st.peek_blocks()
        assert len(left) == 1
        assert left[0]["text"] == "stays"

    def test_a_box_that_matches_nothing_removes_nothing(self):
        st = State()
        st.set([{"x": 10, "y": 10, "src_w": 50, "src_h": 20, "text": "keep"}], None)
        assert st.drop_cards_at([(900, 900, 100, 30)]) == 0
        assert len(st.peek_blocks()) == 1

    def test_an_empty_list_is_a_no_op(self):
        st = State()
        st.set([{"x": 1, "y": 1, "src_w": 10, "src_h": 10}], None)
        assert st.drop_cards_at([]) == 0
        assert len(st.peek_blocks()) == 1


class TestLifecycle:
    def test_a_block_can_report_that_it_went_missing(self):
        b = block()
        assert b.missing is False
        b.missing_passes = 1
        assert b.missing is True

    def test_fresh_blocks_start_clean(self):
        b = block()
        assert b.missing_passes == 0
        assert b.stable_passes == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))  # noqa: F821
