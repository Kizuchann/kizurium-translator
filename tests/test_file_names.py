"""A file extension is one token, and a recogniser does not always know that.

RapidOCR reads `CHANGELOG.md` as `CHANGELOG. md` with a space after the dot, and
the backend then translates the halves separately: it reads `md` as the US state
and returns "ИЗМЕНЕНИЯ. Мэриленд" - on a repository page, on LICENSE.md and on
README.md alike. Three different files, one misread abbreviation, the same
nonsense word on every row.

The repair is a list of extensions rather than a rule about dots, because a
rule about dots turns "Hi. How are you" into "Hi.How are you". A sentence is
not a file, and the difference is whether the word after the dot is something a
file can end with.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402

# What the recogniser produced on the reported page.
SPLIT_NAMES = [
    "CHANGELOG. md",
    "LICENSE. md",
    "README. md",
    "FUNDING. yml",
    "flake. lock",
    "default. nix",
    "version. txt",
    "config. toml",
    ".gitignore",
]

# What it should have produced.
FIXED_NAMES = [
    "CHANGELOG.md",
    "LICENSE.md",
    "README.md",
    "FUNDING.yml",
    "flake.lock",
    "default.nix",
    "version.txt",
    "config.toml",
    ".gitignore",
]

# A sentence is not a file, and the repair must not touch one.
SENTENCES = [
    "Hi. How are you",
    "Done. Next step",
    "Are you ok. Yes",
    "The file. Then run it",
    "What now. Next",
    "Stop. Wait for it",
    "Done. All good",
    "Yes. That is right",
    "Ok. Let us go",
]


class TestTheExtensionIsRepaired:
    @pytest.mark.parametrize("text,expected", list(zip(SPLIT_NAMES, FIXED_NAMES)))
    def test_the_space_goes_away(self, text, expected):
        assert live.unglue_english(text) == expected, text

    def test_the_reported_case(self):
        """The exact string from the screenshot."""
        assert live.unglue_english("CHANGELOG. md") == "CHANGELOG.md"
        assert live.unglue_english("LICENSE. md") == "LICENSE.md"
        assert live.unglue_english("README. md") == "README.md"

    def test_a_name_that_is_already_whole_is_untouched(self):
        for name in FIXED_NAMES:
            assert live.unglue_english(name) == name, name


class TestSentencesAreNotFiles:
    @pytest.mark.parametrize("text", SENTENCES)
    def test_the_repair_leaves_them_alone(self, text):
        assert live.unglue_english(text) == text, text

    def test_a_common_word_after_a_dot_is_a_sentence(self):
        """"md" is an extension and "much" is not."""
        assert live.unglue_english("config. toml is here") == "config.toml is here"
        assert live.unglue_english("Done. Much better") == "Done. Much better"

    def test_a_number_after_a_dot_is_untouched(self):
        """`md` is an extension and a digit is not."""
        assert live.unglue_english("Version 2. 5 is odd") == "Version 2. 5 is odd"
        assert live.unglue_english("Step 1. 2 of the plan") == "Step 1. 2 of the plan"


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))

    def test_the_list_exists_and_is_not_empty(self):
        assert live._FILE_EXTENSIONS
        assert "md" in live._FILE_EXTENSIONS
        assert "yml" in live._FILE_EXTENSIONS
