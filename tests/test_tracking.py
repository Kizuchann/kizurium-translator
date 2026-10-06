"""Block tracking: geometry, matching, and what counts as a changed line.

These decide whether a translation on screen is still the translation of what
is on screen. A wrong answer here is a stale card, and it is not visible: the
card looks fine, it is just wrong.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.core.models import box_size_ratio  # noqa: E402
from kizurium_translator.live import (  # noqa: E402
    make_block_from_line,
    significant_word_diff,
    track_blocks,
)


def _line(text: str, box: tuple[int, int, int, int], conf: float = 90) -> dict:
    return {"text": text, "box": box, "conf": conf, "engine": "rapidocr"}


class TestSizeRatio:
    def test_identical_boxes(self):
        assert box_size_ratio((0, 0, 100, 100), (0, 0, 100, 100)) == 1.0

    def test_half_the_area(self):
        assert box_size_ratio((0, 0, 100, 50), (0, 0, 100, 100)) == 0.5

    def test_double_the_area_on_each_axis(self):
        assert box_size_ratio((0, 0, 200, 200), (0, 0, 100, 100)) == 0.25

    def test_double_the_height_alone_is_half(self):
        """The numerator mixed one box's width with the other's height.

        A box twice as tall and no wider came out at 1.0 - indistinguishable
        from an exact match - while having twice the area. Anything consulting
        this was being told a tall box and a square are the same size.
        """
        assert box_size_ratio((0, 0, 100, 200), (0, 0, 100, 100)) == 0.5

    def test_double_the_width_alone_is_half(self):
        assert box_size_ratio((0, 0, 200, 100), (0, 0, 100, 100)) == 0.5

    def test_equal_area_different_shape_is_still_one(self):
        """An area ratio cannot tell a 200x50 from a 100x100, and should not.

        Shape is a separate question, and the centre-offset check covers the
        case that matters. What must not happen is a different area being
        reported as identical.
        """
        assert box_size_ratio((0, 0, 200, 50), (0, 0, 100, 100)) == 1.0

    def test_the_ratio_is_symmetric(self):
        a, b = (0, 0, 200, 50), (0, 0, 100, 100)
        assert box_size_ratio(a, b) == box_size_ratio(b, a)

    def test_degenerate_boxes_do_not_divide_by_zero(self):
        assert box_size_ratio((5, 5, 5, 5), (0, 0, 100, 100)) >= 0.0


class TestSignificantWordDiff:
    def test_identical_text_is_not_a_change(self):
        assert not significant_word_diff("hello world", "hello world")

    def test_a_changed_word_is_a_change(self):
        assert significant_word_diff("hello there", "hello world")

    def test_numbers_count(self):
        """A game UI changes its numbers more often than its words.

        A letters-only pattern produced empty token sets for "1" and "2" and
        reported no change, leaving the old number's translation on screen.
        """
        assert significant_word_diff("1", "2")
        assert significant_word_diff("Level 5", "Level 6")
        assert significant_word_diff("HP 100/100", "HP 87/100")
        assert not significant_word_diff("HP 100/100", "HP 100/100")

    def test_one_and_two_letter_words_count(self):
        """A length of three was the bar, so these all compared as unchanged.

        The old translation stayed and the new line was never translated.
        """
        assert significant_word_diff("is", "15")
        assert significant_word_diff("to", "go")
        assert significant_word_diff("is", "it")
        assert significant_word_diff("I", "A")
        assert significant_word_diff("ON", "OFF")

    def test_a_lone_short_tail_in_a_long_line_is_noise(self):
        # Five words in common plus one short word only on one side: an OCR tail,
        # not a change of line. The length guard is what decides this, and it is
        # kept deliberately - a long differing word is a real change.
        assert not significant_word_diff(
            "the boss attacks again right now", "the boss attacks again right"
        )

    def test_a_changed_word_is_still_a_change_next_to_a_long_shared_line(self):
        assert significant_word_diff(
            "the boss attacks again right now", "the boss defends again right now"
        )

    def test_emptiness_is_not_a_change_by_itself(self):
        assert not significant_word_diff("", "")


class TestTesseractLanguages:
    """The refinement pass has to be able to read what it is asked to fix."""

    def test_japanese_gets_japanese(self):
        from kizurium_translator.live import tesseract_langs_for_script

        for script in ("jpn", "japanese", "ja", "kana", "kanji"):
            langs = tesseract_langs_for_script(script)
            assert "jpn" in langs, (script, langs)

    def test_english_stays_in_every_set(self):
        """The same pass is asked about mixed lines, so English cannot be dropped."""
        from kizurium_translator.live import tesseract_langs_for_script

        for script in ("", "jpn", "kor", "rus", "latn", "nonsense"):
            assert "eng" in tesseract_langs_for_script(script), script

    def test_latin_keeps_its_original_set(self):
        from kizurium_translator.live import OCR_LANGS, tesseract_langs_for_script

        assert tesseract_langs_for_script("latn") == OCR_LANGS

    def test_an_unknown_script_is_covered_not_guessed(self):
        """Not knowing the script must not mean falling back to a set without it.

        A fixed eng+rus could not read Japanese at all, so the pass meant to
        rescue a short low-confidence block was blind to the text that needed
        rescuing most.
        """
        from kizurium_translator.live import TESSERACT_MULTI, tesseract_langs_for_script

        assert tesseract_langs_for_script("") == TESSERACT_MULTI

    def test_the_requested_sets_are_actually_installed(self):
        """A language set naming a pack that is not installed fails every call."""
        import shutil
        import subprocess

        if not shutil.which("tesseract"):
            return
        try:
            out = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return
        from kizurium_translator.live import (
            TESSERACT_JAPANESE,
            TESSERACT_MULTI,
            tesseract_langs_for_script,
        )

        available = {ln.strip() for ln in out.splitlines()[1:] if ln.strip()}
        for langs in {TESSERACT_JAPANESE, TESSERACT_MULTI,
                      tesseract_langs_for_script("rus"),
                      tesseract_langs_for_script("kor")}:
            for lang in langs.split("+"):
                assert lang in available, f"{lang} missing, needed by {langs}"


class TestBlockIsTheAuthority:
    """A scene key cannot answer "did this element's text change"."""

    def test_a_block_asks_only_about_its_own_text(self):
        from kizurium_translator.live import block_content_changed, make_block_from_line

        now = time.monotonic()
        old = make_block_from_line(_line("Boss HP 100", (0, 0, 100, 20)), 1, now)
        assert block_content_changed(old, "Boss HP 100", "latn", 90) is False
        assert block_content_changed(old, "Boss HP 87", "latn", 90) is True

    def test_a_short_dynamic_word_is_a_change(self):
        from kizurium_translator.live import block_content_changed, make_block_from_line

        now = time.monotonic()
        old = make_block_from_line(_line("is", (0, 0, 40, 16)), 1, now)
        assert block_content_changed(old, "15", "latn", 90) is True

    def test_scene_changed_is_about_the_scene(self):
        from kizurium_translator.live import scene_changed

        assert scene_changed("Name: a new line", "Name: a new line") is False
        assert scene_changed("Name: something else", "Name: a new line") is True

    def test_a_swapped_scene_is_still_seen_as_changed(self):
        """Two elements exchanging text leaves the concatenation different.

        That is the case the scene key can catch. The case it cannot is two
        elements keeping their positions and swapping content between frames,
        which is why the per-block check exists at all.
        """
        from kizurium_translator.live import scene_changed

        assert scene_changed("a|b", "b|a") is True


class TestTrackBlocksIsOneToOne:
    def test_one_previous_block_claims_at_most_one_new_line(self):
        """A reflowed paragraph used to hand its id to every line.

        Greedy matching in reading order gave the wide previous box to the first
        new line and its translation; the rest were told they were new and
        re-translated every frame.
        """
        now = time.monotonic()
        previous = [make_block_from_line(_line("a long line of text", (0, 0, 300, 100)), 1, now)]
        lines = [
            _line("a long line of text", (0, 0, 300, 40)),
            _line("and a second one", (0, 50, 300, 40)),
        ]
        current, pairs, next_id = track_blocks(previous, lines, now, 2)
        assert len(pairs) == 1
        assert len({p.id for p, _ in pairs}) == 1
        assert len({b.id for _, b in pairs}) == 1
        # The unmatched line got a fresh id rather than borrowing the old one.
        assert sorted(b.id for b in current) == [1, next_id - 1]

    def test_every_previous_block_is_used_at_most_once(self):
        now = time.monotonic()
        previous = [make_block_from_line(_line(f"line {i}", (0, i * 20, 200, 18)), i + 1, now)
                    for i in range(3)]
        lines = [_line(f"line {i}", (0, i * 20, 200, 18)) for i in range(3)]
        _current, pairs, _next = track_blocks(previous, lines, now, 10)
        assert len(pairs) == 3
        assert len({p.id for p, _ in pairs}) == 3

    def test_an_unchanged_line_keeps_its_id(self):
        now = time.monotonic()
        previous = [make_block_from_line(_line("chapter 3", (10, 20, 200, 30)), 7, now)]
        lines = [_line("chapter 3", (10, 20, 200, 30))]
        current, pairs, next_id = track_blocks(previous, lines, now, 99)
        assert len(pairs) == 1
        assert current[0].id == 7
        assert next_id == 99, "a matched line must not consume an id"

    def test_a_completely_new_line_gets_a_new_id(self):
        now = time.monotonic()
        previous = [make_block_from_line(_line("old text", (0, 0, 100, 20)), 1, now)]
        lines = [_line("something else entirely", (500, 500, 100, 20))]
        current, pairs, next_id = track_blocks(previous, lines, now, 2)
        assert not pairs
        assert current[0].id == 2
        assert next_id == 3

    def test_one_of_two_overlapping_lines_keeps_the_block(self):
        """Two new lines in the same place: exactly one of them keeps the block.

        Which one is arbitrary from geometry alone - identical boxes score
        identically. What must not happen is both keeping it, or neither.
        """
        now = time.monotonic()
        previous = [make_block_from_line(_line("target", (0, 0, 100, 100)), 1, now)]
        lines = [
            _line("first candidate line", (0, 0, 100, 100)),
            _line("second candidate line", (0, 0, 100, 100)),
        ]
        current, pairs, _next = track_blocks(previous, lines, now, 2)
        assert len(pairs) == 1
        assert pairs[0][1].id == 1
        # The loser is a new block, not a second claimant on the old one.
        assert sum(1 for b in current if b.id == 1) == 1

    def test_a_closer_line_wins_the_block(self):
        now = time.monotonic()
        previous = [make_block_from_line(_line("target", (0, 0, 100, 40)), 1, now)]
        lines = [
            _line("far away line", (400, 400, 100, 40)),
            _line("nearly the same", (2, 2, 100, 40)),
        ]
        _current, pairs, _next = track_blocks(previous, lines, now, 2)
        assert len(pairs) == 1
        assert pairs[0][1].text == "nearly the same"
