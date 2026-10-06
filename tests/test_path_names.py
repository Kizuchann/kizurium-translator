"""A file name is a name, and the backend is confident it is not.

Found on a screenshot: a repository listing showed `FUNDING.yml` and
`LICENSE.md` covered by their own translations, and `bin` rendered as
"мусорное ведро" - a wastepaper basket, because `bin` in English is a container
for rubbish and the backend has no way to know it is a directory.

The distinction that matters is between a name and a word. `install the package`
is a sentence about an action and must read in Russian; `install`, alone on a
row of a listing, is a script called install and must not. A name carrying an
extension is always a name. A conventional directory name is a name when it
stands alone, and a word when it is part of a sentence - which is the whole of
the rule, and it is why the second half matters as much as the first.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402
from kizurium_translator.translate import (  # noqa: E402
    protect_paths,
    restore_paths,
)

# Names that carry an extension: always names.
EXTENSIONS = [
    "README.md",
    "LICENSE.md",
    "CHANGELOG.md",
    "FUNDING.yml",
    "config.toml",
    "flake.lock",
    "default.nix",
    "version.txt",
    ".gitignore",
    "transl.md",
]

# Conventional directory names, alone on a line.
ALONE = ["bin", "src", "nix", "install", "setup", "config", "tests", "docs", "lib"]

# The same words inside sentences, which must still be translated.
IN_SENTENCE = [
    "install the package now",
    "open the src directory",
    "Change the config file",
    "Please install now",
    "run the setup script",
    "config is missing",
]


class TestNamesWithExtensions:
    @pytest.mark.parametrize("name", EXTENSIONS)
    def test_it_is_recognised_as_a_path(self, name):
        _, found = protect_paths(name)
        assert found, name

    @pytest.mark.parametrize("name", EXTENSIONS)
    def test_it_survives_untouched(self, name):
        protected, found = protect_paths(name)
        assert restore_paths(protected, found) == name, name

    def test_a_split_extension_is_whole(self):
        """What RapidOCR actually produced on the reported page."""
        assert live.unglue_english("LICENSE. md") == "LICENSE.md"
        assert live.unglue_english("FUNDING. yml") == "FUNDING.yml"
        assert live.unglue_english("CHANGELOG. md") == "CHANGELOG.md"

    def test_a_url_is_not_a_path(self):
        protected, found = protect_paths("https://example.com/x")
        assert found == [], found
        assert restore_paths(protected, found) == "https://example.com/x"

    def test_a_version_is_not_a_path(self):
        protected, found = protect_paths("v2.0.4")
        assert found == [], found


class TestConventionalNamesAlone:
    @pytest.mark.parametrize("name", ALONE)
    def test_it_is_protected_when_alone(self, name):
        _, found = protect_paths(name)
        assert found == [name], (name, found)

    @pytest.mark.parametrize("name", ALONE)
    def test_it_survives_untouched(self, name):
        protected, found = protect_paths(name)
        assert restore_paths(protected, found) == name, name


class TestTheSameWordsInSentences:
    @pytest.mark.parametrize("text", IN_SENTENCE)
    def test_it_is_not_protected(self, text):
        """A sentence about an action must read in Russian.

        Protecting every short token would leave most of a page untranslated,
        which is the same failure as not protecting any of them.
        """
        _, found = protect_paths(text)
        assert found == [], (text, found)

    @pytest.mark.parametrize("text", IN_SENTENCE)
    def test_and_it_is_unchanged(self, text):
        protected, found = protect_paths(text)
        assert restore_paths(protected, found) == text, text


class TestNoTranslationReturnedAsAPath:
    def test_a_name_the_backend_cannot_translate_comes_back_unchanged(self):
        """Not a placeholder, and not a wrong word.

        The row shows the original name, which is what a directory called `bin`
        should look like on screen.
        """
        from kizurium_translator.translate import Translator

        tr = Translator(target="ru", source="auto")
        # No network in the test: the result must not be a placeholder.
        for name in ("bin", "src", "README.md"):
            out = tr.translate(name, source="en", allow_slow=False)
            assert "PATH" not in out, (name, out)
            assert "PATH" not in out.upper(), (name, out)
            assert "\\u3010" not in out, (name, out)


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))

    def test_the_word_list_exists(self):
        from kizurium_translator.translate import _PATH_WORDS

        assert _PATH_WORDS
        assert "bin" in _PATH_WORDS
        assert "src" in _PATH_WORDS
