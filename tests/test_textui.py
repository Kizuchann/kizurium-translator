"""The text translator window, checked without a display.

GTK cannot run here, so these check the decisions the window makes rather than
the widgets it builds: which pane is editable, what the copy button does, and
how many requests can be in flight at once. Those are the parts that were wrong,
and all three are invisible in a screenshot of a working window.
"""

from __future__ import annotations

import inspect
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator import ui  # noqa: E402


def _window_source() -> str:
    return inspect.getsource(ui.text_window)


class TestResultPaneIsReadOnly:
    def test_the_result_pane_cannot_be_typed_into(self):
        """A user who can edit the result cannot tell it from a real one.

        The right pane is what they read. Editing it is not a feature, it is a
        way to end up with a translation that is quietly their own wording.
        """
        assert "editable=False" in _window_source()

    def test_the_source_pane_is_still_editable(self):
        assert "editable=True" in _window_source()

    def test_the_read_only_pane_offers_a_copy_button(self):
        assert "on_copy" in _window_source()
        assert "copy_to_clipboard" in _window_source()

    def test_the_button_label_says_what_it_does(self):
        assert "Копировать" in inspect.getsource(ui._pane)


class TestLatestOnly:
    def test_only_one_request_is_in_flight(self):
        """Typing "hello world" is one request, not eleven.

        The old guard stopped an obsolete answer being displayed but the request
        still ran to completion, so every keystroke spent the endpoint's rate
        limit and the answers came back in whatever order the network finished.
        """
        assert "previous.join" in _window_source()
        assert 'state["thread"] = thread' in _window_source()

    def test_an_obsolete_answer_is_dropped(self):
        assert 'if job != state["job"]' in _window_source()

    def test_the_wait_is_bounded(self):
        """Typing must not feel like it is waiting on the network."""
        assert 0 < ui.OBSOLETE_WAIT_S <= 0.5, ui.OBSOLETE_WAIT_S

    def test_threads_are_not_created_without_bound(self):
        """Every request is a thread; nothing is keeping the list."""
        source = _window_source()
        assert source.count("threading.Thread(") == 1
        # the in-flight one is replaced, not appended to
        assert 'state["thread"] = thread' in source


class TestClipboard:
    def test_copy_reports_failure_instead_of_doing_nothing(self, monkeypatch):
        """A missing wl-copy has to be visible, not a click that does nothing."""
        import shutil as real_shutil

        monkeypatch.setattr(real_shutil, "which", lambda _n: None)
        assert ui.copy_to_clipboard("текст") is False

    def test_empty_text_is_not_copied(self):
        assert ui.copy_to_clipboard("") is False

    def test_a_successful_copy_reports_true(self, monkeypatch):
        import shutil as real_shutil
        import subprocess as real_subprocess

        class Ok:
            returncode = 0

        monkeypatch.setattr(real_shutil, "which", lambda _n: "/usr/bin/wl-copy")
        monkeypatch.setattr(
            real_subprocess,
            "run",
            lambda cmd, **kw: Ok(),
        )
        assert ui.copy_to_clipboard("текст") is True

    def test_a_failing_tool_is_not_reported_as_success(self, monkeypatch):
        import shutil as real_shutil
        import subprocess as real_subprocess

        class Bad:
            returncode = 1

        monkeypatch.setattr(real_shutil, "which", lambda _n: "/usr/bin/wl-copy")
        monkeypatch.setattr(real_subprocess, "run", lambda cmd, **kw: Bad())
        assert ui.copy_to_clipboard("текст") is False

    def test_a_tool_that_raises_is_not_reported_as_success(self, monkeypatch):
        import shutil as real_shutil
        import subprocess as real_subprocess

        monkeypatch.setattr(real_shutil, "which", lambda _n: "/usr/bin/wl-copy")

        def boom(cmd, **kw):
            raise real_subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(real_subprocess, "run", boom)
        assert ui.copy_to_clipboard("текст") is False


class TestSerialisationHolds:
    def test_only_one_thread_runs_at_a_time_in_practice(self):
        """The behaviour the source claims, exercised for real."""
        concurrent = 0
        peak = 0
        lock = threading.Lock()

        def request() -> None:
            nonlocal concurrent, peak
            with lock:
                concurrent += 1
                peak = max(peak, concurrent)
            time.sleep(0.02)
            with lock:
                concurrent -= 1

        previous: threading.Thread | None = None
        for _ in range(5):
            thread = threading.Thread(target=request, daemon=True)
            if previous is not None and previous.is_alive():
                previous.join(timeout=ui.OBSOLETE_WAIT_S)
            thread.start()
            previous = thread
        if previous is not None:
            previous.join(timeout=2)
        assert peak == 1, f"ran {peak} requests at once"
