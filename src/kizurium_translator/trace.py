"""Automatic tracing: what ran, in what order, and who decided.

The engine is one long pipeline - capture, recognise, group, measure, translate,
lay out, draw - and every defect worth finding lives in a *decision* made
somewhere inside it. The question is never "is this code correct", it is "which
of the things that could have claimed this text claimed it first, and what did
the others do about it". Answering that from the source meant reading the whole
pipeline per question; answering it from the log takes one grep.

So nothing here is written by hand at each call site. Three pieces:

``@traced``
    On a function: entry with its arguments, exit with its return value, the
    exception if it raised, and how long it took. File, line, function and
    thread come from the log record itself rather than from the message, so the
    wrapper never has to describe where it is.

``decide()``
    For a choice that could reasonably have gone another way: records the
    candidates and the verdict, which is the part that cannot be recovered from
    the code afterwards.

``claim()``
    A registry of who took what. Two passes both see the same box; the first call
    returns True and is logged as the winner, the second False and logged as the
    loser. Without this, "the card vanished" has no cause - the pass that dropped
    it and the pass that dropped it *first* look identical from the outside.

Everything is off unless ``KIZURIUM_TRANSLATOR_TRACE`` is set, and the decorator
costs one dict lookup while it is off.
"""

from __future__ import annotations

import functools
import itertools
import logging
import os
import sys
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import logging_setup

# Every line carries this. Threads interleave, and without a counter the only
# way to know what came first is to trust the timestamp's resolution.
_SEQ = itertools.count(1)
_SEQ_LOCK = threading.Lock()

# Guards the claim registry. Two threads claiming the same text is not a
# mistake, it is the normal case, and the registry has to survive it.
_CLAIM_LOCK = threading.Lock()
_CLAIMS: dict[str, str] = {}

# How much of a value goes into the log. A box's repr is a screenful and a
# screenful per call turns the log into the thing it was made to avoid.
_MAX = 120

# Готовится ли путь для логов. См. _warm().
_WARMED = False
_PATHS: Any = None

# Сообщать ли о поломке самого логирования. Один раз.
_FAILED_ONCE = False


def warm() -> None:
    """Resolve the log path before anything logs. See _warm()."""
    _warm()


def enabled() -> bool:
    return os.environ.get("KIZURIUM_TRANSLATOR_TRACE", "").strip().lower() not in (
        "",
        "0",
        "no",
        "false",
    )


def next_seq() -> int:
    """The next number in this process's total order of events."""
    with _SEQ_LOCK:
        return next(_SEQ)


def brief(value: Any, limit: int = _MAX) -> str:
    """A value as one readable line, never a screenful.

    Not repr: the tuples that come out of this engine are boxes and quad points,
    and a box's repr is four numbers that everyone reading the line already
    knows are a box. What is missing from the line is the *content*, so that is
    what is kept.
    """
    if isinstance(value, str):
        text = value
    elif isinstance(value, (list, tuple)) and all(
        isinstance(v, (int, float)) and len(str(v)) <= 6 for v in value
    ) and 2 <= len(value) <= 4:
        text = "box(" + ",".join(str(v) for v in value) + ")"
    elif isinstance(value, dict):
        keep = ("text", "source", "kind", "conf", "lang", "engine", "font", "angle")
        parts = [f"{k}={brief(value[k], 40)!r}" for k in keep if k in value]
        text = "{" + " ".join(parts) + "}" if parts else f"<dict {len(value)}>"
    elif isinstance(value, (list, tuple, set, frozenset)):
        # Элементы в кавычках: список команды без них читается как «4 grim»
        # вместо «4, /usr/bin/grim, -l, 0,...».
        head = ", ".join(repr(v)[:40] for v in list(value)[:3])
        text = f"[{len(value)}] {head}"
    else:
        text = repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _warm() -> None:
    """Import the engine once, up front, so the first log line is cheap.

    This is not a micro-optimisation. The logger resolves its path through
    live.PATHS, and live imports this module, so the import has to be deferred -
    which meant it happened inside whatever called the first log line. When
    that first line came from an audit hook, the import ran *inside the hook*,
    and the hook was then entered a second time for the same command: every
    exec line in the trace was written twice, and a four-times screenshot burst
    came out as four identical lines rather than one.

    Doing it here, before any hook is installed, removes the whole class of it.
    """
    global _WARMED
    if _WARMED:
        return
    _WARMED = True
    try:
        from . import live  # local: live imports this module at import time

        _PATHS = live.PATHS
    except Exception:  # noqa: BLE001
        _PATHS = None


