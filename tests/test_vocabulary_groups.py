"""A default build does not know which games you play.

The proper-noun guard protects a capitalised run so that free translation
cannot rename it. That is right in a game, where the name is the only thing a
player can match against the screen. It is wrong for a build that carries the
roster by default: "Amiya", "Rhodes Island", "Arknights", "Phigros", "osu!",
"Genshin" are names belonging to particular games, and a program that holds all
of them fixed without being told anything is making a claim about its user that
nothing in the user's session supported.

So the vocabulary is three groups instead of one. Ordinary interface words and
ordinary game words are on, because a capitalised "Settings" or "Combo" is not a
name in any program. Per-title terms and per-title names are behind the switch
that already turns on the game glossary, because "hold this game's vocabulary
fixed" and "these are its characters" are one decision and not two.

What this does not do is remove the knowledge. Every term is still in the file;
the change is when it counts. The tests on the game's own screens enable the
switch explicitly, which is what they were already doing for the glossary half.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live, translate  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_title_switch():
    was = translate._TITLE_TRUTHY_ON
    yield
    translate.enable_title_glossary(was)


class TestTheGroupsExist:
    def test_three_groups_not_one(self):
        assert hasattr(translate, "_UI_TRUTHY")
        assert hasattr(translate, "_GAME_TRUTHY")
        assert hasattr(translate, "_TITLE_TRUTHY")

    def test_nothing_was_thrown_away(self):
        """Every term that was in the old single list is still in one group."""
        everything = (
            translate._UI_TRUTHY | translate._GAME_TRUTHY | translate._TITLE_TRUTHY
        )
        for term in ("Settings", "Combo", "Stamina", "Originium", "LMD", "My Sekai"):
            assert term in everything, term

    def test_the_default_set_excludes_the_titles(self):
        assert "Originium" not in translate._DEFAULT_TRUTHY
        assert "LMD" not in translate._DEFAULT_TRUTHY
        assert "Settings" in translate._DEFAULT_TRUTHY
        assert "Combo" in translate._DEFAULT_TRUTHY


class TestNamesAreSplitToo:
    def test_a_class_is_not_a_character(self):
        """Vanguard is a role any game might have, so it stays on."""
        assert "Vanguard" in translate._PROPER_NAMES
        assert "Vanguard" not in translate._TITLE_NAMES

    def test_a_character_is_not_vocabulary(self):
        for name in ("Amiya", "Rhodes Island", "Phigros", "osu!", "Genshin"):
            assert name in translate._TITLE_NAMES, name

    def test_the_active_set_follows_the_switch(self):
        translate.enable_title_glossary(False)
        assert "Amiya" not in translate.active_proper_names()
        assert "Vanguard" in translate.active_proper_names()
        translate.enable_title_glossary(True)
        assert "Amiya" in translate.active_proper_names()


class TestOffByDefault:
    def test_a_character_is_ordinary_text_by_default(self):
        translate.enable_title_glossary(False)
        protected, mapping = translate.protect_proper_nouns("Amiya trusted you")
        assert not mapping, mapping
        assert protected == "Amiya trusted you"

    def test_an_organisation_is_ordinary_text_by_default(self):
        translate.enable_title_glossary(False)
        _protected, mapping = translate.protect_proper_nouns(
            "Rhodes Island is counting on you"
        )
        assert not mapping, mapping

    def test_a_class_is_still_a_name_by_default(self):
        """The generic half does not need the switch to work."""
        translate.enable_title_glossary(False)
        _protected, mapping = translate.protect_proper_nouns(
            "Vanguard deployment cost reduced"
        )
        assert mapping, "the generic half stopped working"

    def test_a_title_term_does_not_hold_a_word_by_default(self):
        translate.enable_title_glossary(False)
        _protected, mapping = translate.protect_proper_nouns(
            "Originium cost went up"
        )
        assert not mapping, mapping

    def test_ui_words_are_never_names(self):
        """Whether or not a game is declared, a button is a button."""
        for text in ("Settings", "Early Bird Bonus deals damage", "Press E to talk"):
            protected, mapping = translate.protect_proper_nouns(text)
            assert not mapping, (text, mapping)
            assert protected == text


class TestOnWhenAsked:
    def test_a_character_is_protected(self):
        translate.enable_title_glossary(True)
        protected, mapping = translate.protect_proper_nouns("Amiya trusted you")
        assert mapping, mapping
        assert "ZZNAME0ZZ" in protected
        restored = translate.restore_proper_nouns(protected, mapping)
        # Glossary Cyrillic when one exists; English otherwise.
        assert restored in ("Amiya trusted you", "Амия trusted you"), restored

    def test_an_organisation_is_protected(self):
        translate.enable_title_glossary(True)
        _protected, mapping = translate.protect_proper_nouns(
            "Rhodes Island is counting on you"
        )
        assert mapping, mapping

    def test_ui_words_are_still_not_names(self):
        translate.enable_title_glossary(True)
        for text in ("Settings", "Early Bird Bonus deals damage", "Team Edit"):
            protected, mapping = translate.protect_proper_nouns(text)
            assert not mapping, (text, mapping)
            assert protected == text


class TestOneSwitchBothHalves:
    def test_the_live_switch_reaches_the_name_list(self):
        """The two halves turn on from one decision, or the halves disagree."""
        translate.enable_title_glossary(False)
        live.apply_game_glossary(True)
        assert translate._TITLE_TRUTHY_ON is True
        assert "Amiya" in translate.active_proper_names()

    def test_turning_it_off_turns_off_the_names(self):
        live.apply_game_glossary(True)
        live.apply_game_glossary(False)
        assert translate._TITLE_TRUTHY_ON is False
        assert "Amiya" not in translate.active_proper_names()

    def test_the_switch_reports_how_many(self):
        assert translate.enable_title_glossary(True) == len(translate._TITLE_TRUTHY)
        assert translate.enable_title_glossary(False) == 0


class TestNoDevelopmentLeftovers:
    @pytest.mark.parametrize(
        "trace", ["New Chat", "Automations", "Repositories", "Connect GitHub", "dolphin"]
    )
    def test_the_environment_the_rules_were_written_in_is_not_a_rule(self, trace):
        for path in all_sources():
            body = path.read_text(encoding="utf-8")
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped.startswith("#") and not stripped.startswith('"'):
                    assert trace not in stripped, f"{path.name}: {stripped}"


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        ast.parse(Path(translate.__file__).read_text(encoding="utf-8"))
        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))
