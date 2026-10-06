"""Память переводов принадлежит переводчику, а не процессу.

Что было сломано.

`Translator` получал `cache_path` и писал кэш туда, куда его направили. Память
переводов при этом бралась из `default_memory()` без аргумента - это
процессный синглтон, путь которого жёстко задан в
`lexicon/translation_memory.py`:

    _MEM = TranslationMemory(default_paths().data_dir / "translation-memory.sqlite")

`cache_path` на неё не влиял. Последствия оказались не только тестовыми:

* `offline_only=true` возвращал перевод, который раньше пришёл **из сети**,
  потому что память читается раньше проверки `offline_only`. На строке в базе
  пользователя это читается как «строгий офлайн» и им является.
* Тесты писали в настоящую базу пользователя. `tests/conftest.py` в проекте нет,
  изоляции не было, и прогоны оставляли мусор:

      ('Hello', 'от сети', 'en', 'ru', 'gtx')
      ('line 0', 'перевод 0', ...)

* Результат теста зависел от того, писал ли предыдущий прогон.

Проверка ниже не читает исходник: она создаёт два переводчика с разными
путями памяти и смотрит, что один не видит записи другого.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translate import Translator  # noqa: E402


def _tr(tmp_path: pathlib.Path, name: str, **kw) -> Translator:
    return Translator(
        target="ru",
        source="en",
        cache_path=tmp_path / f"{name}-cache.sqlite",
        tm_path=tmp_path / f"{name}-tm.sqlite",
        glossary={},
        **kw,
    )


class TestTheMemoryBelongsToTheTranslator:
    def test_a_translator_never_writes_to_the_shared_memory(self, tmp_path):
        tr = _tr(tmp_path, "one")
        tr.via_gtx = lambda text, source=None: "перевод из сети"
        assert tr.translate("Hello") == "перевод из сети"

        shared = pathlib.Path.home() / ".local" / "share" / "kizurium-translator" / "translation-memory.sqlite"
        before = shared.stat().st_size if shared.exists() else None

        _tr(tmp_path, "two").translate("Hello")

        after = shared.stat().st_size if shared.exists() else None
        assert before == after, "переводчик писал в общую память, а не в свою"

    def test_one_translator_cannot_read_another_s_hit(self, tmp_path):
        first = _tr(tmp_path, "a")
        first.via_gtx = lambda text, source=None: "от первого"
        assert first.translate("Hello") == "от первого"

        second = _tr(tmp_path, "b")
        calls: list[str] = []
        second.via_gtx = lambda text, source=None: calls.append(text) or "от второго"

        assert second.translate("Hello") == "от второго"
        assert calls, "второй переводчик прочитал чужую память вместо своего бэкенда"

    def test_two_translators_sharing_a_path_still_share_it(self, tmp_path):
        """Явный путь - это контракт: два переводчика на одном файле делят память."""
        path = tmp_path / "shared-tm.sqlite"
        first = Translator(
            target="ru", source="en",
            cache_path=tmp_path / "c1.sqlite", tm_path=path, glossary={},
        )
        first.via_gtx = lambda text, source=None: "запомнено"
        assert first.translate("Remember me") == "запомнено"

        second = Translator(
            target="ru", source="en",
            cache_path=tmp_path / "c2.sqlite", tm_path=path, glossary={},
        )
        calls: list[str] = []
        second.via_gtx = lambda text, source=None: calls.append(text) or "заново"
        assert second.translate("Remember me") == "запомнено"
        assert not calls, "общая память должна была ответить до бэкенда"

    def test_no_path_resolves_to_the_shared_memory(self, tmp_path):
        """Без `tm_path` переводчик берёт общую память - контракт не изменился.

        Здесь не проверяется перевод: он зависит от того, что лежит в реальной
        базе пользователя, и такой тест был бы недетерминированным. Проверяется
        сам выбор памяти - общая она или нет.
        """
        from kizurium_translator.lexicon import translation_memory as tm_mod

        tr = Translator(
            target="ru", source="en",
            cache_path=tmp_path / "c.sqlite", glossary={},
        )
        assert tr._memory() is tm_mod.default_memory()


class TestOfflineAndTheMemory:
    def test_offline_still_answers_from_a_shared_memory(self, tmp_path):
        """Память под флагом `offline_only` не отключается.

        Она локальная: в ней то, что переводчик уже видел и что пользователь мог
        поправить. Возвращать её - правильное поведение, а не нарушение
        офлайна, поэтому тест фиксирует, что оно не сломано.
        """
        tm = tmp_path / "tm.sqlite"
        online = Translator(
            target="ru", source="en",
            cache_path=tmp_path / "c1.sqlite", tm_path=tm, glossary={},
        )
        online.via_gtx = lambda text, source=None: "ответ сети"
        assert online.translate("Hello") == "ответ сети"

        offline = Translator(
            target="ru", source="en", offline_only=True,
            cache_path=tmp_path / "c2.sqlite", tm_path=tm, glossary={},
        )
        calls: list[str] = []
        offline.via_local = lambda text, source=None, beam_size=1: calls.append(text) or f"лок:{text}"

        assert offline.translate("Hello") == "ответ сети"
        assert not calls, "офлайн пошёл в локальный движок, хотя память уже отвечала"