def _log() -> logging.Logger:
    """The trace logger, pointed at wherever the paths currently say.

    Built per call rather than cached so that configure() moving the paths
    moves the log with them.
    """
    _warm()
    live = sys.modules.get("kizurium_translator.live")
    paths = getattr(live, "PATHS", None)
    state = getattr(paths, "state_dir", None)
    if not isinstance(state, Path):
        state = Path.home() / ".local" / "state" / "kizurium-translator"
    # Рядом с журналом оверлея, а не в Paths: это единственный файл, который
    # добавляет поле к замороженному датаклассу, и он того не стоит - путь
    # выводится из state_dir, который уже есть и уже настроен.
    target = state / "trace.log"
    # Its own file. The overlay log is written for someone reading what was
    # displayed; a trace is written for someone asking why, and the two have
    # different shapes and different readers.
    return logging_setup.get_logger(
        "trace",
        target,
        to_stderr=False,
        formatter=logging_setup.get_trace_formatter(),
    )


# Сколько кадров стека лежит между местом, о котором хочется узнать, и этим
# вызовом _emit, по типам обёрток. Считается не «на глаз», а проверяется на
# тесте по имени функции в строке лога: contextlib добавляет свой кадр при
# входе в stage(), а декоратор - нет, и одно фиксированное число указывало бы
# на contextlib.py вместо вызывающего кода.
_DEPTH_DIRECT = 1      # decide(), consider(), claim() - вызваны прямо
_DEPTH_WRAPPED = 1      # тело @traced - на кадр глубже прямого вызова
_DEPTH_STAGE = 2        # stage(): между _emit и вызывающим кодом __enter__

# Какие функции уже обёрнуты декоратором, чтобы не обернуть дважды. Раньше
# метка висела на самой функции (`inner._kizurium_traced = True`), то есть это
# было поле на объекте-функции - состояние, невидимое линтеру. WeakSet держит
# те же функции и не мешает сборке мусора.
_TRACED: weakref.WeakSet[Callable[..., Any]] = weakref.WeakSet()


def _emit(level: int, msg: str, depth: int, seq: int | None, **fields: Any) -> None:
    """One line, with the context fields the format string asks for.

    ``stacklevel`` points the record at the code that called into here rather
    than at this module, so the file and function in the line are the ones that
    made the statement - which is the entire reason for the format.
    """
    if not enabled():
        return
    extra = ""
    if fields:
        extra = " " + " ".join(f"{k}={brief(v, 60)}" for k, v in fields.items())
    try:
        _log().log(
            level,
            "%s",
            msg + extra,
            stacklevel=2 + depth,
            extra={"seq": "-" if seq is None else str(seq)},
        )
    except Exception as exc:  # noqa: BLE001
        # A log that raises is worse than no log: it would take the overlay down
        # for the sake of a diagnostic. But swallowing it silently is worse
        # still, and it hid a missing import here for a whole test run: every
        # line came out empty and the tests said the log was empty, which is
        # true and useless. One line straight to stderr, once, because stderr is
        # not the thing that is broken.
        global _FAILED_ONCE
        if not _FAILED_ONCE:
            _FAILED_ONCE = True
            try:
                sys.stderr.write(f"[kizurium trace] логирование не работает: {exc!r}\n")
            except Exception:  # noqa: BLE001
                pass


