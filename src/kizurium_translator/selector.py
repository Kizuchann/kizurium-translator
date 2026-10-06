"""Region selector: fullscreen overlay that picks where and what.

QML lives in ``qml/selector/``. Started with ``quickshell -p <dir>`` as its
own shell root — does not read or change the user's Quickshell or compositor
config. Returns ``(action, geometry)``; OCR/translation are done by the caller.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import logging_setup
from .paths import default_paths

QML_DIR = Path(__file__).resolve().parent / "qml" / "selector"

SEL_CANCEL = ""
SEL_OCR = "ocr"
SEL_TEXT = "text"
SEL_LIVE = "live"

# The toolbar, in the order it is drawn. The round Live button is the last entry
# and is the default for Return.
DEFAULT_ACTIONS = (SEL_OCR, SEL_TEXT, SEL_LIVE)

# How long the overlay may stay up before Python gives up on it. The overlay also
# closes itself after the same time, so neither side can leave the other hanging.
DEFAULT_TIMEOUT_S = 120.0

# The overlay additionally closes itself this long after opening. This is the one
# that matters: if this process is killed outright, the `finally` block never
# runs, and without the shell-side timer the overlay would sit on the screen
# holding the keyboard with nothing left to close it.
WATCHDOG_S = 100.0

_IPC_TARGET = "selector"

# Poll interval while waiting for the answer. There is no settle time any more:
# the shell reads what it needs as it loads, so there is nothing to wait for
# before the work can start.
_GAP = 0.05

# "x,y WxH" in layout coordinates. Anything else is a corrupt or foreign value
# and is dropped rather than handed to a screenshot or a grim rectangle.
GEOMETRY_RE = re.compile(r"-?\d+,-?\d+ \d+x\d+")

# The last request handed to a shell. Only read by tests, to check what the
# overlay was actually given.
_last_request: dict[str, object] = {}


class SelectorError(RuntimeError):
    """The selector could not be started."""


class _AlreadyOpen(Exception):
    """Another selector shell already owns this path, and has been closed."""


def have_selector() -> bool:
    """True when quickshell is installed and the QML is present."""
    return bool(shutil.which("quickshell")) and (QML_DIR / "shell.qml").is_file()


def _install_hint() -> str:
    if not (QML_DIR / "shell.qml").is_file():
        return f"QML не найден: {QML_DIR}"
    return (
        "Установи quickshell "
        "(Arch: pacman -S quickshell · Nix: pkgs.quickshell). "
        "Обход: kizurium-translator --live --region \"x,y WxH\" или --output"
    )


# --------------------------------------------------------------------- freeze


def _frame_problem(path: Path) -> str:
    """What the overlay reported about its own frame, or "" if all is well.

    The overlay captures the frame itself, so this is the only way to find out
    that the capture did not work. A silent blank overlay is not acceptable, so
    the answer goes into the log.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return ""
    status = str(data.get("frame") or "ok")
    if status == "ok":
        return ""
    return f"снимок экрана не удался (output={data.get('name') or '?'}, {status})"


# ----------------------------------------------------------------- single run


class _Single:
    """One selector at a time, held with flock on a real file.

    Pressing the key three times must not produce three overlays. A second press
    while one is up cancels it, which is also the way out if the first one ever
    ends up not responding.
    """

    def __init__(self) -> None:
        self._fh = None

    def __enter__(self) -> bool:
        """True when this process owns the selector, False when one is already up."""
        # Under the application runtime directory, not /tmp: a shared /tmp means
        # any other user on the machine can hold this lock and lock the real user
        # out of their own selector.
        path = default_paths().selector_lock
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return False
        try:
            self._fh = open(path, "w")
        except OSError:
            self._fh = None
            return False
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return False
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return True

    def __exit__(self, *_exc) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            except OSError:
                pass
            self._fh.close()
            self._fh = None


def _stop_shell(shell: subprocess.Popen | None) -> None:
    """Stop only the selector we started, by pid.

    Never by name: the process is called quickshell, and killing that name would
    take down whatever shell the user runs.
    """
    if shell is None or shell.poll() is not None:
        return
    try:
        shell.terminate()
        shell.wait(timeout=3)
    except (OSError, subprocess.SubprocessError):
        try:
            shell.kill()
        except OSError:
            pass


def _raise_on_signal(signum, _frame) -> None:
    """Turn a signal into an exception so the usual cleanup path still runs."""
    raise KeyboardInterrupt(f"signal {signum}")


