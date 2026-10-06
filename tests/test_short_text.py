"""Short text is the text that changes most.

A two-letter label is the shortest thing on a game screen and it changes more
often than anything else: a counter, a toggle, a state. Every one of those
changes is invisible to a similarity threshold, because the old and the new text
share nothing except a length, and a threshold that forgives a misread character
also forgives a toggle.

The rule these tests pin: a block in the same place whose meaningful symbols
changed has changed. One symbol is enough.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402

TrackedBlock = live.TrackedBlock

# The list the stage is written around. Each of these is a real change a game UI
# makes, and each of them used to be reported as "the same text".
REQUIRED = [
    ("is", "15"),
    ("15", "is"),
    ("to", "go"),
    ("a", "I"),
    ("1", "2"),
    ("10", "11"),
    ("ON", "OFF"),
    ("OFF", "ON"),
    ("YES", "NO"),
    ("One", "Two"),
    ("Two", "Three"),
]


def blk(text: str, conf: float = 95.0) -> TrackedBlock:
    return TrackedBlock(
        id=1,
        box=(0, 0, 100, 20),
        text=text,
        norm=live.normalize_for_compare(text),
        script=live.block_script(text),
        lang=live.block_lang(text),
        conf=conf,
        engine="rapid",
    )


def changed(old: str, new: str, conf: float = 95.0) -> bool:
    return live.block_content_changed(blk(old, conf), new, live.block_script(new), conf)


class TestTheRequiredChanges:
    @pytest.mark.parametrize("old,new", REQUIRED)
    def test_it_is_a_change(self, old, new):
        assert changed(old, new), f"{old!r} -> {new!r} was reported as unchanged"

    @pytest.mark.parametrize("old,new", REQUIRED)
    def test_no_similarity_function_may_override_it(self, old, new):
        """The stage names these three by name, and they must agree.

        A block that changed can still be judged 'similar' by a threshold, and
        when the threshold won, the new line was never translated and the old
        translation stayed on screen.
        """
        assert live.significant_word_diff(old, new), old
        if hasattr(live, "keys_similar"):
            assert not live.keys_similar(old, new), old
        if hasattr(live, "dialogue_keys_similar"):
            assert not live.dialogue_keys_similar(old, new), old

    def test_the_flip_flops_are_both_directions(self):
        """A toggle changes both ways, and both have to register."""
        for a, b in (("ON", "OFF"), ("OFF", "ON"), ("YES", "NO"), ("NO", "YES")):
            assert changed(a, b), (a, b)
            assert changed(b, a), (b, a)

    def test_numbers_change_by_one_digit(self):
        assert changed("10", "11")
        assert changed("99", "100")


class TestCharFingerprint:
    def test_it_is_order_independent(self):
        """A reflow is not an edit."""
        assert live.char_fingerprint("ab") == live.char_fingerprint("ba")

    def test_it_ignores_whitespace(self):
        assert live.char_fingerprint(" a b ") == live.char_fingerprint("ab")

    def test_it_keeps_case(self):
        """Case is a state: ON -> on is a change, spacing is not."""
        assert live.char_fingerprint("ON") != live.char_fingerprint("on")

    def test_it_sees_a_single_symbol(self):
        assert live.char_fingerprint("is") != live.char_fingerprint("15")
        assert live.char_fingerprint("1") != live.char_fingerprint("2")

    def test_identical_text_has_an_identical_fingerprint(self):
        assert live.char_fingerprint("Settings") == live.char_fingerprint("Settings")

    def test_empty_text_is_handled(self):
        assert live.char_fingerprint("") == ()
        assert live.char_fingerprint(None) == ()


class TestWhatIsStillNotAChange:
    @pytest.mark.parametrize(
        "old,new",
        [
            ("Settings", "Settings"),
            (" is ", "is"),
            ("Start now", "start  now"),
            ("Hello", "Hello"),
        ],
    )
    def test_noise_is_not_a_change(self, old, new):
        assert not changed(old, new), (old, new)

    def test_identical_text_is_never_a_change(self):
        assert not changed("Level 12", "Level 12")

    def test_a_long_line_keeps_tolerating_jitter(self):
        """Above the short threshold, an OCR wobble is still not a change.

        The character rule is for short blocks. Applied to everything it would
        re-translate a whole paragraph on every frame a pixel moved.
        """
        old = "The quick brown fox jumps over the lazy dog again and again"
        assert not changed(old, old.replace("again", "again"))


class TestConfidenceStillMatters:
    def test_low_confidence_trusts_the_diff(self):
        assert changed("A reasonably long line of dialogue here", "A reasonably long line of dialogve here", conf=40.0)

    def test_a_confident_long_line_tolerates_a_wobble(self):
        old = "The quick brown fox jumps over the lazy dog again and again"
        assert not changed(old, old.replace("dog", "doo"), conf=95.0)


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
