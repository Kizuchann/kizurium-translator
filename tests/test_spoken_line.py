"""A column of commit subjects is not a character speaking.

Found through a screenshot: on a GitHub file listing, twenty table rows came
back as one card covering most of the screen, and the neighbouring heading's
translation was written across the elements beside it.

The lines were not merged by size or by position - they were classified as
dialogue. The predicate that recognises a spoken line is `Name:...`, and a
commit subject is `feat:...`, `docs:...`, `fix:...` - the identical shape.
So a table of commit messages was read as one speaker with a very long
monologue, and the clusterer did exactly what it is for.

Both halves are pinned: the commit prefixes must be rejected, and the real
speaker names must keep working, since the same predicate is what makes dialogue
tracking possible in the first place.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402

# Speaker names from games and visual novels. These are the reason the predicate
# exists, so losing one costs real functionality.
SPOKEN = [
    "Amiya: I know that feeling, believe me",
    "Game Master: You have done well so far",
    "lori: please wait for just a moment",
    "Kizuchann: hello there, how are you",
    "Ilia: I will wait right here",
    "Dr. Emmet Brown: Great Scott!",
    "Sakiko: Let's go to the live house",
    "Mori: you came all the way here",
]

# Repository lines. Same shape, entirely different meaning - this is the list
# that was leaking through.
COMMIT_LINES = [
    "feat: add autohide toggle keybindings (#254)",
    "fix: add all of the new missing setting fields",
    "docs: move a pull request template into another folder",
    "chore: update flake.lock",
    "style: remove the border from the github widget",
    "refactor: split the capture module",
    "ci: run the test suite on push",
    "perf: reduce OCR latency",
    "build: bump the dependency versions",
    "test: add coverage for the tracker",
    "revert: undo the last commit",
    "deps: update rapidocr to 3.4",
    "docs: add an update note. Solves #218",
    "fix: a local function call bug",
]


class TestSpeakersStillSpeak:
    @pytest.mark.parametrize("text", SPOKEN)
    def test_a_speaker_is_still_recognised(self, text):
        assert live.looks_like_spoken_line(text), text

    def test_the_predicate_still_looks_for_the_shape(self):
        """It must still be a Name: shape check, not a lookup table of names."""
        assert live.looks_like_spoken_line("Zorua: the path splits here")


class TestCommitsAreNotSpeakers:
    @pytest.mark.parametrize("text", COMMIT_LINES)
    def test_a_commit_line_is_not_a_speaker(self, text):
        assert not live.looks_like_spoken_line(text), text

    @pytest.mark.parametrize("prefix", sorted(live.COMMIT_PREFIXES))
    def test_every_known_prefix_is_rejected(self, prefix):
        assert not live.looks_like_speaker_prefix(prefix), prefix

    def test_the_leaking_case_from_the_screenshot(self):
        """The exact string, and the merge it caused."""
        line = "feat: add autohide toggle keybindings (#254)"
        assert not live.looks_like_spoken_line(line)
        assert not live.looks_like_spoken_line("docs: move a pull request template")


class TestWhatCountsAsAName:
    @pytest.mark.parametrize("word", ["Amiya", "Game Master", "lori", "Kizuchann", "Ilia"])
    def test_a_name_is_accepted(self, word):
        assert live.looks_like_speaker_prefix(word), word

    @pytest.mark.parametrize("word", ["", "a", "1", "x", "feat", "docs", "fix"])
    def test_junk_and_action_words_are_not(self, word):
        assert not live.looks_like_speaker_prefix(word), word

    @pytest.mark.parametrize("word", ["http", "https", "www", "note", "error", "info"])
    def test_a_url_or_a_label_is_not_a_name(self, word):
        assert not live.looks_like_speaker_prefix(word), word

    def test_the_check_is_case_insensitive(self):
        """OCR is inconsistent about capitals; FEAT: is the same prefix."""
        assert not live.looks_like_speaker_prefix("FEAT")
        assert not live.looks_like_speaker_prefix("Docs")


class TestOtherShapesAreUnchanged:
    @pytest.mark.parametrize(
        "text",
        [
            "Popular repositories",
            "Repository settings",
            "feat: add autohide toggle keybindings",
        ],
    )
    def test_a_plain_line_is_not_a_speaker(self, text):
        assert not live.looks_like_spoken_line(text), text

    def test_too_short_is_not_a_speaker(self):
        assert not live.looks_like_spoken_line("a: b")

    def test_a_colon_with_nothing_after_is_not_a_speaker(self):
        assert not live.looks_like_spoken_line("Amiya:")

    def test_a_time_is_not_a_speaker(self):
        assert not live.looks_like_spoken_line("12:30 in the afternoon")


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
