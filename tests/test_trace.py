"""The log line names the code that made the statement.

That is the whole point of the format and it is worth a test, because a
`stacklevel` that is off by one does not fail - it writes `contextlib.py` on
every stage line and looks like a working log that happens to be about the
wrong code.
"""

from __future__ import annotations

import logging

import pytest

from kizurium_translator import logging_setup, trace


@pytest.fixture
def trace_on(monkeypatch, request):
    """Tracing on, with the logger replaced by one that keeps its lines.

    Two things this fixture has to get right, and both of them were wrong the
    first time and failed in a way that looked like a tracing bug rather than a
    fixture bug:

    - the logger handed back is the one the tracing code actually writes to, so
      a test cannot pass against a logger nothing logs to;
    - it is private to this test. `logging.getLogger` is a process-wide
      registry, so a name derived from the log file alone is shared with every
      other test that ever used it, and their lines turn up in this one.
    """
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "1")
    box = _Capture()
    name = f"test-trace-{request.node.name}"

    def fake(_name, logfile, **kwargs):  # noqa: ANN001, ARG001
        # Один и тот же логгер на всё время теста. Каждый вызов get_logger
        # создаёт обработчик заново, и каждая строка стирала бы предыдущую -
        # тест проходил бы, видя только последнюю записанную строку.
        if box.logger is None:
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
            handler = _ListHandler()
            handler.setFormatter(logging_setup.get_trace_formatter())
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            box.logger = logger
        return box.logger

    monkeypatch.setattr(logging_setup, "get_logger", fake)
    # Сброс реестра идёт первым и сам создаёт логгер, а его собственная строка -
    # единственная, которая не относится к тесту, - снимается тут же.
    trace.reset_claims()
    box.lines.clear()
    return box


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        formatter = logging_setup._TolerantFormatter(
            logging_setup.TRACE_FORMAT, datefmt=logging_setup.DATE_FORMAT
        )
        self.lines.append(formatter.format(record))


class _Capture:
    """The lines written so far, and the logger they went to."""

    def __init__(self) -> None:
        self.logger: logging.Logger | None = None

    @property
    def lines(self) -> list[str]:
        if self.logger is None:
            return []
        found = [h for h in self.logger.handlers if isinstance(h, _ListHandler)]
        return found[0].lines if found else []

    def dump(self) -> str:
        return "\n".join(self.lines)


def test_a_decision_names_the_function_that_made_it(trace_on):
    trace.decide("цвет строки", "два цвета")
    line = trace_on.lines[0]
    assert "test_trace.py" in line
    assert "test_a_decision_names_the_function_that_made_it" in line


def test_a_traced_function_names_its_own_caller(trace_on):
    """Entry and exit must not be attributed to the decorator's module."""

    @trace.traced
    def measure(box):
        return {"font": 26}

    measure((451, 240, 1432, 294))
    text = trace_on.dump()
    # Имя квалифицированное, с <locals>: у вложенной функции два одинаковых
    # имени в разных тестах, и в логе они должны различаться.
    assert ".measure(box(451,240,1432,294))" in text
    assert ".measure = {font='26'}" in text
    # Ни одна строка не должна указывать на сам модуль трассировки: файл в
    # строке берётся из записи лога, и если stacklevel off-by-one, там
    # оказывается trace.py вместо кода, который вызвал.
    assert "/trace.py:" not in text, text


def test_a_traced_function_reports_its_arguments_and_its_result(trace_on):
    @trace.traced
    def measure(box, n=3):
        return {"font": 26}

    measure((451, 240, 1432, 294), n=7)
    text = trace_on.dump()
    assert "box(451,240,1432,294)" in text
    assert "n=7" in text
    assert "font='26'" in text


def test_a_traced_function_logs_the_exception_and_reraises(trace_on):
    @trace.traced
    def boom(box):
        raise ValueError("нет чернил")

    with pytest.raises(ValueError):
        boom((1, 2, 3, 4))
    text = trace_on.dump()
    assert ".boom raised ValueError" in text
    assert "нет чернил" in text


def test_a_stage_names_the_caller_not_contextlib(trace_on):
    """contextlib sits between stage() and the caller, and it is not the answer."""
    with trace.stage("разбор кадра", ws=12):
        pass
    text = trace_on.dump()
    assert "== разбор кадра START" in text
    assert "contextlib.py:" not in text, text
    assert "test_a_stage_names_the_caller_not_contextlib" in text


def test_a_stage_carries_its_fields_and_its_duration(trace_on):
    with trace.stage("разбор кадра", ws=12):
        pass
    line = trace_on.dump().splitlines()[0]
    assert "ws=12" in line
    assert "ms]" in trace_on.dump()


def test_a_refused_claim_names_the_loser_and_the_winner(trace_on):
    """The whole reason for the registry: both sides of the decision, in order."""
    assert trace.claim("box(451,240)", "make_block") is True
    assert trace.claim("box(451,240)", "ocr_fill_vertical_gaps") is False
    text = trace_on.dump()
    assert "claim 'box(451,240)' to make_block" in text
    assert "REFUSED" in text
    assert "already held by make_block" in text
    assert text.index("to make_block") < text.index("REFUSED")


def test_claims_are_answered_correctly_even_without_tracing(monkeypatch):
    """Выключенная трассировка не имеет права менять ответ - реестр решает."""
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "")
    trace.reset_claims()
    assert trace.claim("box(1,1)", "first") is True
    assert trace.claim("box(1,1)", "second") is False


def test_releasing_a_claim_lets_the_next_one_in(trace_on):
    assert trace.claim("box(1,1)", "first") is True
    trace.release("box(1,1)", "first")
    assert trace.claim("box(1,1)", "second") is True
    assert trace.claims() == {"box(1,1)": "second"}


def test_claims_are_for_one_frame_only(trace_on):
    """Иначе отказы второго кадра выглядят как contention, когда его не было."""
    trace.claim("box(1,1)", "first")
    trace.reset_claims()
    assert trace.claims() == {}


def test_a_line_without_an_order_number_still_formats(trace_on):
    """A missing cosmetic field must cost the field, not the line."""
    trace.decide("шрифт", "кегль 26 по толщине штриха")
    line = trace_on.lines[0]
    assert line.strip()
    assert "кегль 26" in line


def test_nothing_is_written_when_tracing_is_off(monkeypatch):
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "")
    assert trace.enabled() is False


def test_tracing_is_on_for_the_usual_spellings_of_yes(monkeypatch):
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", value)
        assert trace.enabled() is True, value
    for value in ("0", "no", "false", ""):
        monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", value)
        assert trace.enabled() is False, value


def test_brief_keeps_a_box_a_box(monkeypatch):
    assert trace.brief((451, 240, 1432, 294)) == "box(451,240,1432,294)"


def test_brief_keeps_the_text_and_drops_the_rest(monkeypatch):
    out = trace.brief({"text": "Kills:", "kind": "ui", "conf": 99.8, "junk": "x" * 400})
    assert "Kills:" in out
    assert "x" * 400 not in out


def test_brief_of_a_big_collection_does_not_dump_it(monkeypatch):
    out = trace.brief(list(range(500)))
    assert len(out) <= 121
