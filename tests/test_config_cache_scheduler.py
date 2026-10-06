"""Фазы 6–10: снимок настроек, бэкенды, SQLite-кэш, планировщик.

Контракты из плана, а не форма модулей. Кэш различает языки и версию
словаря. Ответ после смены ревизии не сохраняется. gtx не идёт двумя
запросами сразу.

"""

import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.config import Config, ConfigError, load  # noqa: E402
from kizurium_translator.core import active  # noqa: E402
from kizurium_translator.translate import Translator, cache_key  # noqa: E402
from kizurium_translator.translation.cache import SqliteCache  # noqa: E402
from kizurium_translator.translation.scheduler import (  # noqa: E402
    LOCAL_BATCH_BLOCKS,
    LOCAL_BATCH_TOKENS,
    SCHEDULER,
    TranslationRequest,
    pack_local_batch,
)


class TestConfigStaysFrozen:
    def test_glossary_cannot_be_assigned_into(self):
        cfg = Config(glossary={"TIPS": "Подсказка"})
        with pytest.raises(TypeError):
            cfg.glossary["TIPS"] = "другое"  # type: ignore[index]

    def test_missing_file_is_defaults(self, tmp_path):
        cfg = load(tmp_path / "no-such.toml")
        assert cfg.target_lang == "ru"

    def test_broken_toml_is_an_error(self, tmp_path):
        path = tmp_path / "bad.toml"
        path.write_text("this = [", encoding="utf-8")
        with pytest.raises(ConfigError):
            load(path)

    def test_a_bad_interval_is_an_error(self):
        with pytest.raises(ConfigError):
            Config(interval=-1)


class TestActiveSnapshot:
    def test_configure_is_what_the_lower_layers_read(self, tmp_path, monkeypatch):
        from kizurium_translator import live

        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        live.configure(Config(target_lang="de", ocr_engines=("tesseract",), card_bg=(0.2, 0.2, 0.2, 0.4)))
        assert active.target_lang() == "de"
        assert active.ocr_engines() == ("tesseract",)
        assert active.card_bg()[0] == pytest.approx(0.2)
        live.shutdown()


class TestSqliteCacheSeparatesLanguages:
    def test_english_and_japanese_do_not_share_a_row(self, tmp_path):
        path = tmp_path / "c.sqlite"
        cache = SqliteCache(path, 20)
        cache.put(cache_key("Fire", "en", "ru"), "Огонь", glossary_version="a")
        cache.put(cache_key("Fire", "ja", "ru"), "炎", glossary_version="a")
        cache.flush(force=True)
        again = SqliteCache(path, 20, glossary_version="a")
        assert again.get_matching(cache_key("Fire", "en", "ru"), "a") == "Огонь"
        assert again.get_matching(cache_key("Fire", "ja", "ru"), "a") == "炎"

    def test_a_new_glossary_version_misses(self, tmp_path):
        path = tmp_path / "c.sqlite"
        cache = SqliteCache(path, 20, glossary_version="v1")
        key = cache_key("TIPS", "en", "ru")
        cache.put(key, "Советы")
        cache.flush(force=True)
        again = SqliteCache(path, 20, glossary_version="v2")
        assert again.get(key) == "Советы"
        assert again.get_matching(key, "v2") == ""

    def test_backend_and_model_are_stored(self, tmp_path):
        path = tmp_path / "c.sqlite"
        cache = SqliteCache(path, 20, backend_id="gtx", model_version="none")
        cache.put(cache_key("Hi", "en", "ru"), "Привет")
        cache.flush(force=True)
        row = sqlite3.connect(path).execute(
            "SELECT backend_id, model_version, dictionary_version FROM translations"
        ).fetchone()
        assert row[0] == "gtx"
        assert row[1] == "none"
        assert row[2] == ""

    def test_lru_drops_the_oldest_touch(self, tmp_path):
        cache = SqliteCache(tmp_path / "c.sqlite", 2)
        cache.put("a", "1")
        cache.put("b", "2")
        assert cache.get("a") == "1"  # a becomes newest
        cache.put("c", "3")
        assert cache.get("b") == ""
        assert cache.get("a") == "1"
        assert cache.get("c") == "3"

    def test_two_readers_see_a_flushed_row(self, tmp_path):
        path = tmp_path / "c.sqlite"
        writer = SqliteCache(path, 10)
        writer.put("k", "v")
        writer.flush(force=True)
        reader = SqliteCache(path, 10)
        assert reader.get("k") == "v"


class TestScheduler:
    def test_a_bumped_revision_rejects_the_old_request(self):
        captured = SCHEDULER.capture()
        req = TranslationRequest(
            session_id="live",
            state_revision=captured,
            block_id=1,
            content_revision=1,
            source="Hello",
            source_language="en",
            target_language="ru",
            text="Hello",
        )
        assert SCHEDULER.accept(req)
        SCHEDULER.bump()
        assert not SCHEDULER.accept(req)

    def test_online_slot_is_one_at_a_time(self):
        started = threading.Event()
        release = threading.Event()
        second_entered = threading.Event()

        def hold():
            with SCHEDULER.online():
                started.set()
                release.wait(2)

        def wait_for_slot():
            started.wait(2)
            with SCHEDULER.online():
                second_entered.set()

        first = threading.Thread(target=hold)
        second = threading.Thread(target=wait_for_slot)
        first.start()
        second.start()
        assert started.wait(2)
        time.sleep(0.05)
        assert not second_entered.is_set()
        release.set()
        second.join(2)
        first.join(2)
        assert second_entered.is_set()

    def test_local_batches_respect_the_caps(self):
        items = [(f"word{i}", "en") for i in range(LOCAL_BATCH_BLOCKS + 1)]
        batches = pack_local_batch(items)
        assert len(batches) == 2
        assert len(batches[0]) == LOCAL_BATCH_BLOCKS
        long = [(" ".join(["tok"] * LOCAL_BATCH_TOKENS), "en"), ("tail", "en")]
        packed = pack_local_batch(long)
        assert len(packed) == 2


class TestTranslatorVersions:
    def test_two_glossary_versions_do_not_share_a_hit(self, tmp_path):
        path = tmp_path / "c.sqlite"
        # A memory of its own per translator: the point is that the *cache* is
        # shared and the memory is not. Left on the default one, the first
        # translator remembered "от сети" and the second read it back before it
        # ever consulted the cache, which says nothing about glossary versions.
        first = Translator(
            target="ru", source="en", cache_path=path,
            tm_path=tmp_path / "tm-1.sqlite",
            glossary={"TIPS": "Советы"},
        )
        first.via_gtx = lambda text, source=None: "от сети"
        assert first.translate("Hello") == "от сети"
        first.cache_flush()
        second = Translator(
            target="ru", source="en", cache_path=path,
            tm_path=tmp_path / "tm-2.sqlite",
            glossary={"TIPS": "Подсказка"},
        )
        calls: list[str] = []
        second.via_gtx = lambda text, source=None: calls.append(text) or "заново"
        assert second.translate("Hello") == "заново"
        assert calls, "другая версия словаря не должна брать чужой кэш"
