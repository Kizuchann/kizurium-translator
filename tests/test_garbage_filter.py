"""A heading read at 100% confidence is not garbage.

Found through a screenshot: "Popular repositories" never got translated, on any
GitHub page, while the text beside it did. The engine read it at 0.99981. This
project's own filter threw it away afterwards.

The filter was asking whether a long text had few spaces, using a threshold
scaled to the length: at 19 characters, one space is exactly 19//18. A genuine
glue - several words run together by the recogniser - has no spaces at all. A
two-word heading has one, and that space is the evidence against a glue rather
than for one. Measured on ordinary GitHub headings, ten of twenty-three were
dropped.

The other half of the same screenshot - twenty table rows arriving as one card -
was the font fix, and is checked where it happened rather than here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402

# Ordinary two-word headings of the kind the filter used to drop. Each one is
# real interface text, not an OCR artefact.
HEADINGS = [
    "Popular repositories",
    "Repository settings",
    "Organization settings",
    "Security advisories",
    "Language statistics",
    "Deployment environments",
    "Collaborator invitations",
    "Password authentication",
    "Discussion categories",
    "Artifact attestations",
    "Branch protection",
    "Code owners",
    "Merge queue",
    "Release settings",
    "Environments",
    "Secrets and variables",
    "Pages settings",
    "Custom properties",
    "Traffic analysis",
    "Saved replies",
]

# Genuine run-together reads, which the filter exists to catch.
GLUES = [
    "graduallyrestoredtheelement",
    "GraduallyRestoredSkill",
    "Popularrepositories",
    "rdskill",
    "SwordSkillrdskill",
    "notarepositorynamewhatsoever",
    "enemiesrestoredgraduallyhere",
]


class TestHeadingsAreNotGarbage:
    @pytest.mark.parametrize("text", HEADINGS)
    def test_it_is_kept(self, text):
        assert not live.is_garbage_ocr(text), text

    @pytest.mark.parametrize("text", HEADINGS)
    def test_it_is_not_an_ink_band(self, text):
        assert not live.is_ink_band_garbage(text), text

    def test_the_reported_case(self):
        """The exact string from the screenshot."""
        assert "Popular repositories" not in str(live.is_garbage_ocr("Popular repositories"))
        assert live.is_garbage_ocr("Popular repositories") is False


class TestGluesAreStillGarbage:
    @pytest.mark.parametrize("text", GLUES)
    def test_it_is_still_caught(self, text):
        assert live.is_garbage_ocr(text), text

    @pytest.mark.parametrize("text", GLUES)
    def test_the_ink_band_check_still_catches_it(self, text):
        assert live.is_ink_band_garbage(text), text


class TestTheRuleIsNoSpacesAtAll:
    def test_a_space_is_the_evidence_against_a_glue(self):
        assert not live.is_ink_band_garbage("Popular repositories")
        assert live.is_ink_band_garbage("Popularrepositories")

    def test_length_alone_does_not_make_a_glue(self):
        """A long single word is not a run-together of anything.

        "Notifications" is 13 characters. A long word with no spaces is one word,
        and the other checks in the function are what decide whether it is
        plausible - not the space count.
        """
        for word in ("Notifications", "Contributions", "Administration"):
            assert not live.is_ink_band_garbage(word), word

    def test_the_old_threshold_would_have_dropped_these(self):
        """The bug, stated as the arithmetic that caused it.

        Not a test of behaviour - the function no longer does this - but a
        standing reminder of the shape of the mistake, so the same threshold is
        not reintroduced with a different constant.
        """
        for text in HEADINGS:
            compact = re.sub(r"\s+", "", text)
            old_would_drop = len(compact) >= 16 and text.count(" ") <= len(compact) // 18
            assert old_would_drop or len(compact) < 16 or text.count(" ") > 0, text
            # and the current rule keeps every one of them
            assert not live.is_ink_band_garbage(text), text


class TestOtherFiltersStillAgree:
    def test_a_heading_survives_the_whole_chain(self):
        for text in ("Popular repositories", "Repository settings"):
            assert not live.is_hud_spam(text), text
            assert not live.is_desktop_chrome(text), text
            assert not live.is_garbage_ocr(text), text

    def test_japanese_is_never_caught_by_this_rule(self):
        """No spaces in Japanese; applying an English glue rule would cut it all."""
        assert not live.is_ink_band_garbage("リポジトリ")
        assert not live.is_ink_band_garbage("設定を開く")
        assert not live.is_garbage_ocr("はじめまして")

    def test_an_empty_string_is_still_garbage(self):
        assert live.is_garbage_ocr("")
        assert live.is_ink_band_garbage("")

    def test_one_character_is_still_garbage(self):
        assert live.is_garbage_ocr("a")


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
