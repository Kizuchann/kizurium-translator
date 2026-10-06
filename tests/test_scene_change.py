"""A scene change must invalidate what was on screen before it.

The failure this guards against is not a wrong translation, it is the wrong
picture: a session started on one scene, moved to another, and showing both at
once. It happened because two decisions were made from unrelated evidence.

The scene classifier called anything without a ``Name:`` block a static page. A
game scene with a prose block and three labels has no speaker name, so it was
classified as a page, and a page is only re-examined when the pixels outside
our own cards change. Between workspaces the pixels do change, but the change
was consumed by the incremental path, which re-read the boxes it was already
tracking - the ones from the previous scene - and never asked what the frame
now contains.

So the two questions are separated here: does this look like a static page at
all, and did the content change enough that the tracked boxes are no longer
trustworthy. The second does not depend on the first.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from kizurium_translator import live  # noqa: E402

SIZE = (400, 300)


def scene(label: str, *, left: int = 40, top: int = 40, text: str = "") -> Image.Image:
    """A frame with one block of text at a known spot."""
    img = Image.new("L", SIZE, 20)
    d = ImageDraw.Draw(img)
    d.rectangle((left, top, left + 160, top + 24), fill=235)
    d.text((left + 4, top + 6), text or label, fill=0)
    return img


def card(x: int, y: int, w: int = 160, h: int = 24) -> dict:
    return {"x": x, "y": y, "src_w": w, "src_h": h}


class TestSceneIsNotAPage:
    def test_a_prose_scene_without_a_speaker_is_not_a_page(self):
        """The absence of ``Name:`` is not evidence of a static page.

        This is the misclassification that let the failure through: a game
        scene with a long prose block has no speaker name, and inheriting the
        page treatment meant a near-infinite recheck interval.
        """
        blocks = [
            {"kind": "body", "source": "A modern nation ruled by Draco"},
            {"kind": "ui", "source": "Victorian Empire"},
            {"kind": "body", "source": "bladepoint, ensuring the survival"},
            {"kind": "ui", "source": "Downloading update"},
        ]
        assert live.is_static_ui_overlay(blocks) is False
        # The clause that turned that into "page" is the absence of a named
        # speaker, which this scene also has. It must not be enough on its own.
        dlg = live.live_dialogue_cards(blocks)
        vn_named = any(
            __import__("re").match(r"^[^:]{2,28}:\s+\S", str(b.get("source") or ""))
            for b in (dlg or [])
        )
        assert vn_named is False

    def test_a_menu_still_counts_as_static(self):
        """The other half: a real menu must keep the cheap treatment."""
        menu = [
            {"kind": "ui", "source": f"Menu item {i}"} for i in range(6)
        ]
        assert live.is_static_ui_overlay(menu) is True


class TestSceneChangeIsVisible:
    def test_two_scenes_differ_outside_the_old_cards(self):
        """The old cards cannot mask a whole-scene swap.

        Masking exists so our own overlay does not read as motion. It must not
        extend to covering the only evidence that the scene changed.
        """
        a = scene("first", text="Victorian")
        b = scene("second", text="Sonico", left=40, top=200)
        # One card sitting where the first scene's text was.
        cards = [card(40, 40)]
        assert live.probe_changed(live.probe(a), live.probe(b), cards, SIZE) is True

    def test_the_same_scene_is_not_a_change(self):
        """Otherwise every cycle pays for a full read."""
        a = scene("same", text="Settings")
        b = scene("same", text="Settings")
        cards = [card(40, 40)]
        assert live.probe_changed(live.probe(a), live.probe(b), cards, SIZE) is False


class TestTrackedBlocksBelongToAScene:
    def test_tracked_blocks_from_another_scene_are_detectable(self):
        """What the incremental path needs to be able to answer.

        The incremental re-read is correct only while the tracked boxes still
        describe the frame. When they do not, the answer has to be available
        from the cheap image before any OCR is spent.
        """
        old = scene("Victorian", text="Victorian")
        new = scene("Sonico", text="Sonico", left=40, top=200)
        # The tracked box is where the old text was; the new text is elsewhere.
        changed = live.probe_changed(
            live.probe(old), live.probe(new), [card(40, 40)], SIZE
        )
        assert changed, "a scene swap must be visible to the cheap probe"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))  # noqa: F821
