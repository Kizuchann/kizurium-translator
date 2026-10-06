"""translation memory ≠ glossary ≠ hot cache."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.lexicon.store import exact, load_terms  # noqa: E402
from kizurium_translator.lexicon.translation_memory import TranslationMemory  # noqa: E402
from kizurium_translator.translate import Translator  # noqa: E402


class TestMemoryBasics:
    def test_remember_lookup_and_not_glossary(self, tmp_path):
        mem = TranslationMemory(tmp_path / "tm.sqlite")
        mem.remember("Hello there", "Привет там", source_lang="en", target_lang="ru", backend_id="gtx")
        assert mem.lookup("Hello there", source_lang="en", target_lang="ru") == "Привет там"
        # Not in lexicon packs until promoted.
        assert exact("Hello there", terms=load_terms()) is None

    def test_promote_writes_user_pack(self, tmp_path):
        mem = TranslationMemory(tmp_path / "tm.sqlite")
        mem.remember("Settings", "Настройки", source_lang="en", target_lang="ru")
        pack = mem.promote_to_user_glossary(
            "Settings",
            source_lang="en",
            target_lang="ru",
            pack_id="from-memory",
            dictionaries_dir=tmp_path / "dicts",
        )
        assert pack is not None
        terms = load_terms(tmp_path / "dicts")
        assert exact("Settings", terms=terms) == "Настройки"


class TestTranslatorRemembers:
    def test_successful_translate_lands_in_memory(self, tmp_path, monkeypatch):
        mem = TranslationMemory(tmp_path / "tm.sqlite")
        monkeypatch.setattr(
            "kizurium_translator.lexicon.translation_memory.default_memory",
            lambda path=None: mem,
        )
        tr = Translator(
            target="ru",
            source="en",
            use_gtx=True,
            cache_path=tmp_path / "cache.sqlite",
            glossary={},
        )
        tr.via_gtx = lambda text, source=None: "из сети"
        assert tr.translate("Unique phrase xyz") == "из сети"
        assert mem.lookup("Unique phrase xyz", source_lang="en", target_lang="ru") == "из сети"