def traced(
    fn: Callable[..., Any] | None = None,
    *,
    returns: bool = True,
    args: bool = True,
) -> Any:
    """Log a function's entry, exit, result, exception and duration.

    Applied bare (``@traced``) or with options. Idempotent, so a function can be
    traced in its own module and again by whoever applies it centrally without
    ending up with two copies of every line.
    """

    def wrap(func: Callable[..., Any]) -> Callable[..., Any]:
        if func in _TRACED:
            return func

        name = getattr(func, "__qualname__", getattr(func, "__name__", "?"))

        @functools.wraps(func)
        def inner(*a: Any, **kw: Any) -> Any:
            if not enabled():
                return func(*a, **kw)
            seq = next_seq()
            shown = ""
            if args:
                parts = [brief(v, 40) for v in a[:6]]
                parts += [f"{k}={brief(v, 30)}" for k, v in list(kw.items())[:6]]
                shown = " ".join(parts)
            _emit(
                logging.DEBUG,
                f"-> {name}({shown})",
                _DEPTH_WRAPPED,
                seq,
            )
            started = time.monotonic()
            try:
                out = func(*a, **kw)
            except BaseException as exc:
                _emit(
                    logging.ERROR,
                    f"!! {name} raised {type(exc).__name__} after "
                    f"{(time.monotonic() - started) * 1000:.1f}ms: {exc}",
                    _DEPTH_WRAPPED,
                    seq,
                )
                raise
            took = (time.monotonic() - started) * 1000.0
            tail = f" = {brief(out)}" if returns else ""
            _emit(
                logging.DEBUG,
                f"<- {name}{tail} [{took:.1f}ms]",
                _DEPTH_WRAPPED,
                seq,
            )
            return out

        _TRACED.add(inner)
        return inner

    return wrap if fn is None else wrap(fn)


@contextmanager
def stage(name: str, **fields: Any) -> Iterator[None]:
    """Mark a stretch of the pipeline, from here to wherever this block ends.

    A stage is a span, not a line: it says where in the pipeline the log is
    currently reading, so a trace can be read as a sequence of stages with the
    decisions inside each one, instead of as a flat list of calls.
    """
    if not enabled():
        yield
        return
    seq = next_seq()
    _emit(logging.INFO, f"== {name} START", _DEPTH_STAGE, seq, **fields)
    started = time.monotonic()
    try:
        yield
    except BaseException as exc:
        _emit(
            logging.ERROR,
            f"== {name} FAILED after {(time.monotonic() - started) * 1000:.1f}ms: "
            f"{type(exc).__name__}: {exc}",
            _DEPTH_STAGE,
            seq,
        )
        raise
    _emit(
        logging.INFO,
        f"== {name} END [{(time.monotonic() - started) * 1000:.1f}ms]",
        _DEPTH_STAGE,
        seq,
    )


def decide(topic: str, verdict: str, **fields: Any) -> None:
    """A choice that could reasonably have gone another way, and what won.

    This is the line worth having. Everything else in the log says what
    happened; this says which of several things that were about to happen
    actually happened, which is the only version of the question that cannot be
    answered by reading the code afterwards.
    """
    _emit(logging.INFO, f"decide {topic}: {verdict}", _DEPTH_DIRECT, None, **fields)


def consider(topic: str, option: str, **fields: Any) -> None:
    """One candidate the decision had in front of it, before the verdict.

    Logged at DEBUG so a run at the default level shows the verdicts and a run
    with the trace on shows the whole shortlist.
    """
    _emit(logging.DEBUG, f"  candidate {topic}: {option}", _DEPTH_DIRECT, None, **fields)


def claim(key: str, owner: str) -> bool:
    """Take ownership of ``key`` for ``owner``, or report that it is taken.

    Returns True to the first caller for a key and False to every later one, and
    logs both outcomes. This is the difference between "the box went missing"
    and "make_block took it at 14:03:11 and ocr_fill_vertical_gaps was refused
    at 14:03:11 two milliseconds later": with a registry the second line is
    written by the code that lost, which is the code that needs changing.
    """
    if not enabled():
        # Without tracing the registry would still have to answer the question
        # correctly, because claim() decides ownership either way.
        with _CLAIM_LOCK:
            if key in _CLAIMS:
                return False
            _CLAIMS[key] = owner
        return True
    with _CLAIM_LOCK:
        taken = _CLAIMS.get(key)
        if taken is not None:
            _emit(
                logging.INFO,
                f"claim REFUSED {key!r} to {owner}, already held by {taken}",
                _DEPTH_DIRECT,
                next_seq(),
            )
            return False
        _CLAIMS[key] = owner
    _emit(
        logging.INFO,
        f"claim {key!r} to {owner}",
        _DEPTH_DIRECT,
        next_seq(),
    )
    return True


