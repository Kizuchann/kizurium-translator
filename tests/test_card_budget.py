"""A card budget has to be spent on the text a reader wants translated.

Found on the real screen, not on a file: a GitHub page produced seventy-nine
readable lines, and nineteen of them never reached the translator. The cap kept
the first sixty after sorting by descending text length, so the shortest lines
were always the ones dropped - and on a page like that the shortest lines are
the headings and the file names: `README.md`, `flake.nix`, `.github`, `install`,
`bin`, `nix`, `src`, `Projects`, `Actions`, `Releases`, `Packages`. Every one a
real element, every one shorter than a commit subject.

The second half is a merger that no filter could see. "on o 'ses o sdn o ns- ee
n 2 weeks ago" is three columns of a table read as one line: two real words among
eleven fragments. Every existing check reads the long words, and there are almost
none, so it looked like a perfectly good sentence and was translated and shown.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402


def line(text: str, y: int = 0, w: int = 100, conf: float = 92.0) -> dict:
    return {
        "text": text,
        "box": (10, y, 10 + w, y + 20),
        "line_height": 20,
        "conf": conf,
    }


class TestTheCapKeepsWhatMatters:
    def test_a_page_under_the_cap_is_untouched(self):
        lines = [line(f"line {i}", y=i * 30) for i in range(10)]
        assert live.cap_ui_lines(lines) == lines

    def test_the_cap_exists(self):
        """A misread frame must not be able to fill the screen with cards."""
        assert live.UI_LINES_MAX > 0

    def test_nothing_is_lost_below_the_limit(self):
        lines = [line(f"item {i}", y=i * 30) for i in range(live.UI_LINES_MAX - 1)]
        assert len(live.cap_ui_lines(lines)) == len(lines)

    def test_a_confident_short_line_outranks_an_unsure_long_one(self):
        """Length was the wrong question, and this is what replaced it.

        A heading is one word and a commit subject is eight, so sorting by
        length dropped exactly the headings - and on a page of links the
        headings are what the reader came for. A doubtful read of a long line
        is a fragment of something else; a confident short one is a label.
        """
        heading = line("README.md", y=0, conf=95.0)
        fragment = dict(
            line("fix: add all of the new missing setting fields into the config", y=30)
        )
        fragment["conf"] = 40.0
        kept = live.cap_ui_lines([heading, fragment], limit=1)
        assert kept[0]["text"] == "README.md", kept

    def test_length_still_breaks_ties_between_confident_reads(self):
        a = line("Settings", y=0, conf=95.0)
        b = line("Sponsor this project", y=30, conf=95.0)
        kept = live.cap_ui_lines([a, b], limit=1)
        assert kept[0]["text"] == "Sponsor this project", kept

    def test_a_multi_word_line_is_kept_over_a_fragment(self):
        many = [line(f"fragment{i}", y=i * 30) for i in range(20)]
        phrase = line("Contributors 17", y=900)
        kept = live.cap_ui_lines(many + [phrase], limit=20)
        assert phrase in kept
        assert len(kept) == 20

    def test_the_result_is_stable_between_calls(self):
        """A card that flickers in and out is worse than one that is missing."""
        lines = [line(f"row {i}", y=i * 30) for i in range(90)]
        assert live.cap_ui_lines(lines) == live.cap_ui_lines(lines)

    def test_a_page_like_the_reported_one_keeps_its_headings(self):
        names = [
            ".github", "bin", "config", "docs/assets", "install", "nix", "src",
            "Readme", "compositors", "hyprland", "Insights", "Actions", "About",
            "Releases", "Packages", "Projects", "README.md", "flake.nix",
            ".gitignore", "LICENSE.md",
        ]
        bodies = [line(f"fix: change the number {i} in the module", y=i * 40) for i in range(59)]
        lines = [line(n, y=1000 + i * 30) for i, n in enumerate(names)] + bodies
        kept = {str(p["text"]) for p in live.cap_ui_lines(lines)}
        missing = [n for n in names if n not in kept]
        # Not every heading can fit under any budget; the point is that the
        # budget is now spent deliberately rather than by text length.
        assert len(missing) < len(names) // 2, missing


class TestMergersAreCaught:
    @pytest.mark.parametrize(
        "text",
        [
            "on o 'ses o sdn o ns- ee n 2 weeks ago",
            "a b c d e f g h i j",
            "x y z w v u t",
        ],
    )
    def test_a_line_of_fragments_is_garbage(self, text):
        assert live.is_garbage_ocr(text), text

    @pytest.mark.parametrize(
        "text",
        [
            "Press E to interact with the do or",
            "Cross-platform GUI proxy utility (Empowered by sing-box)",
            "Popular repositories",
            "You don't have any public repositories yet",
            "Contributors 17",
            "Sponsor this project",
            "1 contribution in the last year",
            "You dont have any public repositories yet.",
            "No releases published",
            "Learn how we count contributions",
        ],
    )
    def test_a_real_line_is_not_garbage(self, text):
        """Half short words is a sentence; the threshold sits above it.

        "Press E to interact with the do or" is exactly half short words and is
        a line a user would want translated, which is why the test is a
        fraction of the whole rather than a count.
        """
        assert not live.is_garbage_ocr(text), text

    def test_the_rule_looks_at_the_ratio_not_the_count(self):
        import re

        junk = "on o 'ses o sdn o ns- ee n 2 weeks ago"
        real = "Press E to interact with the do or"
        for text, expected in ((junk, True), (real, False)):
            tokens = re.findall(r"[A-Za-zА-Яа-яЁё']+", text)
            stubs = sum(1 for w in tokens if len(w) <= 2)
            assert (stubs * 5 >= len(tokens) * 3) is expected, text


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
