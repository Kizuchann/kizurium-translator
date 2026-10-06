"""`live/` должен быть слоями, а не одним файлом.

`worker()` перестал быть тысячами строк, а цикл
кадра читался как конвейер:

    capture -> detect change -> read dirty regions -> reconcile blocks
            -> schedule translation -> update state -> request redraw

Тест написан до правки и падает на текущем коде.

Ограничение этого файла: он проверяет форму, а не смысл. Формально
переименованный монолит такой тест пройдёт. Поэтому рядом с проверками формы
обязателен живой прогон на экране.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import live  # noqa: E402

LIVE = pathlib.Path(__file__).resolve().parent.parent / "src" / "kizurium_translator" / "live"

# Целевая структура пакета. `runtime.py` в этом списке нет: его
# содержимое - это `Config -> Services`, а не слой кадра.
PLAN_MODULES = (
    "__init__.py",
    "session.py",
    "state.py",
    "change.py",
    "tracking.py",
    "reconcile.py",
    "scheduler.py",
)

# Модуль не должен превышать этот размер: пакет разделён по
# ответственности, и файл в полторы тысячи строк означает, что
# ответственность не разделена.
MAX_MODULE_LINES = 1200


def _module_lines() -> dict[str, int]:
    return {p.name: len(p.read_text().splitlines()) for p in sorted(LIVE.glob("*.py"))}


class TestTheLayoutMatchesThePlan:
    """конкретные имена модулей, а не «модули по смыслу»."""

    def test_the_planned_modules_exist(self):
        missing = [n for n in PLAN_MODULES if not (LIVE / n).is_file()]
        assert missing == [], f"нет обязательных модулей: {missing}"

    def test_no_module_outgrew_its_share(self):
        too_big = {
            name: n for name, n in _module_lines().items() if n > MAX_MODULE_LINES
        }
        assert too_big == {}, f"модуль больше {MAX_MODULE_LINES} строк: {too_big}"


class TestWorkerIsAnOrchestration:
    """«`worker()` не должен содержать тысячи строк»."""

    def test_it_is_shorter_than_a_thousand_lines(self):
        n = len(inspect.getsource(live.worker).splitlines())
        assert n < 1000, f"worker() = {n} строк"

    def test_the_frame_cycle_does_not_contain_a_tangle_of_continues(self):
        """Один `continue` на ветку решения - нормально. Двадцать - это
        признак того, что решения и действия смешаны в одном теле."""
        source = inspect.getsource(live.worker)
        n = source.count("continue")
        assert n <= 12, f"{n} continue в worker(): тело не читается как конвейер"


class TestTheCycleReadsAsAPipeline:
    """перечисляет этапы кадра. Каждый должен быть виден в коде."""

    STAGES = (
        # capture: снимок экрана
        "grim_region(",
        # detect change: что-то изменилось
        "last_clean is None",
        # read dirty regions: адресный перечит
        "incremental",
        # reconcile blocks: сопоставление с прошлым кадром
        "track_blocks(",
        # schedule translation: порция на перевод
        "translate_many(",
        # update state / request redraw: запись результата
        "state.set(",
    )

    @pytest.mark.parametrize("marker", STAGES)
    def test_the_stage_is_present(self, marker):
        assert marker in inspect.getsource(live.worker), (
            f"этап кадра не виден в worker(): {marker}"
        )


class TestTheDecisionsAreNotInsideTheLoopBody:
    """Решение «нужен ли кадр» отделено от действия «взять кадр».

    Пока решения и действия в одном теле, цикл не читается: чтобы понять
    порядок шагов, приходится держать в голове все 46 выходов. Здесь просто
    фиксируется сам факт разделения - появление функции-решения.
    """

    def test_there_is_a_function_that_decides_whether_to_read(self):
        """Решение - отдельная функция, а не вычисление в теле цикла.

        Ищется **вызов** функции решения в теле `worker()` и её наличие в
        пакете. Раньше здесь стояло `{n.name для всех узлов ast.walk(...)}`:
        у большинства узлов атрибута `name` нет, и проверка падала с
        AttributeError на любом коде - измерить фазу было нечем. Определение
        при этом лежит в соседнем модуле (`gate.decide_frame`,
        `scene.decide_scene`), и в исходнике `worker()` его не видно: видно
        только место вызова, а вызов - и есть то, что проверяется. Иначе
        проверка прошла бы на функции, которую никто не зовёт.
        """
        tree = ast.parse(inspect.getsource(live.worker))
        called = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert any(
            n.startswith(("decide", "_decide")) for n in called
        ), "worker() не зовёт функцию решения: тело само вычисляет все условия"
        assert any(
            n.startswith(("decide", "_decide")) for n in dir(live)
        ), "в пакете live нет функции решения"

    def test_the_decision_function_is_small(self):
        """Решение должно быть коротким. Функция на 600 строк - это тот же
        монолит под другим именем, и такой тест обманул бы ровно так же,
        как предыдущие обманки в этом проекте."""
        found = [
            name
            for name in dir(live)
            if name.startswith("_decide") or name.startswith("decide")
        ]
        assert found, "нет функции решения"
        for name in found:
            n = len(inspect.getsource(getattr(live, name)).splitlines())
            assert n < 300, f"{name}() = {n} строк: это не решение, а переезд монолита"
