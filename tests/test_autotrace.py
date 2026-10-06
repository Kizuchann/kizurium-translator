"""Automatic tracing of every call, with nothing written by hand.

The point is coverage: a function nobody thought to instrument still shows up,
with its file, its line and its result.
"""

from __future__ import annotations

import logging
import sys

import pytest

from kizurium_translator import autotrace, logging_setup, trace


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(logging_setup.get_trace_formatter().format(record))


@pytest.fixture
def tracer(monkeypatch, request):
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "1")
    logger = logging.getLogger(f"test-autotrace-{request.node.name}")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = _ListHandler()
    handler.setFormatter(logging_setup.get_trace_formatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    monkeypatch.setattr(logging_setup, "get_logger", lambda *a, **k: logger)
    handler.lines.clear()
    yield handler
    autotrace.uninstall()


# Модуль, а не функция в тесте: функция в тесте лежит в test_autotrace.py, и
# имена её окажутся в проверках вместо проверяемого кода.
def _leaf(box, n=3):
    return {"font": 26, "kind": "ui"}


def _middle(img, hint=None):
    return {"lines": [_leaf((1, 2, 3, 4))] * 2}


def _outer(region):
    return _middle("png-bytes", hint="ws12")


def test_a_function_nobody_instrumented_is_still_traced(tracer):
    assert autotrace.install() is True
    _outer("region")
    text = "\n".join(tracer.lines)
    for name in ("_leaf", "_middle", "_outer"):
        assert f">> {name}  {__file__.rsplit('/', 1)[-1]}:" in text, name


def test_a_return_value_is_recorded(tracer):
    autotrace.install()
    _leaf((451, 240, 1432, 294))
    text = "\n".join(tracer.lines)
    assert "<< _leaf = " in text
    assert "font" in text


def test_the_line_carries_the_function_and_its_line(tracer):
    """Файл и номер строки приходят из кодового объекта, а не пишутся руками."""
    autotrace.install()
    _outer("region")
    text = "\n".join(tracer.lines)
    here = __file__.rsplit("/", 1)[-1]
    line_no = _leaf.__code__.co_firstlineno
    assert f"{here}:{line_no}" in text


def test_third_party_code_is_counted_but_not_written_out(tracer):
    """Иначе один кадр пишет тысячи строк про Pango и numpy."""
    autotrace.install()
    before = len(tracer.lines)
    sorted([3, 1, 2], key=str)
    _leaf((1, 2, 3, 4))
    text = "\n".join(tracer.lines)
    assert "_leaf" in text
    assert "sorted" not in text


def test_installing_and_removing_gives_the_tool_id_back(tracer):
    assert autotrace.install() is True
    assert autotrace.installed() is True
    autotrace.uninstall()
    assert autotrace.installed() is False
    # Инструмент снова свободен - иначе второй запуск в том же процессе
    # молча не включился бы.
    assert sys.monitoring.get_tool(autotrace.TOOL_ID) is None
    assert autotrace.install() is True


def test_it_does_not_trace_itself_into_a_loop(tracer):
    """Обратный случай сработает за секунды и уронит оверлей."""
    autotrace.install()
    _leaf((1, 2, 3, 4))
    assert autotrace._STATE["calls"] < 200


def test_the_skipped_names_are_not_traced(tracer):
    autotrace.install(skip=("_leaf",))
    _outer("region")
    text = "\n".join(tracer.lines)
    assert ">> _middle" in text
    assert ">> _leaf" not in text


def test_a_counter_decorator_counts_without_a_line_per_call(tracer):
    @autotrace.on_call
    def step(value):
        return value * 2

    autotrace.install()
    for i in range(50):
        step(i)
    text = "\n".join(tracer.lines)
    assert ">> step" not in text
    assert autotrace.counts()["step"] == 50


def test_a_counter_keeps_the_function_intact():
    @autotrace.on_call
    def step(value, factor=2):
        """docstring stays."""
        return value * factor

    assert step(3) == 6
    assert step.__name__ == "step"
    assert step.__doc__ == "docstring stays."
    assert step.__wrapped__.__name__ == "step"


def test_counting_is_resettable(tracer):
    counted = autotrace.on_call(_leaf)
    autotrace.reset_counts()
    counted((1, 2, 3, 4))
    assert autotrace.counts().get("_leaf") == 1
    autotrace.reset_counts()
    assert autotrace.counts() == {}


def test_an_exception_inside_a_traced_function_is_recorded(tracer):
    autotrace.install()

    def kaboom():
        raise ValueError("нет чернил")

    with pytest.raises(ValueError):
        kaboom()
    text = "\n".join(tracer.lines)
    assert "kaboom" in text
    assert "нет чернил" in text


def test_tracing_stays_off_without_the_flag(monkeypatch):
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "")
    assert autotrace.install() is False
    assert autotrace.installed() is False


def test_the_tool_id_is_one_the_interpreter_reserves_for_this(monkeypatch):
    """0 - отладчик, 1 - coverage, 2 - профилировщик, 5 - оптимизатор."""
    monkeypatch.setattr(sys.monitoring, "use_tool_id", _reject)
    assert autotrace.install() is False


def _reject(tool_id, name):
    raise ValueError(f"invalid tool {tool_id}")
