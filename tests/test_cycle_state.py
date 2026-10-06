"""Состояние цикла должно быть объектом, а не полем на функции.

`worker()` раньше хранил десять счётчиков на самом объекте функции:
`worker._last_dirty_probe = now`, `worker._force_retry = True` и так далее -
43 обращения в исходнике. Такое состояние не видно ни тестам, ни
типизатору, ни тому, кто читает `worker()`: оно переживает перезапуск сессии
внутри процесса и не имеет никакого отношения к циклу, который его ставит.

обещает «нет глобального mutable state». Поле на функции — это
ровно такое состояние, просто спрятанное от линтера.

Проверки ниже падают на коде до правки.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import live  # noqa: E402
from kizurium_translator.live import state as state_mod  # noqa: E402

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "kizurium_translator"

# Поля, которые раньше висели на функции. Имена и значения по умолчанию взяты
# из прежних `getattr(worker, "_x", default)` - поведение не меняется.
EXPECTED_DEFAULTS = {
    "last_dirty_probe": 0.0,
    "dirty_echo": 0,
    "dirty_empty": 0,
    "last_echo_clean": 0.0,
    "soft_empty": 0,
    "prev_mode": None,
    "last_line_n": 0,
    "ui_scene_change": False,
    "force_retry": False,
    "partial_streak": 0,
}


def _source_files() -> list[pathlib.Path]:
    return sorted(SRC.rglob("*.py"))


class TestNoStateIsKeptOnAFunctionObject:
    """Поле на функции невидимо для всего, кроме функции."""

    # `functools.wraps` и подражание ему присваивают служебные атрибуты
    # обёртке. Это не состояние, а метаданные функции, и они обязательны.
    WRAPPER_ATTRS = frozenset({"__name__", "__qualname__", "__doc__", "__wrapped__"})

    def test_nothing_assigns_to_a_function_defined_in_this_project(self):
        offenders: list[str] = []
        for path in _source_files():
            tree = ast.parse(path.read_text())
            funcs = {
                n.name: n.lineno
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if not isinstance(node.ctx, ast.Store):
                    continue
                if node.attr in self.WRAPPER_ATTRS:
                    continue
                name = getattr(node.value, "id", None)
                if name in funcs:
                    offenders.append(
                        f"{path.relative_to(SRC)}:{node.lineno} "
                        f"{name}.{node.attr} = (функция объявлена на строке {funcs[name]})"
                    )
        assert offenders == [], "\n".join(offenders)

    def test_the_worker_function_object_carries_no_attributes(self):
        """Проверка на живой функции, а не только по исходнику."""
        for name in EXPECTED_DEFAULTS:
            assert not hasattr(live.worker, name), (
                f"worker.{name} существует: состояние висит на функции"
            )


class TestTheCycleStateIsAnObject:
    """Состояние цикла объявлено явно и имеет те же значения по умолчанию."""

    def test_it_exists(self):
        assert hasattr(state_mod, "CycleCounters"), "нет объекта состояния цикла"

    def test_the_defaults_match_the_old_getattr_fallbacks(self):
        counters = state_mod.CycleCounters()
        for name, want in EXPECTED_DEFAULTS.items():
            got = getattr(counters, name)
            assert got == want, f"{name}: было по умолчанию {want!r}, стало {got!r}"

    def test_it_starts_from_zero_for_every_session(self):
        """Старая версия переживала перезапуск сессии в том же процессе:
        первый кадр новой сессии видел счётчики прошлой."""
        first = state_mod.CycleCounters()
        first.force_retry = True
        first.partial_streak = 7
        second = state_mod.CycleCounters()
        assert second.force_retry is False
        assert second.partial_streak == 0


class TestTheWorkerUsesTheObject:
    """Не только нет полей на функции - но и сам цикл ими пользуется."""

    def test_the_worker_body_mentions_the_counter_object(self):
        import inspect

        body = inspect.getsource(live.worker)
        assert "CycleCounters(" in body, "worker не создаёт состояние цикла"

    @pytest.mark.parametrize("name", sorted(EXPECTED_DEFAULTS))
    def test_no_counter_is_still_reached_through_the_function(self, name):
        import inspect

        body = inspect.getsource(live.worker)
        assert f"worker._{name}" not in body, (
            f"worker._{name} осталось в коде: состояние всё ещё на функции"
        )
