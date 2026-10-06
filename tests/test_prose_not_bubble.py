"""A codex entry is not a person speaking.

Found on a screenshot: a four-line game hint came back as two cards, with the
first two lines joined mid-word - "Safe Areas" as "Safe Are as" - and the rest of
the hint untranslated. Six lines were read; three arrived.

The bubble logic took it. A bubble is recognised by being short and by stopping
mid-sentence, and this hint has both: a line ending in "progressing" reads as a
half-spoken sentence whatever it is, and the next line below it was taken as the
rest of it. Nothing in the checks knew the difference between a sentence in a
dialogue and a sentence in a codex entry, because nothing looked at how long the
lines are.

Density is the difference, and it is not a dictionary. A spoken line is three to
six words and starts with a name. A paragraph is seven or more, and a capital
in the middle of it is a proper noun rather than a new sentence - which matters
here because the recogniser misreads "Areas" as "Are as" and the stray "Are" then
counted as a capital and the line stopped looking like what it actually is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402


def line(text: str, y: int, h: int = 27) -> dict:
    return {"text": text, "box": (648, y, 1237, y + h), "line_height": h}


# The hint, as the recogniser returned it on the reported screen.
HINT = [
    line("Inactive Safe Are as appear as blue pillars of light.", 495),
    line("beacons serve as important landmarks when progressin", 523),
    line("through unexplored areas—it's a good idea to head there", 550),
    line("first.", 577, 28),
]

# A dialogue bubble, which must still merge.
BUBBLE = [
    line("Amiya: I know that feeling", 100, 24),
    line("believe me, we all did at some point", 126, 24),
    line("Amiya: we all did", 152, 24),
]

# A second bubble that stops mid-sentence, which is the other case.
BUBBLE_INCOMPLETE = [
    line("Amiya: I know that feeling, and I", 100, 24),
    line("believe me, we all did", 126, 24),
]


class TestTheHintIsNotABubble:
    @pytest.mark.parametrize("text", [h["text"] for h in HINT[1:3]])
    def test_the_middle_lines_read_as_prose(self, text):
        """The two that were joined.

        The first line starts with a capital because it starts a sentence, and
        the last is one word; neither is the line that was merged, and neither
        should have to be.
        """
        assert live._reads_as_prose(text), text

    def test_consecutive_lines_do_not_merge(self):
        for a, b in zip(HINT, HINT[1:]):
            assert not live.vn_lines_should_merge(a, b), (a["text"], b["text"])

    def test_the_first_pair_would_have_merged_without_the_test(self):
        """The state the screenshot showed, so the fix has something to be against."""
        a, b = HINT[1], HINT[2]
        # both long, both lowercase, and 1px apart vertically
        assert abs(a["box"][0] - b["box"][0]) < 140
        assert b["box"][1] - a["box"][3] <= 28

    def test_the_mid_word_split_is_why_the_capital_rule_is_loose(self):
        """"Are as" out of "Areas" must not make the line look like speech.

        The first line of a hint is a sentence and starts with a capital, so it
        is not prose by the opening rule; the rule is loose about capitals
        further in precisely so that the misread "Are" cannot save a line that
        would otherwise be caught.
        """
        broken = HINT[0]["text"]
        assert "Safe Are as" in broken, broken
        assert len(broken.split()) >= 7
        # the stray capital is in the middle, not the opening
        assert broken.split()[0][:1].isupper()
        assert sum(1 for w in broken.split()[2:] if w[:1].isupper()) >= 1


class TestBubblesStillMerge:
    def test_a_named_speaker_merges(self):
        assert live.vn_lines_should_merge(BUBBLE[0], BUBBLE[1]), BUBBLE[0]["text"]

    def test_a_line_that_stops_mid_sentence_merges(self):
        assert live.vn_lines_should_merge(
            BUBBLE_INCOMPLETE[0], BUBBLE_INCOMPLETE[1]
        )

    def test_a_speaker_prefix_is_recognised_on_the_hint_too(self):
        """If it were a bubble it would merge - which is the point."""
        as_bubble = line("Amiya: Inactive Safe Areas appear as blue", 495)
        nxt = line("beacons serve as important landmarks", 523)
        assert live.looks_like_spoken_line(as_bubble["text"])
        assert live.vn_lines_should_merge(as_bubble, nxt)

    def test_the_ink_band_pass_is_not_reached_for_prose(self):
        """A line of prose must not ask to have its continuation read."""
        assert all(live._reads_as_prose(h["text"]) for h in HINT[1:3])


class TestWhatCountsAsProse:
    def test_seven_lowercase_words_is_prose(self):
        assert live._reads_as_prose(
            "beacons serve as important landmarks when progressing through areas"
        )

    def test_a_short_line_is_not_prose_however_plain(self):
        assert not live._reads_as_prose("believe me, we all did")
        assert not live._reads_as_prose("we all did at some point")

    def test_a_named_speaker_is_not_prose(self):
        assert not live._reads_as_prose("Amiya: we all did at some point here")

    def test_a_proper_noun_mid_line_does_not_stop_it_being_prose(self):
        """A capital in the middle is a name, not a sentence start."""
        assert live._reads_as_prose(
            "the beacons near Arknights serve as important landmarks here now"
        )

    def test_a_sentence_start_later_on_is_counted(self):
        """Enough capitals spread through it is a list, not a paragraph."""
        assert not live._reads_as_prose(
            "Amiya believes we all did Server Errors Too Many Requests here"
        )


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