def release(key: str, owner: str) -> None:
    """Give a claim back, so the next frame can be decided again."""
    if not enabled():
        return
    with _CLAIM_LOCK:
        if _CLAIMS.get(key) == owner:
            del _CLAIMS[key]
            _emit(
                logging.INFO,
                f"release {key!r} from {owner}",
                _DEPTH_DIRECT,
                next_seq(),
            )


def emit_raw(level: int, msg: str) -> None:
    """A line whose text is already the whole story.

    For the automatic tracer, which builds the line itself out of a code object
    and has no call site of its own to point at - `stacklevel` has nothing
    correct to aim at, so the file and function in the record are its, and the
    message carries the real one. Guessing a stacklevel here would put
    `autotrace.py` in every line and make the trace useless.
    """
    if not enabled():
        return
    try:
        _log().log(level, "%s", msg, extra={"seq": next_seq()})
    except Exception:  # noqa: BLE001
        pass


def digest(blob: bytes) -> str:
    """A short content hash, for things too big to log but worth comparing.

    Two frames that differ must produce different digests, which is the whole
    question when a screenshot command writes a PNG; the image itself is
    megabytes and belongs nowhere near a log file.
    """
    import hashlib

    return hashlib.sha256(blob).hexdigest()[:16]


def watch_command(
    name: str,
    *,
    argv: Any = None,
    cwd: Any = None,
    env_keys: Any = None,
    via: str = "",
) -> None:
    """A process is being started, or has been spawned.

    Caught by an audit hook rather than written at a call site, so it covers the
    ones nobody remembered, the ones inside third-party wrappers, and the ones
    behind ``shell=True`` where the program name is a shell.

    The hook sees two events per command - the request and the actual spawn -
    and they are not the same line twice. `os.posix_spawn` is DEBUG: at the
    default level one command is one line, and the pair is there when asking why
    a command did something other than what it was asked to do.
    """
    level = logging.DEBUG if via in ("os.posix_spawn", "os.exec") else logging.INFO
    _emit(
        level,
        f"exec {name}" + (f" via {via}" if via else ""),
        _DEPTH_DIRECT,
        None,
        argv=argv,
        cwd=cwd,
        env=sorted(env_keys) if env_keys else None,
    )


def watch_command_result(name: str, **fields: Any) -> None:
    """A process finished. The audit hook cannot know this; the caller does."""
    _emit(
        logging.INFO,
        f"done {name}",
        _DEPTH_DIRECT,
        None,
        **fields,
    )


def crash(what: str, **fields: Any) -> None:
    """Something died. Always ERROR, and always with the traceback attached."""
    if not enabled():
        return
    detail = fields.pop("detail", None)
    try:
        logger = _log()
        logger.error(
            "%s%s",
            what,
            (" | " + " ".join(detail.splitlines()[-6:])) if detail else "",
            stacklevel=2,
            extra={"seq": "-"},
        )
    except Exception:  # noqa: BLE001
        pass
    for key, value in fields.items():
        _emit(logging.ERROR, f"  {key}={brief(value, 80)}", _DEPTH_DIRECT, None)


def crash_handler_ready(path: str) -> None:
    """The native-crash handler is armed, and where it writes.

    Logged rather than assumed: a crash handler that failed to install looks
    exactly like a machine that never crashes.
    """
    _emit(logging.INFO, f"crash handler armed -> {path}", _DEPTH_DIRECT, None)


def summary(line: str) -> None:
    """The one line per frame that says what happened and how long it took."""
    _emit(logging.INFO, f"== {line}", _DEPTH_DIRECT, next_seq())


def reset_claims() -> None:
    """Forget every claim. Called at the start of each frame.

    Claims are about one frame. Keeping them across frames would make the second
    frame's refusals look like contention when nothing was competing.
    """
    with _CLAIM_LOCK:
        if _CLAIMS:
            _emit(
                logging.DEBUG,
                f"reset {len(_CLAIMS)} claims",
                _DEPTH_DIRECT,
                next_seq(),
            )
        _CLAIMS.clear()


def claims() -> dict[str, str]:
    """Who holds what right now. For a report, not for a decision."""
    with _CLAIM_LOCK:
        return dict(_CLAIMS)
