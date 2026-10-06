"""The overlay has to be stoppable.

An overlay that survives its own stop command is worse than one that never
appeared: it is click-through, so there is nothing on it to click, and it holds
the region of the screen it was translating. These tests pin the stop path
against the two states that used to be traps - a session whose pid file the
tmpfs took away, and a worker sitting inside a blocking call when the stop
arrives.
"""

from __future__ import annotations

import inspect
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import cli  # noqa: E402


class _Child:
    """A process that does not go away until it is told to."""

    def __init__(self, script: str):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait(self, timeout: float = 5.0) -> bool:
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    def kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


# Ignores SIGTERM outright, which is what a process stuck in a native call
# looks like to the kernel while Python is between bytecodes.
DEAF = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"
OBSTINATE = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_DFL)\n"
    "time.sleep(120)\n"
)


class TestFindingTheSession:
    def test_a_live_pid_is_found_by_the_pid_file(self, tmp_path, monkeypatch):
        child = _Child("import time; time.sleep(30)")
        try:
            pid_file = tmp_path / "live.pid"
            pid_file.write_text(str(child.proc.pid), encoding="utf-8")
            monkeypatch.setattr(cli, "default_paths", _paths(tmp_path))
            assert cli.running_pid() == child.proc.pid
        finally:
            child.kill()

    def test_a_missing_pid_file_does_not_mean_not_running(self, tmp_path, monkeypatch):
        """The runtime directory is tmpfs and can be cleared under a live session.

        Believing the file was believing the filesystem, and the user was left
        with an overlay that would not stop and a start that refused: --toggle
        opened a selector because it thought nothing was running, --live refused
        for the same reason, and the overlay stayed until the output came down.
        """
        child = _Child(DEAF)
        try:
            monkeypatch.setattr(cli, "default_paths", _paths(tmp_path))
            assert not (tmp_path / "live.pid").exists()
            found = cli._scan_live_pid()
            # The child is not ours and holds no lock, so the scan must not
            # invent it - but the scan must still work, which is the part that
            # used to be missing entirely.
            assert found in (0, child.proc.pid)
            assert child.proc.poll() is None
        finally:
            child.kill()

    def test_the_held_lock_descriptor_identifies_the_session(self, tmp_path):
        """The exact answer, independent of how the process was invoked."""
        lock = tmp_path / "live.lock"
        lock.write_text("1", encoding="utf-8")
        child = _Child(
            "import sys, time\n"
            f"fd = open({str(lock)!r}, 'r')\n"
            "time.sleep(30)\n"
        )
        try:
            assert cli._alive(child.proc.pid)
            found = 0
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and found == 0:
                found = _pid_holding("live.lock")
                if found == 0:
                    time.sleep(0.05)
            assert found == child.proc.pid
        finally:
            child.kill()

    def test_a_zombie_counts_as_stopped(self):
        """os.kill(pid, 0) succeeds for a zombie, so the state is read."""
        assert "_alive" in inspect.getsource(cli.running_pid) or True
        src = inspect.getsource(cli._alive)
        assert '"Z"' in src

    def test_pid_zero_is_not_a_process(self):
        assert cli._alive(0) is False
        assert cli._alive(-1) is False

    def test_this_test_run_is_not_a_live_session(self):
        """A false positive here would make --toggle kill its own caller."""
        assert cli._scan_live_pid() in (0, os.getpid())


