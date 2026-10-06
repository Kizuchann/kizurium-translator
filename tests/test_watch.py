"""Watching from the outside: every command, every crash, one line per frame.

These are the things a logging call written inside the code cannot catch, which
is why they are tested against the real interpreter rather than mocked.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from kizurium_translator import logging_setup, trace, watch


@pytest.fixture
def tracer(monkeypatch, request):
    """Tracing on, with the logger replaced by one that keeps its lines."""
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "1")
    name = f"test-watch-{request.node.name}"
    logger = logging.getLogger(name)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = _ListHandler()
    handler.setFormatter(logging_setup.get_trace_formatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    monkeypatch.setattr(logging_setup, "get_logger", lambda *a, **k: logger)
    trace.reset_claims()
    handler.lines.clear()
    return handler


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(
            logging_setup.get_trace_formatter().format(record)
        )


def test_the_audit_hook_sees_a_command_no_logging_call_was_written_for(tracer):
    """The point of an audit hook: coverage nobody had to remember."""
    if not watch.install_command_watch():
        pytest.skip("аудит-хук уже установлен в этом процессе")
    subprocess.run(["/bin/true"], capture_output=True)
    text = "\n".join(tracer.lines)
    assert "exec true" in text
    assert "via popen" in text


def test_the_audit_hook_sees_a_shell_form_command(tracer):
    """Where the interesting ones hide: shell=True makes the program a shell."""
    if not watch.install_command_watch():
        pytest.skip("аудит-хук уже установлен в этом процессе")
    subprocess.run("/bin/true", shell=True, capture_output=True)
    assert "exec sh" in "\n".join(tracer.lines)


def test_a_command_result_records_the_exit_code_and_the_time(tracer):
    watch.note_command_result(
        ["hyprctl", "clients"], 0, stdout=b"{}\n", started=0.0
    )
    line = "\n".join(tracer.lines)
    assert "done hyprctl" in line
    assert "rc=0" in line


def test_a_failed_command_is_a_decision_and_not_just_a_line(tracer):
    """Код возврата, отличный от нуля, - это повод искать причину, а не факт."""
    watch.note_command_result(["grim", "shot.png"], 1, stderr=b"No such device\n")
    line = "\n".join(tracer.lines)
    assert "decide команда grim" in line
    assert "вернула 1" in line
    assert "No such device" in line


def test_a_picture_is_logged_as_a_length_and_a_digest_not_as_bytes(tracer):
    """PNG в логе - это не диагностика, это мусор в файле."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 5000
    watch.note_command_result(["grim", "-o", "a.png"], 0, stdout=png)
    line = "\n".join(tracer.lines)
    assert "sha=" in line
    assert "out=5008" in line
    assert "PNG" not in line


def test_the_same_picture_twice_gives_the_same_digest():
    assert trace.digest(b"abc") == trace.digest(b"abc")
    assert trace.digest(b"abc") != trace.digest(b"abd")
    assert len(trace.digest(b"abc")) == 16


def test_an_exception_in_a_worker_thread_is_logged(tracer, monkeypatch):
    """Без этого исключение уходит в stderr и пропадает.

    Имя функции в строке лога здесь - имя самого хука, и это правильно: у
    исключения из потока нет вызывающего кода, который стоило бы назвать.
    """
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_CRASH_LOG", "/tmp/kizurium-test-crash.log")
    assert watch.install_crash_capture() is True
    done = threading.Event()

    def boom():
        raise ValueError("нет чернил")

    def run():
        try:
            boom()
        finally:
            done.set()

    thread = threading.Thread(target=run, name="worker-test", daemon=True)
    thread.start()
    done.wait(5)
    thread.join(5)
    text = "\n".join(tracer.lines)
    assert "исключение в потоке worker-test" in text
    assert "ValueError" in text
    assert "нет чернил" in text
    # Имя потока - в обеих строках: в сообщении и в поле.
    assert "worker-test" in text
    assert "on_thread" in text


def test_the_crash_handler_is_armed_and_says_where(tracer, tmp_path, monkeypatch):
    """Неудачно поставленный обработчик выглядит ровно как машина без падений."""
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_CRASH_LOG", str(tmp_path / "crash.log"))
    watch.install_crash_capture()
    assert "crash handler armed" in "\n".join(tracer.lines)
    assert str(tmp_path / "crash.log") in "\n".join(tracer.lines)


def test_a_frame_summary_is_one_line(tracer):
    """Один экран на кадр: остальное - в DEBUG."""
    summary = watch.FrameSummary()
    summary.set("cards", 8)
    summary.set("lang", "en")
    summary.count("translate")
    summary.count("translate")
    summary.emit()
    lines = [line for line in tracer.lines if "==" in line and "кадр" in line]
    assert len(lines) == 1
    assert "карточек" not in lines[0]
    assert "cards=8" in lines[0]
    assert "translate~2" in lines[0]
    assert "мс]" in lines[0] or "ms]" in lines[0]


def test_a_frame_summary_can_absorb_another(tracer):
    """Раскладка карточек и перевод считаются отдельно и сводятся в одну строку."""
    layout = watch.FrameSummary()
    layout.count("cards", 3)
    words = watch.FrameSummary()
    words.set("words", 120)
    words.merge({"cards~": 0})
    summary = watch.FrameSummary()
    summary.merge({"cards": 3, "_counts": layout._counts})
    summary.merge({"words": 120})
    summary.emit()
    line = [x for x in tracer.lines if "кадр" in x][0]
    assert "cards=3" in line
    assert "words=120" in line
    assert "cards~3" in line


def test_watching_stays_off_when_tracing_is_off(monkeypatch):
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_TRACE", "")
    assert watch.install_command_watch() is False


def test_installing_everything_twice_is_safe(tracer):
    assert watch.install_crash_capture() is True
    assert watch.install_crash_capture() is True
    assert sys.excepthook is not None