def _cleanup_selector(
    shell: subprocess.Popen | None,
    files: tuple[Path, ...],
    previous_handlers: dict[int, object],
    log_fh,
) -> None:
    """The one place a selector run is torn down.

    Every ending - Escape, right click, the toolbar, the watchdog, a timeout, a
    signal - comes through here, so nothing can be left on screen because one
    exit path forgot a step.
    """
    for sig, handler in previous_handlers.items():
        try:
            signal.signal(sig, handler)  # type: ignore[arg-type]
        except (OSError, ValueError, TypeError):
            pass
    # The overlay must be gone before the caller starts anything else, otherwise
    # two overlays end up on screen at once.
    _stop_shell(shell)
    for path in files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if log_fh is not None:
        try:
            log_fh.close()
        except OSError:
            pass


def _read_result(path: Path, timeout_s: float, shell: subprocess.Popen | None) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                data = {}
            if data.get("action") or data.get("done"):
                return str(data.get("action") or SEL_CANCEL)
        if shell is not None and shell.poll() is not None:
            # The shell quit on its own, so nothing will ever be written.
            return SEL_CANCEL
        time.sleep(_GAP)
    return SEL_CANCEL


# Selector uses built-in colours only. No Serpantinum dependency.


def geometry_file() -> Path:
    """Where remembered regions live, keyed per output.

    Under XDG state (not runtime tmpfs): a region should survive logout/reboot
    the same way Win+Shift+S remembers. Runtime is wiped by the session manager,
    which made every new session feel like the box was never saved.
    """
    path = default_paths().selector_geometry
    # One-shot migrate from the old runtime location.
    if not path.exists():
        legacy = default_paths().runtime_dir / "selector-geometry.json"
        if legacy.exists() and legacy != path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass
    return path


def output_key(name: str, width: int, height: int) -> str:
    """Identity of an output, size included.

    A connector name alone is not enough: the same DP-1 at 1920x1080 and at
    2560x1440 are different places, and a region from one is nonsense on the
    other. Mode changes and replugging both produce a new key, so the stale
    region is simply not offered rather than landing somewhere wrong.
    """
    return f"{name}@{int(width)}x{int(height)}"


def remembered_regions() -> dict[str, str]:
    """Every remembered region, by output key. Empty when there are none."""
    try:
        data = json.loads(geometry_file().read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in data.items()
        if isinstance(k, str) and GEOMETRY_RE.fullmatch(str(v))
    }


def remember_geometry(geom: str, key: str = "") -> None:
    """Remember a region for one output. A blank key or shape is ignored."""
    if not GEOMETRY_RE.fullmatch(geom or "") or not key:
        return
    regions = remembered_regions()
    regions[key] = geom
    # A handful of outputs at most; an unbounded file would just be a leak.
    if len(regions) > 16:
        regions = dict(list(regions.items())[-16:])
    path = geometry_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(regions), encoding="utf-8")
    except OSError:
        pass


def _frames_for(base: str) -> list[Path]:
    """Every capture the overlay may have written for one run.

    The overlay numbers its captures, so a run that was retried left more than
    one file. Cleaning a single path left the rest in the runtime directory,
    where they outlived the session that made them.
    """
    if not base:
        return []
    try:
        prefix = Path(base)
    except (TypeError, ValueError):
        return []
    return sorted(prefix.parent.glob(f"{prefix.name}-*.png"))


def _write_start_request(
    runtime_dir: Path,
    token: str,
    watchdog_s: float,
    request_file: Path,
    *,
    auto_action: str = "",
) -> dict[str, object]:
    """Write what the shell needs, and return the paths it will use.

    The shell is pointed at this directory and reads the request as it loads, so
    the values are in place before the process exists. Nothing has to wait for an
    IPC endpoint, and nothing has to be retried.

    Remembered regions are handed over as a whole map keyed by output: Python does
    not know which output the overlay will land on, so it cannot pick the one
    that applies. The overlay does that.

    ``auto_action`` (e.g. ``ocr`` / ``live``): finish as soon as the user
    releases a valid drag — no toolbar click. Used by ``--ocr-copy`` / ``--live``.
    """
    request: dict[str, object] = {
        "token": token,
        "result": str(runtime_dir / f"selector-result-{token}.json"),
        "screen": str(runtime_dir / f"selector-screen-{token}.json"),
        # A base name, not a path: the overlay numbers its own captures so a
        # retry writes a new file rather than overwriting the one its Image
        # already has open, which is what produced a half-drawn frame.
        "frame": str(runtime_dir / f"selector-frame-{token}"),
        "geometries": remembered_regions(),
        "watchdogMs": int(watchdog_s * 1000),
        "autoAction": (auto_action or "").strip(),
    }
    runtime_dir.mkdir(parents=True, exist_ok=True)
    request_file.write_text(json.dumps(request), encoding="utf-8")
    # Kept so a test can see exactly what the shell was handed.
    global _last_request
    _last_request = request
    return request


