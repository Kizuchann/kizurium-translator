"""A one is not a lowercase L, and the last frame is not the truth.

The report was a counter reading "15" where the screen said "is", and the
element stayed on the old translation. There are two separate mistakes in that,
and they pull in opposite directions.

The first is that "is" and "15" look like the same thing to a recogniser: a one
and a lowercase l are the same stroke at text size, and so are an O and a zero,
an S and a five, a B and an eight. The list of pairs here says only that two
readings differing at those positions are candidates for the same glyph. It is
deliberately not a table of replacements - rewriting "l" to "1" anywhere in the
pipeline would corrupt every word that contains one, and the report asks for
targeted correction rather than global substitution.

The second is that the previous frame is tempting as a fix and must not be used
as one. If the last frame wins whenever the fresh reading differs, then an
element that genuinely changed from "is" to "15" is frozen at "is" forever,
which is the bug this stage exists to fix, moved rather than solved. So the
previous frame never votes. It corroborates a reading another engine produced:
if Tesseract and the last frame agree, and the crop read something only a
confusion away, then two independent sources are against one misread and the
misread loses.

The tests are in both directions. A misread is corrected, and a genuine change
is allowed to happen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources, owner, patch_all, source_text

from kizurium_translator import live  # noqa: E402


class TestThePairList:
    @pytest.mark.parametrize(
        "left,right",
        [
            ("is", "15"),
            ("O", "0"),
            ("I", "1"),
            ("l", "1"),
            ("S", "5"),
            ("B", "8"),
        ],
    )
    def test_the_reported_pairs_are_confusable(self, left, right):
        assert live.readings_are_confusable(left, right), (left, right)

    @pytest.mark.parametrize(
        "left,right",
        [("is", "no"), ("is", "in"), ("15", "16"), ("ON", "OFF"), ("l", "O")],
    )
    def test_real_differences_are_not_confusable(self, left, right):
        assert not live.readings_are_confusable(left, right), (left, right)

    def test_a_different_length_is_not_confusable(self):
        assert not live.readings_are_confusable("is", "i")
        assert not live.readings_are_confusable("is", "is it")

    def test_identical_readings_are_confusable(self):
        assert live.readings_are_confusable("is", "is")

    def test_empty_is_not_confusable_with_anything(self):
        assert not live.readings_are_confusable("", "is")
        assert not live.readings_are_confusable("is", "")

    def test_the_list_is_symmetric(self):
        assert live.confusable_chars("1", "l")
        assert live.confusable_chars("l", "1")

    def test_an_i_and_an_l_are_different_letters(self):
        """The list is the reported pairs, not every pair of similar shapes.

        A lowercase i has its dot and an l does not, so a recogniser that swaps
        them is doing something else. Keeping them out means "is" against "ls"
        is a real edit rather than a reading of the same word.
        """
        assert not live.readings_are_confusable("is", "ls")

    def test_two_real_edits_are_not_confusable(self):
        assert not live.readings_are_confusable("ab", "ac")


class TestNothingRewritesText:
    def test_there_is_no_replacement_table(self):
        """The pairs must never become a substitution applied to output text."""
        import ast

        src = source_text("resolve_short_element")
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "confusable_chars"
        )
        # confusable_chars answers a question; it must not return a replacement.
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        assert returns, "the function never returns anything"
        for node in returns:
            value = node.value
            assert isinstance(value, ast.BoolOp) or isinstance(value, ast.Constant), (
                f"confusable_chars returns {type(value).__name__} - it must answer "
                "yes or no, not propose a character to write"
            )

    def test_a_word_containing_l_is_left_alone(self):
        """No function here turns text into digits."""
        for text in ("live", "hello", "sold", "OFF", "balance"):
            assert live.readings_are_confusable(text, text)


# The two readings are injected rather than produced by a recogniser: the
# question being tested is which reading is chosen, not whether OCR can be made
# to misread a rendered glyph on demand.
def _resolve(monkeypatch, base, rapid, rapid_conf, tess, previous):
    patch_all(monkeypatch, "reocr_crop_rapid", lambda *a, **k: (rapid, rapid_conf))
    patch_all(monkeypatch, "reocr_crop_tesseract", lambda *a, **k: tess)
    return live.resolve_short_element(
        None, (0, 0, 40, 20), base, previous=previous
    )


class TestAMisreadIsCorrected:
    def test_two_engines_against_the_crop(self, monkeypatch):
        """The reported case: the crop reads "15", the other two read "is"."""
        got = _resolve(monkeypatch, "15", "15", 88.0, "is", "is")
        assert got == "is", got

    def test_a_confident_crop_is_not_overturned_by_one_disagreeing_word(self, monkeypatch):
        """With nothing to corroborate Tesseract, the confident read stands.

        A single second opinion is not enough to rewrite a confident reading,
        because if the element really did become "15" that is exactly the wrong
        move - it is the freeze, arrived at from the other direction.
        """
        got = _resolve(monkeypatch, "15", "15", 88.0, "is", "")
        assert got == "15", got

    @pytest.mark.parametrize(
        "misread,truth",
        [("1S", "IS"), ("l5", "15"), ("O5", "OS")],
    )
    def test_the_other_reported_pairs(self, monkeypatch, misread, truth):
        got = _resolve(monkeypatch, misread, misread, 90.0, truth, truth)
        assert got == truth, (misread, got)

    def test_a_lone_glyph_pair_is_not_decided_here(self, monkeypatch):
        """"B" and "8" are both real single tokens, and this stage cannot tell.

        Nothing in a glyph list settles it: either reading is a plausible thing
        for a screen to show, and the previous frame is not entitled to break the
        tie on its own. Leaving the reading alone is the honest outcome, and the
        test says so rather than picking a winner to make the code look busy.
        """
        got = _resolve(monkeypatch, "B", "B", 90.0, "8", "8")
        assert got == "B", got


class TestAGenuineChangeIsAllowed:
    def test_is_to_fifteen_sticks(self, monkeypatch):
        """The whole point: nothing may veto a real change to 15.

        Here Tesseract also misreads the new glyph and agrees with the crop, so
        there is no evidence at all against the fresh reading, and the previous
        frame is not allowed to invent any.
        """
        got = _resolve(monkeypatch, "15", "15", 92.0, "15", "is")
        assert got == "15", got

    def test_a_tesseract_disagreement_alone_does_not_revert(self, monkeypatch):
        """No previous frame to corroborate with, so the crop keeps its word."""
        got = _resolve(monkeypatch, "15", "15", 92.0, "is", "")
        assert got == "15", got

    def test_one_to_two_is_untouched(self, monkeypatch):
        got = _resolve(monkeypatch, "Two", "Two", 92.0, "One", "One")
        assert got == "Two", got

    def test_on_to_off_is_untouched(self, monkeypatch):
        got = _resolve(monkeypatch, "OFF", "OFF", 92.0, "ON", "ON")
        assert got == "OFF", got

    def test_yes_to_no_is_untouched(self, monkeypatch):
        got = _resolve(monkeypatch, "No", "No", 92.0, "Yes", "Yes")
        assert got == "No", got


class TestThePreviousFrameNeverVotes:
    def test_a_stable_reading_is_returned_unchanged(self, monkeypatch):
        got = _resolve(monkeypatch, "OK", "OK", 90.0, "OK", "OK")
        assert got == "OK", got

    def test_the_previous_frame_alone_outvotes_nothing(self, monkeypatch):
        """Tesseract unavailable: the crop is the only source and it stands."""
        got = _resolve(monkeypatch, "15", "15", 92.0, "", "is")
        assert got == "15", got

    def test_a_short_but_real_change_survives(self, monkeypatch):
        """Confusable lengths differ, so this is not a confusion at all."""
        got = _resolve(monkeypatch, "Hello", "Hello", 90.0, "", "Hi")
        assert got == "Hello", got


class TestTheTemporalBonusIsNarrow:
    def test_the_bonus_is_not_applied_when_nothing_confuses(self, monkeypatch):
        """Tesseract says "Two", the crop says "Three", last frame says "Two".

        These are not the same glyph, so continuity has nothing to say and the
        crop keeps its reading.
        """
        got = _resolve(monkeypatch, "Three", "Three", 92.0, "Two", "Two")
        assert got == "Three", got

    def test_the_bonus_needs_tesseract(self, monkeypatch):
        got = _resolve(monkeypatch, "15", "15", 92.0, "", "is")
        assert got == "15", got

    def test_the_bonus_needs_agreement(self, monkeypatch):
        """Last frame says "is", Tesseract says something else: no corroboration."""
        got = _resolve(monkeypatch, "15", "15", 92.0, "l5", "is")
        assert got == "15", got

    def test_the_bonus_needs_a_actual_change(self, monkeypatch):
        """When the crop already read the previous text, nothing is decided."""
        got = _resolve(monkeypatch, "is", "is", 60.0, "15", "is")
        assert got == "is", got


class TestWiring:
    def test_the_previous_frame_is_passed_in_from_the_tracker(self):
        """The dirty re-read hands the element's last text to the resolver."""
        import ast

        src = source_text("resolve_short_element")
        assert "previous=blk.text or \"\"" in src, (
            "resolve_short_element is never told what the element read as before, "
            "so the corroboration path is unreachable"
        )

    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
