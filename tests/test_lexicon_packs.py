"""Фазы 11–14: словари в данных, формат, каталоги, приоритет областей.

"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import translate  # noqa: E402
from kizurium_translator.core.text import SPEAKER_NAMES  # noqa: E402
from kizurium_translator.lexicon.store import (  # noqa: E402
    Term,
    compile_index,
    exact,
    groups,
    load_terms,
)
from kizurium_translator.translation import user_glossary  # noqa: E402
from kizurium_translator.translation.service import glossary_translation  # noqa: E402

# Сколько записей было в коде до переноса. Если файл потерял строку, тест падает.
EXPECTED = {
    "ui": 75,
    "game_vocab": 80,
    "title_vocab": 5,
    "proper_names": 11,
    "title_names": 28,
    "stat_abbr": 41,
    "speakers": 46,
}


class TestNothingWasDropped:
    def test_every_group_kept_its_count(self):
        got = groups()
        assert len(got.ui) == EXPECTED["ui"]
        assert len(got.game_vocab) == EXPECTED["game_vocab"]
        assert len(got.title_vocab) == EXPECTED["title_vocab"]
        assert len(got.proper_names) == EXPECTED["proper_names"]
        assert len(got.title_names) == EXPECTED["title_names"]
        assert len(got.stat_abbr) == EXPECTED["stat_abbr"]
        assert len(got.speakers) == EXPECTED["speakers"]

    def test_the_module_names_are_the_data(self):
        assert translate._UI_TRUTHY == groups().ui
        assert "Vanguard" in translate._PROPER_NAMES
        assert "Amiya" in translate._TITLE_NAMES
        assert "ミク" in SPEAKER_NAMES

    def test_tsv_round_trip_matches_the_loader(self):
        sources = {t.source for t in load_terms()}
        assert "Settings" in sources
        assert "Kal'tsit" in sources
        assert len(sources) >= sum(EXPECTED.values()) - 5  # одно слово может быть в двух ролях


class TestFormatAndLayout:
    def test_packs_follow_the_directory_layout(self):
        root = Path(__file__).resolve().parent.parent / "src" / "kizurium_translator" / "data" / "lexicons"
        assert (root / "core" / "en-ru" / "manifest.toml").is_file()
        assert (root / "core" / "en-ru" / "terms.tsv").is_file()
        assert (root / "games" / "arknights" / "terms.tsv").is_file()
        assert (root / "games" / "sekai" / "speakers.jsonl").is_file()
        assert (root / "games" / "genshin" / "manifest.toml").is_file()

    def test_sqlite_index_compiles(self, tmp_path):
        path = compile_index(tmp_path / "lexicon.sqlite3")
        import sqlite3

        n = sqlite3.connect(path).execute("SELECT COUNT(*) FROM terms").fetchone()[0]
        assert n >= 200


class TestScopePriority:
    def _terms(self):
        return (
            Term("Fire", "Огонь", "global", 10, frozenset(), "core-en-ru"),
            Term("Fire", "Пламя", "game", 10, frozenset(), "arknights"),
            Term("Fire", "Жар", "game", 10, frozenset(), "genshin"),
        )

    def test_user_beats_global_and_game(self):
        assert exact("Fire", user={"Fire": "Юзер"}, terms=self._terms(), game="arknights") == "Юзер"

    def test_selected_game_beats_global(self):
        assert exact("Fire", terms=self._terms(), game="arknights") == "Пламя"
        assert exact("Fire", terms=self._terms(), game="genshin") == "Жар"

    def test_global_is_used_when_no_game_is_selected(self):
        assert exact("Fire", terms=self._terms(), game_on=False) == "Огонь"

    def test_machine_translation_does_not_become_glossary(self, tmp_path):
        user_glossary.replace({})
        tr = translate.Translator(target="ru", source="en", cache_path=tmp_path / "c.sqlite", glossary={})
        tr.via_gtx = lambda text, source=None: "из сети"
        assert tr.translate("Hello there") == "из сети"
        assert user_glossary.snapshot() == {}

    def test_user_exact_is_what_the_screen_uses(self):
        user_glossary.replace({"Settings": "Мои настройки"})
        try:
            assert glossary_translation("Settings") == "Мои настройки"
        finally:
            user_glossary.replace({})
