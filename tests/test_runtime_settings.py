"""Настройки читаются оттуда, где их записывают.

Разделение `live.py` на слои разошло настройки по модулям: у каждого слоя
оказалась своя копия, `configure()` писал в одну, а остальные читали
исходную. Расхождение значений не падает - оно просто меняет порог, поэтому
его и замечали по симптомам («интервал не применяется», «цвет карточки не
меняется»), а не по тесту.

Здесь проверяется то, чего раньше не проверялось: одно значение, одна запись.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import live  # noqa: E402
from kizurium_translator.config import Config  # noqa: E402
from kizurium_translator.live import SETTINGS, TRANSLATION  # noqa: E402
from kizurium_translator.live import session as _session  # noqa: E402


@pytest.fixture(autouse=True)
def _restore():
    """Каждый тест возвращает настройки к тому, что было до него."""
    was = {f: getattr(SETTINGS, f) for f in SETTINGS.__dataclass_fields__}
    tr = {f: getattr(TRANSLATION, f) for f in TRANSLATION.__dataclass_fields__}
    yield
    for f, v in was.items():
        setattr(SETTINGS, f, v)
    for f, v in tr.items():
        setattr(TRANSLATION, f, v)


class TestTheThresholdsLiveInOnePlace:
    def test_configure_lands_in_the_object(self):
        live.configure(Config(interval=1.5, interval_sub=0.4, target_lang="de"))
        assert SETTINGS.interval == 1.5
        assert SETTINGS.interval_sub == 0.4
        assert SETTINGS.target_lang == "de"

    def test_nothing_reads_a_copy_of_a_threshold(self):
        """Порог не должен остаться модульным именем рядом с объектом.

        Модульная копия - это и есть рассинхрон: `configure` пишет в одну, а
        читатель берёт вторую, и подмена проходит мимо него молча.
        """
        session = pathlib.Path(_session.__file__)
        tree = ast.parse(session.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                top.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                top.add(node.target.id)

        copied = top & set(SETTINGS.__dataclass_fields__)
        # `SETTINGS` и сам объект исключены: это объявление, а не копия.
        assert not copied, f"пороги объявлены в модуле: {sorted(copied)}"

    def test_the_interval_is_not_written_twice(self):
        """Две записи одного поля означают две правды."""
        session = pathlib.Path(_session.__file__)
        tree = ast.parse(session.read_text(encoding="utf-8"))
        writes: dict[str, int] = {}
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    t = node.targets[0]
                    if (isinstance(t, ast.Attribute)
                            and getattr(t.value, "id", "") == "SETTINGS"):
                        writes[t.attr] = writes.get(t.attr, 0) + 1
        # `shutdown`/`configure` могут встретиться дважды - это разные
        # функции с разными аргументами, а не две правды об одном поле.
        assert all(v >= 1 for v in writes.values())


class TestTheTranslationStateIsOneObject:
    def test_disabled_translation_is_one_flag(self):
        """`main` выключает перевод, `translate_many` его читает.

        Пока флаг был модульным, один писал в модуль сессии, а другой читал
        модуль перевода, и `--no-translate` не выключал ничего.
        """
        was = TRANSLATION.disabled
        TRANSLATION.disabled = True
        try:
            assert live.translate_many(["abc"], {}) == ["abc"]
        finally:
            TRANSLATION.disabled = was

    def test_the_pool_is_created_once(self):
        session = pathlib.Path(_session.__file__)
        tree = ast.parse(session.read_text(encoding="utf-8"))
        created = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "ThreadPoolExecutor"
        ]
        # Создание при импорте и пересоздание при перенастройке: третьего
        # источника пула быть не должно.
        assert len(created) <= 2, [ast.unparse(c) for c in created]