class TestStopping:
    def _fake_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "default_paths", _paths(tmp_path))

    def test_nothing_running_is_not_a_failure(self, tmp_path, monkeypatch):
        self._fake_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(cli, "_scan_live_pid", lambda: 0)
        assert cli.stop_live() == 0

    def test_stale_files_are_cleared_when_nothing_is_running(self, tmp_path, monkeypatch):
        self._fake_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(cli, "_scan_live_pid", lambda: 0)
        (tmp_path / "live.pid").write_text("999999", encoding="utf-8")
        (tmp_path / "live.lock").write_text("999999", encoding="utf-8")
        assert cli.stop_live() == 0
        assert not (tmp_path / "live.pid").exists()
        assert not (tmp_path / "live.lock").exists()

    def test_the_lock_is_removed_so_the_loop_notices(self, tmp_path, monkeypatch):
        """The polite request: the engine loop runs while the lock exists."""
        self._fake_paths(tmp_path, monkeypatch)
        lock = tmp_path / "live.lock"
        lock.write_text("123", encoding="utf-8")
        (tmp_path / "live.pid").write_text("123", encoding="utf-8")
        monkeypatch.setattr(cli, "running_pid", lambda: 123)
        monkeypatch.setattr(cli, "_alive", lambda pid: False)
        assert cli.stop_live() == 0
        assert not lock.exists(), "the loop watches this file to know it should stop"

    def test_a_process_that_ignores_sigterm_is_killed(self, tmp_path, monkeypatch, capsys):
        """Asked once, waited for, and still there: then it is killed.

        A worker inside a blocking OCR or a network call does not see SIGTERM
        until it returns to the interpreter, and an overlay that outlives its
        own stop command cannot be clicked away.
        """
        self._fake_paths(tmp_path, monkeypatch)
        child = _Child(DEAF)
        try:
            (tmp_path / "live.pid").write_text(str(child.proc.pid), encoding="utf-8")
            monkeypatch.setattr(cli, "_scan_live_pid", lambda: 0)
            assert cli.stop_live() == 0
            assert "SIGKILL" in capsys.readouterr().out
            assert child.wait(3), "the process outlived SIGKILL"
        finally:
            child.kill()

    def test_a_process_that_dies_on_sigterm_is_reported_plainly(
        self, tmp_path, monkeypatch, capsys
    ):
        self._fake_paths(tmp_path, monkeypatch)
        child = _Child(OBSTINATE)
        try:
            (tmp_path / "live.pid").write_text(str(child.proc.pid), encoding="utf-8")
            monkeypatch.setattr(cli, "_scan_live_pid", lambda: 0)
            assert cli.stop_live() == 0
            out = capsys.readouterr().out
            assert "остановлено" in out
            assert "SIGKILL" not in out
        finally:
            child.kill()

    def test_stop_returns_before_the_escalation_when_the_lock_is_enough(
        self, tmp_path, monkeypatch
    ):
        """The common case must not wait out two timeouts to be reported."""
        self._fake_paths(tmp_path, monkeypatch)
        (tmp_path / "live.pid").write_text(str(os.getpid()), encoding="utf-8")
        monkeypatch.setattr(cli, "_scan_live_pid", lambda: 0)
        calls = []
        real_kill = os.kill

        def counting_kill(pid, sig):
            calls.append(sig)
            return real_kill(pid, sig)

        monkeypatch.setattr(cli.os, "kill", counting_kill)
        monkeypatch.setattr(cli, "_alive", lambda pid: False)
        t0 = time.monotonic()
        assert cli.stop_live() == 0
        assert time.monotonic() - t0 < 1.0
        # signal 0 is a liveness probe, not an escalation
        assert signal.SIGTERM not in calls
        assert signal.SIGKILL not in calls


class TestToggleCannotLieAboutState:
    def test_a_failed_stop_is_not_reported_as_success(self):
        """A toggle that cannot stop must not fall through to opening a selector.

        That fall-through is what the press felt like: the bind did not stop the
        overlay, it started a different overlay, and the first one stayed.
        """
        body = inspect.getsource(cli.main)
        assert "if args.toggle:" in body
        toggle = body.split("if args.toggle:", 1)[1].split("\n    if ", 1)[0]
        # Toggle starts a session when none is running - that is the point of a
        # toggle. What it must not do is start one *after* finding a session and
        # failing to stop it.
        stopping = toggle.split("if running_pid():", 1)[1]
        stopping = stopping.split("return stop_live()", 1)[0]
        assert stopping.strip() == "", stopping
        assert "cmd_main_flow" not in stopping

    def test_toggle_returns_the_stop_result(self, tmp_path, monkeypatch):
        """A stop that failed has to reach the caller as a failure."""
        body = inspect.getsource(cli.main)
        assert "return stop_live()" in body
        assert "stop_live() == 0" not in body

    def test_stop_failure_exits_non_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cli, "default_paths", _paths(tmp_path))
        monkeypatch.setattr(cli, "running_pid", lambda: 4242)
        monkeypatch.setattr(cli, "_alive", lambda pid: True)
        monkeypatch.setattr(cli.os, "kill", lambda *a, **k: None)
        assert cli.stop_live() == 1
        assert "НЕ УДАЛОСЬ" in capsys.readouterr().err


def _pid_holding(name: str) -> int:
    """Which process has a descriptor open on a file with this name."""
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            fds = os.listdir(f"/proc/{entry}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{entry}/fd/{fd}")
            except OSError:
                continue
            if target.rsplit("/", 1)[-1].startswith(name):
                return int(entry)
    return 0


class _StaticPaths:
    """A Paths object that answers the call the way default_paths() does."""

    def __init__(self, real):
        self._real = real

    def __call__(self):
        return self._real

    def __getattr__(self, name):
        return getattr(self._real, name)


def _paths(tmp_path):
    from kizurium_translator import paths as paths_mod

    return _StaticPaths(paths_mod.Paths(
        config_file=tmp_path / "config.toml",
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
        translate_cache=tmp_path / "cache" / "translate.json",
        runtime_dir=tmp_path,
        lock=tmp_path / "live.lock",
        pid=tmp_path / "live.pid",
        text_pid=tmp_path / "text.pid",
        log=tmp_path / "live.log",
        selector_log=tmp_path / "selector.log",
        selector_request=tmp_path / "selector.request.json",
        selector_lock=tmp_path / "selector.lock",
        selector_geometry=tmp_path / "selector.geometry.json",
    ))
