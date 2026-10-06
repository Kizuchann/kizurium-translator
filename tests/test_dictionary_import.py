"""LEX-07: external dictionary research and safe import."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.lexicon.import_external import (  # noqa: E402
    detect_format,
    import_dictionary_file,
    parse_plain_dictionary,
    parse_xunity_lines,
)
from kizurium_translator.lexicon.regex_rules import apply_regex, load_regex_rules  # noqa: E402
from kizurium_translator.lexicon.store import exact, load_terms  # noqa: E402


class TestParseXUnity:
    def test_exact_and_regex_lines(self):
        text = "\n".join(
            [
                "Hello=Привет",
                r"r:^(?i)hp\s*(\d+)$=ОЗ $1",
                "# comment",
                "[Dialogue]",
                "Same=Same",
            ]
        )
        exact_pairs, regex_pairs, notes = parse_xunity_lines(text)
        assert ("Hello", "Привет") in exact_pairs
        assert regex_pairs and regex_pairs[0][0].startswith("(?i)^hp")
        assert regex_pairs[0][1] == r"ОЗ \1"
        assert any("directive" in n for n in notes)

    def test_rejects_redos_regex(self):
        _, regex_pairs, notes = parse_xunity_lines("r:(a+)+ =x")
        assert regex_pairs == []
        assert any("rejected" in n for n in notes)


class TestParsePlain:
    def test_equals_and_tsv(self):
        text = "Foo=Бар\nBaz\tКвз\n"
        pairs, _ = parse_plain_dictionary(text)
        assert ("Foo", "Бар") in pairs
        assert ("Baz", "Квз") in pairs


class TestImportWritesPack:
    def test_round_trip_into_user_root(self, tmp_path):
        src = tmp_path / "game.txt"
        src.write_text(
            "Settings=Мои настройки\n"
            r"r:^(?i)lv\.?\s*(\d+)$=Ур. $1" + "\n",
            encoding="utf-8",
        )
        dest = tmp_path / "dicts"
        result = import_dictionary_file(
            src, pack_id="demo-game", dest_root=dest, fmt="xunity"
        )
        assert result.exact == 1
        assert result.regex == 1
        assert (result.pack_dir / "manifest.toml").is_file()
        assert (result.pack_dir / "terms.tsv").is_file()
        assert (result.pack_dir / "regex.tsv").is_file()
        terms = load_terms(dest)
        assert exact("Settings", terms=terms, game_on=True) == "Мои настройки"
        rules = load_regex_rules(dest)
        assert apply_regex("Lv 7", ruleset=rules) == "Ур. 7"

    def test_detect_xunity_from_r_prefix(self):
        assert detect_format("r:a=b\n") == "xunity"
        assert detect_format("a=b\n") == "plain"
