"""Automatic tracing of every function call, with nothing written by hand.

The decorator in `trace.py` only covers the functions somebody remembered to
put it on; this covers all of them, which is the difference between a log that
explains a decision and a log that explains the decisions in the parts of the
pipeline nobody instrumented.

`sys.monitoring` (PEP 669) is the mechanism: the interpreter emits an event for
every call, return and exception, to a callback registered against a tool id.
Nothing is patched and nothing is wrapped, so a function cannot be invisible by
being defined somewhere nobody thought to look.

Three things make this usable rather than a way to make the overlay unusable.
Events are never cleared to stop the tracer tracing itself, because clearing the
code object's local events does not mask the global ones and re-arming on the way
out reports every return twice - and the interpreter already suspends events
inside the tool's own callback anyway. The event set is armed once at startup,
since changing it de-optimises instrumented code for hundreds of milliseconds,
which per frame would exceed the trace's value. And events for `site-packages`
are counted and reported but not written out, because without that one frame
emits thousands of lines about Pango and numpy and answers nothing.

Scope: `PY_START` and `PY_RETURN` fire for Python code objects only. Calls into C
- Pango, cairo, the OCR library - need `CALL`, which fires *before* the call and
cannot see the return; `CALL`/`C_RETURN`/`C_RAISE` are a group and enabling any
one enables all three. That is `include_native_calls()`, off by default.
"""

from __future__ import annotations

import logging
import sys
import time
from collections import defaultdict
from typing import Any

from . import trace

# 0 is the debugger, 1 coverage, 2 the profiler, 5 the optimiser. 3 and 4 are
# free, and 3 is what this takes.
TOOL_ID = 3
TOOL_NAME = "kizurium.trace"

# What to watch. LINE and INSTRUCTION are deliberately absent: the language
# reference warns that instrumenting LINE has a large performance impact, and a
# pipeline that takes 7 seconds a frame cannot afford to log every line of one.
_EVENTS = (
    sys.monitoring.events.PY_START
    | sys.monitoring.events.PY_RETURN
    | sys.monitoring.events.PY_UNWIND
)

_STATE: dict[str, Any] = {
    "installed": False,
    "counts": defaultdict(int),
    "calls": 0,
    "started": 0.0,
    "native": False,
    "skip": (),
}


def _ignored_path(filename: str) -> bool:
    """Whether a code object's file is not ours and not worth a line."""
    return (
        not filename
        or filename.startswith("<")
        or "site-packages" in filename
        or "dist-packages" in filename
        or "/lib/python3" in filename
        or "/.venv/" in filename
    )


def _should_log(code: Any) -> bool:
    skip = _STATE["skip"]
    if not skip:
        return True
    name = code.co_name
    return not any(name == s or name.startswith(s) for s in skip)


def install(skip: tuple[str, ...] = (), *, native: bool = False) -> bool:
    """Start tracing every call. Returns whether it is running.

    ``skip`` is a tuple of function-name prefixes to leave out - the logging
    module's own calls, and anything that would otherwise trace the tracer.
    """
    if not trace.enabled():
        return False
    mon = sys.monitoring
    if mon.get_tool(TOOL_ID) is not None:
        # Something took our id, or a previous call is still live. Either way a
        # second registration would raise and take the caller down.
        return _STATE["installed"]
    try:
        mon.use_tool_id(TOOL_ID, TOOL_NAME)
    except ValueError:
        return False

    # Список пропускаемых дополняется, а не заменяется: on_call успевает
    # зарегистрировать свои имена до того, как кто-то включил трассировку, и
    # замена здесь молча вернула бы функции-счётчики в поток строк.
    _STATE["skip"] = tuple(dict.fromkeys(tuple(_STATE["skip"]) + tuple(skip)))
    _STATE["started"] = time.monotonic()
    events = _EVENTS
    if native:
        # A group: asking for one of the three asks for all three.
        events |= (
            mon.events.CALL | mon.events.C_RETURN | mon.events.C_RAISE
        )
        _STATE["native"] = True
    try:
        mon.set_events(TOOL_ID, events)
        mon.register_callback(TOOL_ID, mon.events.PY_START, _on_start)
        mon.register_callback(TOOL_ID, mon.events.PY_RETURN, _on_return)
        mon.register_callback(TOOL_ID, mon.events.PY_UNWIND, _on_unwind)
        if native:
            mon.register_callback(TOOL_ID, mon.events.CALL, _on_native_call)
            mon.register_callback(TOOL_ID, mon.events.C_RETURN, _on_native_return)
            mon.register_callback(TOOL_ID, mon.events.C_RAISE, _on_native_raise)
    except Exception:  # noqa: BLE001
        uninstall()
        return False
    _STATE["installed"] = True
    trace.decide(
        "автотрассировка",
        "включена"
        if not native
        else "включена вместе с вызовами C (громко)",
        # Битовая маска, а не множество: сколько включено событий - это
        # число установленных битов, и len() по int падает.
        events=bin(events).count("1"),
        skip=skip or "нет",
    )
    return True


