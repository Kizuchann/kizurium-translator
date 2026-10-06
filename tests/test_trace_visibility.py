"""Логирование, которое молчит, хуже отсутствия логирования.

Здесь было два дефекта, и оба молчали.

Первый: событий на команду два - `subprocess.Popen` (что попросили запустить)
и `os.posix_spawn` (что запустилось на самом деле). Хук честно логировал оба, а
строки выглядели одинаково, и это читалось как "строка пишется дважды". При
shell=True это вообще разные программы, так что пара полезна; различать надо
меткой, а не убирать.

Второй: в trace.py не был импортирован sys, и NameError внутри _emit
проглатывался. Каждая строка выходила пустой, тесты честно сообщали "лог пуст",
и это было правдой и бесполезно. Теперь такая поломка один раз пишется в
stderr - не в тот лог, который сломан.
"""

from __future__ import annotations

import logging
import subprocess

import pytest

from kizurium_translator import logging_setup, trace, watch


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(logging_setup.get_trace_formatter().format(record))


@pytest.fixture
def tracer(monkeypatch, request):
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "1")
    logger = logging.getLogger(f"test-loud-{request.node.name}")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = _ListHandler()
    handler.setFormatter(logging_setup.get_trace_formatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    monkeypatch.setattr(logging_setup, "get_logger", lambda *a, **k: logger)
    return handler


def _need_hook() -> None:
    """Аудит-хук необратим: поставил - на весь процесс.

    Поэтому тест, который ставит его, проходит только один раз за прогон, а
    остальные пропускаются. Раньше это делали через skip, и два теста молча
    проходили, ни разу не проверив ничего; здесь пропуск виден в отчёте.
    """
    if not watch.install_command_watch():
        pytest.skip("аудит-хук уже установлен в этом процессе")
@pytest.mark.needs_host


def test_a_command_writes_one_line_at_info_and_a_second_at_debug(tracer):
    _need_hook()
    tracer.lines.clear()
    subprocess.run(["/bin/true"], capture_output=True)
    text = "\n".join(tracer.lines)
    assert text.count("via popen") == 1, text
    assert text.count("posix_spawn") == 1, text


def test_the_two_lines_say_different_things(tracer):
    """«Попросили» и «запустилось» - разные вопросы с разными ответами."""
    _need_hook()
    tracer.lines.clear()
    subprocess.run("/bin/true", shell=True, capture_output=True)
    text = "\n".join(tracer.lines)
    assert "via popen" in text, "не записано, что просили"
    assert "posix_spawn" in text, "не записано, что запустилось"


def test_a_broken_logger_says_so_once_instead_of_saying_nothing(monkeypatch, capsys):
    """Молчание - худший вид поломки: снаружи оно неотличимо от нормы."""
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "1")

    def broken(*a, **k):
        raise RuntimeError("лог недоступен")

    monkeypatch.setattr(logging_setup, "get_logger", broken)
    monkeypatch.setattr(trace, "_FAILED_ONCE", False)
    trace.decide("проверка", "логгер сломан")
    trace.decide("проверка", "и ещё раз")
    err = capsys.readouterr().err
    assert "логирование не работает" in err
    # Один раз, а не на каждую строку: иначе поломка забивает stderr.
    assert err.count("логирование не работает") == 1