def select_region(
    actions: tuple[str, ...] = DEFAULT_ACTIONS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    *,
    auto_action: str = "",
) -> tuple[str, str]:
    """Show the overlay, return ``(action, geometry)``.

    ``geometry`` is ``"x,y WxH"`` in layout coordinates, ready for ``--region``.
    ``("", "")`` means cancelled. Raises:class:`SelectorError` when the overlay
    cannot be started at all.

    With ``auto_action`` set (``ocr`` / ``live`` / ``text``), the overlay skips
    the toolbar and confirms that action on mouse-up after a valid drag.
    """
    if not have_selector():
        raise SelectorError(_install_hint())

    auto = (auto_action or "").strip()
    if auto and auto not in actions:
        actions = actions + (auto,)

    paths = default_paths()
    log = paths.selector_log
    with _Single() as mine:
        if not mine:
            # One is already up: treat this press as "get me out of here".
            _cancel_existing()
            return SEL_CANCEL, ""

        token = secrets.token_hex(8)
        try:
            request = _write_start_request(
                paths.runtime_dir,
                token,
                WATCHDOG_S,
                paths.selector_request,
                auto_action=auto,
            )
        except OSError as exc:
            raise SelectorError(
                f"не удалось подготовить селектор: {exc} ({paths.runtime_dir})"
            ) from exc
        out = Path(request["result"])
        screen_out = Path(request["screen"])
        frame_base = str(request["frame"])
        for stale in (out, screen_out):
            stale.unlink(missing_ok=True)
        for stale in _frames_for(frame_base):
            stale.unlink(missing_ok=True)

        shell: subprocess.Popen | None = None
        log_fh = None
        action = ""
        geometry = ""
        output = ""
        # One cleanup path for every way this can end: Escape, right click, the
        # toolbar, the watchdog, a timeout, or a signal.
        previous_handlers: dict[int, object] = {}
        try:
            # Rotate before appending: the quickshell output is handed straight
            # to this descriptor, so the file is opened once per run and the
            # size check happens here rather than through a handler.
            sel_log = logging_setup.get_logger("selector", log, to_stderr=False)
            logging_setup.rotate_if_oversized(log)
            sel_log.info("--- selector %s ---", token)
            try:
                log_fh = open(log, "a", encoding="utf-8")
            except OSError:
                # A missing log must not stop the selector, but the run then has
                # nowhere to record why it failed.
                log_fh = None
            shell = subprocess.Popen(
                ["quickshell", "-n", "-p", str(QML_DIR)],
                cwd=str(paths.runtime_dir),
                stdout=log_fh or subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    previous_handlers[sig] = signal.signal(
                        sig, _raise_on_signal
                    )
                except (OSError, ValueError):
                    # Not the main thread, or the platform has no such signal.
                    previous_handlers.pop(sig, None)

            action = _read_result(out, timeout_s, shell)
            if log_fh is not None:
                problem = _frame_problem(screen_out)
                if problem:
                    log_fh.write(f"selector {token}: {problem}\n")
                    log_fh.flush()
            if not action and shell is not None and shell.poll() is not None:
                # -n makes quickshell exit at once when another shell already
                # owns this path. That is not a user cancelling, and the real
                # overlay is still up: close it and get out of the way.
                _cancel_existing()
                raise _AlreadyOpen
            if action and out.is_file():
                try:
                    answer = json.loads(out.read_text(encoding="utf-8") or "{}")
                except (OSError, ValueError):
                    answer = {}
                geometry = str(answer.get("geometry") or "")
                # The overlay names the output it was on, so the region is stored
                # against that output instead of overwriting whatever the last
                # run on a different monitor happened to leave behind.
                output = str(answer.get("output") or "")
        except _AlreadyOpen:
            sel_log.info("selector %s: другой селектор уже открыт", token)
            return SEL_CANCEL, ""
        except subprocess.TimeoutExpired as exc:
            sel_log.warning("selector %s: не ответил вовремя", token)
            raise SelectorError("селектор не ответил вовремя") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            sel_log.warning("selector %s: запуск не удался: %s", token, exc)
            raise SelectorError(
                f"не удалось запустить селектор: {exc}. Лог: {log}"
            ) from exc
        finally:
            # Every numbered capture, not one path: a run that retried left
            # several files behind.
            _cleanup_selector(
                shell,
                (out, screen_out, *_frames_for(frame_base)),
                previous_handlers,
                log_fh,
            )

    if action not in actions:
        return SEL_CANCEL, ""
    # Next time the selector opens on this same output, with the same region
    # already selected.
    remember_geometry(geometry, output)
    return action, geometry


def _ipc(*args: str) -> bool:
    """Send one IPC message to the running selector shell."""
    try:
        res = subprocess.run(
            ["quickshell", "-p", str(QML_DIR), "ipc", "call", _IPC_TARGET, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def _cancel_existing() -> None:
    """Pressing the key again closes the overlay that is already up.

    This is the escape hatch that does not depend on the overlay cooperating: if
    Escape somehow does not arrive, the same key gets you out.
    """
    _ipc("cancel")