def uninstall() -> None:
    """Stop tracing. Leaves the tool id free."""
    mon = sys.monitoring
    _STATE["installed"] = False
    if mon.get_tool(TOOL_ID) is None:
        return
    for event in (
        mon.events.PY_START,
        mon.events.PY_RETURN,
        mon.events.PY_UNWIND,
        mon.events.CALL,
        mon.events.C_RETURN,
        mon.events.C_RAISE,
    ):
        try:
            mon.register_callback(TOOL_ID, event, None)
        except Exception:  # noqa: BLE001
            pass
    try:
        mon.set_events(TOOL_ID, mon.events.NO_EVENTS)
        mon.free_tool_id(TOOL_ID)
    except Exception:  # noqa: BLE001
        pass


def installed() -> bool:
    return bool(_STATE["installed"])


def on_call(fn: Any) -> Any:
    """Count a function instead of writing a line for every call.

    For the handful of functions that run tens of thousands of times a frame:
    the count in the frame summary is worth having and fifty thousand lines
    are not. The name goes into the skip list as well, because counting and
    still writing every call out is neither.
    """
    name = getattr(fn, "__name__", "?")
    _STATE["skip"] = tuple(_STATE["skip"]) + (name,)

    def wrap(*a: Any, **kw: Any) -> Any:
        _STATE["counts"][name] += 1
        return fn(*a, **kw)

    wrap.__name__ = name
    wrap.__qualname__ = getattr(fn, "__qualname__", name)
    wrap.__doc__ = getattr(fn, "__doc__", None)
    wrap.__wrapped__ = fn  # type: ignore[attr-defined]
    return wrap


def counts() -> dict[str, int]:
    return dict(_STATE["counts"])


def reset_counts() -> None:
    _STATE["counts"].clear()


def _on_start(code: Any, offset: int) -> None:
    """A Python function is being entered.

    Nothing is switched off here. Events are suspended inside this callback by
    the interpreter, so it cannot recurse into itself, and a log call made from
    here is traced like any other code - which is what it should be, because
    seeing the logger in the trace is how you know the log itself is healthy.
    """
    file = code.co_filename
    if _ignored_path(file):
        # Считать чужой код тоже дорого: один вызов уходит в logging, а тот -
        # в десятки вызовов обработчиков, и счётчик на кадр становится
        # четырёхзначным, ни о чём не говоря. Чужое отфильтровано, своё считается.
        return
    _STATE["calls"] += 1
    if not _should_log(code):
        return
    trace.emit_raw(
        logging.DEBUG,
        f">> {code.co_name}  {file.rsplit('/', 1)[-1]}:{code.co_firstlineno}",
    )


def _on_return(code: Any, offset: int, retval: Any) -> None:
    file = code.co_filename
    if _ignored_path(file) or not _should_log(code):
        return
    trace.emit_raw(
        logging.DEBUG,
        f"<< {code.co_name} = {trace.brief(retval, 60)}",
    )


# Управляющий поток, а не сбой. SystemExit(0) - это нормальный выход, и
# StopIteration - это то, чем заканчивается генератор; оба доходят до сюда
# тем же путём, что и настоящая ошибка.
_NOT_A_CRASH = (SystemExit, StopIteration, GeneratorExit)


def _on_unwind(code: Any, offset: int, exc: Any) -> None:
    file = code.co_filename
    if isinstance(exc, _NOT_A_CRASH):
        if not _ignored_path(file) and _should_log(code):
            trace.emit_raw(
                logging.DEBUG,
                f"<< {code.co_name} {type(exc).__name__}",
            )
        return
    if _ignored_path(file) or not _should_log(code):
        return
    trace.emit_raw(
        logging.ERROR,
        f"!! {code.co_name} unwound: {type(exc).__name__}: {exc}",
    )


def _on_native_call(code: Any, offset: int, callable_: Any, arg0: Any) -> None:
    name = getattr(callable_, "__qualname__", None) or repr(callable_)
    trace.emit_raw(logging.DEBUG, f"-> C {trace.brief(name, 70)}")


def _on_native_return(code: Any, offset: int, callable_: Any, arg0: Any) -> None:
    trace.emit_raw(logging.DEBUG, "   C ok")


def _on_native_raise(code: Any, offset: int, callable_: Any, arg0: Any) -> None:
    trace.emit_raw(logging.ERROR, "   C raised")
