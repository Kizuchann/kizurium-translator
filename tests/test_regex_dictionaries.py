"""regex dictionaries.

Pack file ``regex.tsv`` is compiled once, scoped like exact terms, and refused
when the pattern is malformed or invites catastrophic backtracking.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.lexicon.regex_rules import (  # noqa: E402
    RegexDictionaryError,
    RegexRule,
    apply_regex,
    load_regex_rules,
    validate_pattern,
)
from kizurium_translator.translation import user_glossary  # noqa: E402
from kizurium_translator.translation.service import glossary_translation  # noqa: E402


class TestValidate:
    def test_compiles_a_plain_pattern(self):
        assert validate_pattern(r"(?i)^lv\.?\s*(\d+)$").pattern

    def test_rejects_empty(self):
        with pytest.raises(RegexDictionaryError):
            validate_pattern("  ")

    def test_rejects_malformed(self):
        with pytest.raises(RegexDictionaryError):
            validate_pattern(r"(unclosed")

    def test_rejects_nested_quantifiers(self):
        with pytest.raises(RegexDictionaryError):
            validate_pattern(r"(a+)+")
        with pytest.raises(RegexDictionaryError):
            validate_pattern(r"(a*)*")


class TestLoadAndApply:
    def test_bundled_core_pack_loads(self):
        rules = load_regex_rules()
        assert rules
        assert any(r.pattern.startswith("(?i)^lv") for r in rules)

    def test_priority_and_scope(self):
        rules = (
            RegexRule(
                pattern=r"^X(\d+)$",
                replacement=r"global-\1",
                scope="global",
                priority=10,
                pack="core",
                compiled=validate_pattern(r"^X(\d+)$"),
            ),
            RegexRule(
                pattern=r"^X(\d+)$",
                replacement=r"game-\1",
                scope="game",
                priority=10,
                pack="arknights",
                compiled=validate_pattern(r"^X(\d+)$"),
            ),
        )
        assert apply_regex("X12", ruleset=rules, game="arknights") == "game-12"
        assert apply_regex("X12", ruleset=rules, game_on=False) == "global-12"

    def test_skips_latin_rule_on_cjk_only_text(self):
        rules = (
            RegexRule(
                pattern=r"(?i)^stage\s+(\d+)$",
                replacement=r"Этап \1",
                scope="global",
                priority=40,
                pack="core",
                compiled=validate_pattern(r"(?i)^stage\s+(\d+)$"),
            ),
        )
        # Pure CJK must not be touched by a Latin pattern that somehow matches.
        assert apply_regex("ステージ", ruleset=rules) is None

    def test_pack_file_round_trip(self, tmp_path):
        pack = tmp_path / "user-pack"
        pack.mkdir()
        (pack / "manifest.toml").write_text(
            'id = "user-pack"\nscope = "user"\n', encoding="utf-8"
        )
        (pack / "regex.tsv").write_text(
            "pattern\treplacement\tscope\tpriority\n"
            r"(?i)^hp\s*:\s*(\d+)$" + "\tОЗ: \\1\tuser\t90\n",
            encoding="utf-8",
        )
        rules = load_regex_rules(tmp_path)
        assert apply_regex("HP: 40", ruleset=rules) == "ОЗ: 40"


class TestWiredIntoGlossary:
    def test_lv_line_translates_without_network(self):
        user_glossary.replace({})
        try:
            assert glossary_translation("Lv. 12") == "Ур. 12"
            assert glossary_translation("Chapter 3") == "Глава 3"
        finally:
            user_glossary.replace({})
