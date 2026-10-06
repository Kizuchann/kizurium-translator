"""Watching what the process does from the outside.

Three things here that no amount of reading the source will tell you, and that
no logging call written into the code would catch either:

``install_command_watch()``
    An audit hook, so *every* process start is recorded - including the ones made
    inside third-party OCR wrappers, and including `shell=True`, which is where
    the interesting ones hide. Nothing is monkeypatched: `subprocess.run`,
    `Popen`, `call` and `check_output` all funnel through `subprocess.Popen`, and
    that emits the audit event.

``install_crash_capture()``
    `faulthandler` on its own file for a native crash, and the thread exception
    hook for the ones that happen in a worker. Without the second, an exception
    in the translation thread prints to stderr and is gone; that is how a
    pipeline goes quiet with no explanation.

``run_summary()``
    One line per frame carrying the whole story. A long pipeline logged at INFO
    produces more lines than anyone reads, and the one thing anyone wants is
    "what happened, and how long it took".

Deliberately not logged: environment *values* (they carry tokens), clipboard
contents, and the bytes a screenshot command wrote - only its length and a
digest of it.
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from . import trace

# Set once. An audit hook cannot be removed once installed, and installing it
# twice would double every line.
_watched = False
_crash_hooks = False
# Куда на��орены обработчики. Путь читается один раз, но если он изменился -
# configure() переставил пути, прогон пошёл в другой каталог - перевооружать
# надо: обработчик, пишущий в прошлый каталог, молчит именно там, где он нужен.
_crash_path_armed: str | None = None

# Commands whose output is a picture or a blob. Logged as a length and a digest,
# never as content.
_BINARY = frozenset({"grim", "spectacle", "gnome-screenshot", "slurp", "hyprctl"})

_CRASH_ENV = "KIZURIUM_TRANSLATOR_CRASH_LOG"


def _crash_path() -> Path | None:
    """Where faulthandler writes, or None if it must not write.

    faulthandler keeps the file descriptor of whatever it is given. If that file
    is closed and its number is reused, the next crash is written into whatever
    opened the number - so this file is opened once, never rotated, and never
    handed to a RotatingFileHandler. It is a separate file for that reason and
    not a stylistic choice.
    """
    override = os.environ.get(_CRASH_ENV, "").strip()
    if override:
        return Path(override)
    try:
        from . import live

        state = getattr(live.PATHS, "state_dir", None)
        if isinstance(state, Path):
            return state / "crash.log"
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / ".local" / "state" / "kizurium-translator" / "crash.log"


def install_command_watch() -> bool:
    """Record every process this one starts. Returns whether it took.

    The hook is global and permanent, which is the point: it is the only way to
    be sure. The cost per event is one function call and a small string build,
    and the events are rare - a handful per frame.
    """
    global _watched
    # Сначала включён ли трассировщик, и только потом «уже установлен». Хук
    # необратим: если он поставлен в этом процессе, любой другой тест в этом
    # же процессе получает True отсюда, и проверка «выключено ли наблюдение»
    # прошла бы наоборот.
    if not trace.enabled():
        # Not an error. Watching is a diagnostic, and a diagnostic nobody asked
        # for in production costs a little on every spawn for nothing.
        return False
    if _watched:
        return True

    def hook(event: str, args: tuple[Any, ...]) -> None:
        # The hook runs inside whatever code caused the event, including code
        # that is already failing. Raising here would replace a useful error
        # with a useless one, so everything below is guarded.
        try:
            if event == "subprocess.Popen":
                executable = args[0] if len(args) > 0 else None
                argv = args[1] if len(args) > 1 else None
                cwd = args[2] if len(args) > 2 else None
                env = args[3] if len(args) > 3 else None
                name = os.path.basename(str(executable or (argv[0] if argv else "?")))
                keys = sorted(env)[:20] if isinstance(env, dict) else []
                trace.watch_command(
                    name,
                    argv=argv,
                    cwd=cwd,
                    env_keys=keys,
                    via="popen",
                )
            elif event == "os.system":
                # bytes, not str - os.system has always passed bytes here.
                trace.watch_command(
                    "os.system",
                    argv=[str(args[0]) if args else None],
                    via="audit",
                )
            elif event in ("os.exec", "os.posix_spawn"):
                # Второе событие на ту же команду, а не дубль первого: первое -
                # что попросили запустить, это - что запустилось на самом деле.
                # При shell=True это разные программы, и именно здесь видно, что
                # просили /bin/sh, а поехало /bin/sh -c "...". На DEBUG, чтобы не
                # удваивать строки: на обычном уровне на каждый запуск нужна
                # одна строка, а обе подробности - при разборе, почему команда
                # сделала не то.
                trace.watch_command(
                    os.path.basename(str(args[0])) if args else event,
                    argv=args[1] if len(args) > 1 else None,
                    via=event,
                )
        except Exception:  # noqa: BLE001
            pass

    try:
        sys.addaudithook(hook)
    except Exception:  # noqa: BLE001
        return False
    _watched = True
    return True


def install_crash_capture() -> bool:
    """Log the two kinds of crash that otherwise leave nothing behind.

    A native crash writes to a file of its own; an exception in a worker thread
    is logged where the pipeline's log can be read. Both are installed once and
    both are safe to call again.
    """
    global _crash_hooks, _crash_path_armed

    def on_thread(args: Any) -> None:
        # Without this the exception is printed to stderr and lost. That is how
        # a pipeline goes quiet mid-frame with nothing in any log to say why.
        try:
            exc_type = getattr(args, "exc_type", None)
            exc_value = getattr(args, "exc_value", None)
            exc_tb = getattr(args, "exc_traceback", None)
            thread = getattr(args, "thread", None)
            name = getattr(thread, "name", "?")
            detail = "".join(
                traceback.format_exception(exc_type, exc_value, exc_tb)
            )
            trace.crash(
                f"исключение в потоке {name}: {exc_type.__name__ if exc_type else '?'}: {exc_value}",
                detail=detail,
                thread=name,
            )
        except Exception:  # noqa: BLE001
            pass
        # Then the default behaviour, so the traceback still reaches the
        # terminal: this replaces the printing, not the crashing.
        try:
            threading.__excepthook__(args)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def on_main(exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
        try:
            detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            trace.crash(
                f"исключение в главном потоке: "
                f"{exc_type.__name__ if exc_type else '?'}: {exc_value}",
                detail=detail,
                thread="MainThread",
            )
        except Exception:  # noqa: BLE001
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    path = _crash_path()
    if _crash_hooks and _crash_path_armed == str(path if path else None):
        return True

    try:
        threading.excepthook = on_thread  # type: ignore[assignment]
        sys.excepthook = on_main
    except Exception:  # noqa: BLE001
        return False

    if path is not None:
        try:
            import faulthandler

            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Перевооружение на другой файл: enable() поверх включённого
            # обработчика - это молчание, а смена каталога при переносе путей
            # происходит именно тогда, когда старый обработчик уже не туда.
            if faulthandler.is_enabled():
                faulthandler.disable()
            # Opened here, held by faulthandler, never closed and never rotated.
            # faulthandler.write is unbuffered on the fd it was given.
            handle = path.open("a", encoding="utf-8")
            # `chain` - это параметр register(), а не enable(); с ним вызов
            # падает целиком и обработчик не встаёт.
            faulthandler.enable(file=handle, all_threads=True)
            trace.crash_handler_ready(str(path))
            _crash_path_armed = str(path)
            atexit.register(_dump_on_exit)
        except Exception as exc:  # noqa: BLE001
            # No faulthandler is much better than a broken overlay - but silence
            # here is what a machine that never crashes looks like, so the
            # failure to arm is itself recorded.
            trace.crash(f"не удалось вооружить обработчик падений: {exc}")
            _crash_path_armed = None

    _crash_hooks = True
    if path is None:
        _crash_path_armed = None
    return True


def _dump_on_exit() -> None:
    """A clean exit still says where it was.

    A trace with no last line in it is a trace that stops somewhere, and the
    place it stops is usually the answer.
    """
    try:
        import faulthandler

        if faulthandler.is_enabled():
            trace.crash("чистый выход", detail=traceback.format_stack())
    except Exception:  # noqa: BLE001
        pass


def note_command_result(
    argv: list[str] | tuple[str, ...] | None,
    returncode: int,
    *,
    stdout: bytes | str | None = None,
    stderr: bytes | str | None = None,
    started: float | None = None,
) -> None:
    """Record how a command ended, once the call site has an answer.

    The audit hook fires before the process starts, so it cannot know any of
    this. It also cannot see commands that were never started at all, which is
    usually the interesting case: a binary that is not installed looks exactly
    like one that has not run yet.
    """
    try:
        name = os.path.basename(str(argv[0])) if argv else "?"
        took = f"{(time.monotonic() - started) * 1000:.0f}ms" if started else "?"
        out_len = len(stdout) if isinstance(stdout, (bytes, str)) else 0
        err_len = len(stderr) if isinstance(stderr, (bytes, str)) else 0
        fields: dict[str, Any] = {
            "rc": returncode,
            "took": took,
            "out": out_len,
            "err": err_len,
        }
        if name in _BINARY and out_len:
            # A screenshot is megabytes of PNG. Its length and digest say
            # "a picture was written" and prove two frames are different,
            # which is all a log can usefully say about it.
            blob = stdout if isinstance(stdout, bytes) else str(stdout).encode()
            fields["sha"] = trace.digest(blob)
        elif stderr:
            fields["err_head"] = str(stderr)[:160]
        if returncode != 0:
            trace.decide(f"команда {name}", f"вернула {returncode}", **fields)
        else:
            trace.watch_command_result(name, **fields)
    except Exception:  # noqa: BLE001
        pass


class FrameSummary:
    """Accumulate one frame's numbers and write them as a single line.

    A pipeline logged at INFO drowns. This is the one line that answers "what
    happened this frame and how long did it take", so it is the line worth
    reading first and the only one that is on by default.
    """

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self._fields: dict[str, Any] = {}
        self._counts: dict[str, int] = {}

    @property
    def dirty(self) -> bool:
        """Whether anything happened worth a line.

        A loop that spins several times a second would otherwise write several
        summary lines a second about nothing at all, which is how a log stops
        being read.
        """
        return bool(self._fields or self._counts)

    def set(self, key: str, value: Any) -> None:
        self._fields[key] = value

    def count(self, key: str, by: int = 1) -> None:
        self._counts[key] = self._counts.get(key, 0) + by

    def merge(self, other: dict[str, Any]) -> None:
        for key, value in other.items():
            if key == "_counts" and isinstance(value, dict):
                for name, n in value.items():
                    self.count(name, int(n))
            elif key not in self._fields:
                self._fields[key] = value

    def emit(self) -> None:
        took = (time.monotonic() - self._t0) * 1000.0
        parts = " ".join(
            f"{k}={trace.brief(v, 40)}" for k, v in self._fields.items()
        )
        counts = " ".join(f"{k}~{n}" for k, n in sorted(self._counts.items()))
        trace.summary(
            f"кадр [{took:.0f}ms]"
            + (f" | {parts}" if parts else "")
            + (f" | {counts}" if counts else "")
        )


def install_all() -> None:
    """Everything, in one call, for the process entry point."""
    trace.reset_claims()
    # Логгер прогревается ДО аудит-хука: иначе его первая строка вызывает
    # импорт движка изнутри хука, и хук входит в себя второй раз на ту же
    # команду. Каждая строка про exec писалась вдвое.
    lg = trace._log()
    lg.setLevel(logging.DEBUG)
    for h in lg.handlers:
        h.setLevel(logging.DEBUG)
    trace.warm()
    install_command_watch()
    install_crash_capture()
    if not trace.enabled():
        logging.getLogger("kizurium.watch").debug(
            "наблюдение выключено: KIZURIUM_TRANSLATOR_TRACE не задан"
        )
