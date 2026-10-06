"""Log setup.

Two things make this its own module rather than a call inside live.py:

- the log has to outlive the session. XDG_RUNTIME_DIR is tmpfs and the session
  manager wipes it, so a log written there is gone exactly when someone needs
  to read it after a crash. Logs therefore go to XDG_STATE_HOME, while the
  lock and the pid stay on tmpfs because they are meaningless between runs.
- several processes write. The selector is a separate process from the overlay
  and they fail independently, so each gets a file.

Rotation is 1 MiB with three files kept, which holds a long session without
letting a directory of logs grow without bound. Files are created 0600 inside a
0700 directory: a log records what was on the screen.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import stat
import sys
from pathlib import Path

MAX_BYTES = 1024 * 1024
BACKUP_COUNT = 3
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"

# A trace is read to find out which function decided what, so the function is
# part of the line rather than something to be typed into the message at each
# call site - and every one of these fields comes from the record the logging
# module builds anyway. Milliseconds because two passes over one box are
# microseconds apart, and "who was first" is the whole question. The order
# number is the fallback when even that is not enough: threads interleave, and
# only a total order can be trusted to say which of two claims came first.
TRACE_FORMAT = (
    "%(asctime)s.%(msecs)03d %(levelname)-5s "
    "%(threadName)s %(filename)s:%(lineno)d %(funcName)s [%(seq)s] %(message)s"
)


class _TolerantFormatter(logging.Formatter):
    """A formatter that does not raise over a field a call site left out.

    ``[%(seq)s]`` is set on some lines and not others, and a formatter that
    raised on the missing one would turn a missing cosmetic field into a lost
    line - the opposite of what the trace is for.
    """

    _MISSING = {"seq": "-", "filename": "?", "funcName": "?", "threadName": "?"}

    def format(self, record: logging.LogRecord) -> str:
        for key, value in self._MISSING.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return super().format(record)


_TRACE_FORMATTER: _TolerantFormatter | None = None


def get_trace_formatter() -> logging.Formatter:
    """One formatter instance, always the same object.

    `get_logger` compares the formatter by identity to decide whether the
    handler needs replacing. A fresh instance per call therefore rebuilt the
    handler on every log line, and a rebuilt handler that the old one had
    already written to leaves the file with each line in it twice.
    """
    global _TRACE_FORMATTER
    if _TRACE_FORMATTER is None:
        _TRACE_FORMATTER = _TolerantFormatter(
            TRACE_FORMAT, datefmt=DATE_FORMAT
        )
    return _TRACE_FORMATTER


def _secure(path: Path) -> None:
    """Tighten permissions on a log file we just created.

    mkdir is called with 0700 but a pre-existing directory keeps its mode, and
    the umask decides what a plain open produces. Both are set explicitly rather
    than assumed.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
    except OSError:
        pass
    try:
        if path.exists():
            path.chmod(0o600)
    except OSError:
        pass


def _restrict(path: Path) -> None:
    """Drop a log file to 0600.

    RotatingFileHandler creates the file through open(), which follows the
    umask and lands on 0644 for the usual 022. A log records what was on the
    screen, so the mode is set on every call rather than only at creation: a
    file that already exists keeps whatever mode it had.
    """
    try:
        if path.exists() and stat.S_IMODE(path.stat().st_mode) != 0o600:
            path.chmod(0o600)
    except OSError:
        pass


def _has_console_handler(logger: logging.Logger) -> bool:
    """Whether the logger already writes to a console stream.

    ``RotatingFileHandler`` is a ``StreamHandler`` subclass, so a naive
    ``isinstance(..., StreamHandler)`` check treats the file as "already on
    stderr" and never attaches the console handler. That is why live looked
    silent in the terminal while the overlay.log file filled up.
    """
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            continue
        if isinstance(handler, logging.StreamHandler):
            return True
    return False


def _attach_stderr(logger: logging.Logger, formatter: logging.Formatter) -> None:
    if _has_console_handler(logger):
        return
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    logger.addHandler(stream)


def get_logger(
    name: str,
    logfile: Path,
    *,
    to_stderr: bool = True,
    formatter: logging.Formatter | None = None,
) -> logging.Logger:
    """A logger writing to ``logfile`` and, optionally, to stderr.

    Repeated calls for one name return the same logger, because handlers
    otherwise accumulate and every line reaches the file several times. The
    cached handler is also pointed at a new path when one is asked for, which
    is what lets a test run against a temporary directory in the same process
    that already wrote to the real one.

    The formatter is compared as well as the path. Without that, a second call
    for the same file with a different format keeps the first handler, and the
    trace ends up written in the plain format - which reads exactly as though
    nothing had been recorded.
    """
    logger = logging.getLogger(name)
    formatter = formatter or logging.Formatter(FORMAT, datefmt=DATE_FORMAT)

    existing = getattr(logger, "_kizurium_file", None)
    if isinstance(existing, logging.Handler):
        if (
            Path(getattr(existing, "baseFilename", "")) == Path(logfile)
            and getattr(existing, "formatter", None) is formatter
        ):
            if to_stderr:
                _attach_stderr(logger, formatter)
            return logger
        logger.removeHandler(existing)
        try:
            existing.close()
        except Exception:  # noqa: BLE001
            pass
        delattr(logger, "_kizurium_file")

    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        _secure(logfile)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            logfile,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        _restrict(logfile)
    except OSError:
        # An unwritable log directory must not stop the overlay: stderr still
        # carries everything when the process has a terminal.
        handler = logging.NullHandler()
    else:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger._kizurium_file = handler  # type: ignore[attr-defined]

    if to_stderr:
        _attach_stderr(logger, formatter)

    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
    return logger


def rotate_if_oversized(path: Path) -> None:
    """Roll a log that grew past the limit, before something appends to it.

    Used where a caller opens the file itself to hand it to a child process,
    which a RotatingFileHandler cannot do. The suffix naming is kept compatible
    with what RotatingFileHandler produces so one directory stays consistent.
    """
    try:
        if not path.is_file() or path.stat().st_size < MAX_BYTES:
            return
        oldest = path.with_name(f"{path.name}.{BACKUP_COUNT}")
        if oldest.exists():
            oldest.unlink()
        for index in range(BACKUP_COUNT - 1, 0, -1):
            src = path.with_name(f"{path.name}.{index}")
            if src.exists():
                src.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        _secure(path)
        for index in range(1, BACKUP_COUNT + 1):
            _restrict(path.with_name(f"{path.name}.{index}"))
    except OSError:
        pass


def debug_enabled() -> bool:
    """Whether per-text diagnostics were asked for.

    Screen text at INFO would put everything read off the screen into a file on
    disk. The measurements that need it are opt-in and stay off by default.
    """
    return os.environ.get("KIZURIUM_TRANSLATOR_DEBUG", "").strip() not in ("", "0", "no", "false")
