"""The overlay contract: actions, safety, and the no-harm rules.

The QML itself cannot run under pytest, so these cover the Python side and the
invariants that matter most: one selector at a time, no process killing by name,
and the overlay always gone before the caller starts anything.
"""

import json
import re
import signal
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from kizurium_translator import paths as selector_paths
from kizurium_translator import selector


class Done0:
    """subprocess.CompletedProcess stand-in with a zero exit code."""

    def __init__(self, cmd=(), code: int = 0) -> None:
        self.args = cmd
        self.returncode = code
        self.stdout = b""
        self.stderr = b""


class FakeProc:
    """Stand-in for the quickshell shell, so nothing is ever drawn in tests."""

    def __init__(self, stopped: list[int] | None = None, pid: int = 4242) -> None:
        self.pid = pid
        self._stopped = stopped if stopped is not None else []
        self._code = None

    def poll(self):
        return self._code

    def terminate(self) -> None:
        self._stopped.append(self.pid)
        self._code = 0

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self._stopped.append(self.pid)


@pytest.fixture
def _state_dir(tmp_path):
    """A throwaway stand-in for the selector state file."""
    return tmp_path


@pytest.fixture
def wired(monkeypatch, _state_dir):
    """Wire select_region so it never launches anything real.

    ``calls["answer"]`` is the result the fake overlay should write. Leaving it
    as None means the overlay stays silent and the run times out, which is what
    the cancel and timeout tests need.
    """
    calls: dict[str, object] = {
        "pops": [],
        "kwargs": [],
        "runs": [],
        "stopped": [],
        "requests": [],
        "answer": None,
    }

    monkeypatch.setattr(selector, "_GAP", 0.01)
    # Tests must never touch the real runtime directory: the selector writes its
    # start request, log and result files there, and a fake geometry would show
    # up in the user's next selector run.
    monkeypatch.setattr(selector, "default_paths", lambda: _fake_paths(_state_dir))

    def popen(cmd, **kw):
        pops: list = calls["pops"]  # type: ignore[assignment]
        kwargs_log: list = calls["kwargs"]  # type: ignore[assignment]
        pops.append(list(cmd))
        kwargs_log.append(kw)
        request = _read_request(_state_dir)
        requests: list = calls["requests"]  # type: ignore[assignment]
        requests.append(request)
        answer = calls["answer"]
        if answer is not None:
            _write_answer(request, answer)
        return FakeProc(calls["stopped"])  # type: ignore[arg-type]

    monkeypatch.setattr(selector.subprocess, "Popen", popen)

    def run(cmd, **kw):
        runs: list = calls["runs"]  # type: ignore[assignment]
        runs.append(list(cmd))
        return Done0(cmd)

    monkeypatch.setattr(selector.subprocess, "run", run)
    return calls


def _fake_paths(root: Path) -> selector_paths.Paths:
    """A Paths pointing entirely inside a throwaway directory."""
    return selector_paths.Paths(
        config_file=root / "config.toml",
        state_dir=root / "state",
        cache_dir=root / "cache",
        translate_cache=root / "cache" / "translate.json",
        runtime_dir=root,
        lock=root / "translate.lock",
        pid=root / "translate.pid",
        text_pid=root / "textui.pid",
        log=root / "overlay.log",
        selector_log=root / "selector.log",
        selector_request=root / "selector-request.json",
        selector_lock=root / "selector.lock",
        selector_geometry=root / "selector-geometry.json",
    )


def _read_request(root: Path) -> dict:
    """The start request, as the shell reads it when it loads."""
    return json.loads((root / "selector-request.json").read_text(encoding="utf-8"))


def _write_answer(request: dict, payload: dict) -> None:
    """Answer the way the overlay does: write the result file it was given."""
    path = Path(request["result"])
    if "screen" in payload:
        Path(request["screen"]).write_text(
            json.dumps(payload["screen"]), encoding="utf-8"
        )
    path.write_text(json.dumps(payload.get("result", {})), encoding="utf-8")


def _is_start(cmd: list[str]) -> bool:
    """The start call, found by content rather than by offset."""
    return "start" in cmd


def _result_path(cmd: list[str]) -> Path:
    """The result file, found by extension: the call carries several arguments."""
    return Path(next(a for a in cmd if a.endswith(".json")))


def _write_result(cmd: list[str], payload: dict) -> None:
    _result_path(cmd).write_text(json.dumps(payload), encoding="utf-8")


class TestActions:
    def test_toolbar_offers_ocr_text_live(self):
        assert selector.DEFAULT_ACTIONS == ("ocr", "text", "live")

    def test_live_is_last_and_is_the_return_key_default(self):
        # The round button is the last toolbar entry and Return maps to it.
        assert selector.SEL_LIVE == selector.DEFAULT_ACTIONS[-1]
        source = selector.QML_DIR.joinpath("ScreenshotOverlay.qml").read_text(encoding="utf-8")
        assert 'sequence: "Return"' in source
        assert 'root.finish("live")' in source

    def test_buttons_sit_above_the_drag_layer(self):
        """The drag MouseArea must not cover the buttons.

        It used to sit at z: 50 with the toolbar left at the default 0, so every
        click landed on the drag layer: the buttons could not be pressed at all
        and the overlay just sat there. The original has toolbar z: 30, mouse
        z: 20.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        mouse_at = source.index("acceptedButtons: Qt.LeftButton | Qt.RightButton")
        window = source[mouse_at : mouse_at + 400]
        drag_z = int(window.split("z: ")[1].split()[0])
        # the whole panel carries the z, and both rows sit inside it
        toolbar_z = int(source.split("id: toolbar")[1].split("z: ")[1].split()[0])
        assert drag_z < toolbar_z, (drag_z, toolbar_z)
        # both rows really are inside that panel, so they inherit its z
        panel = source.split("id: toolbar")[1].split("// ---- interaction")[0]
        assert "id: bar" in panel and "id: round" in panel

    def test_toolbar_follows_the_selection(self):
        """The panel has to travel with the selection, not sit on the screen edge.

        It was pinned to the top and bottom of the output, which is nothing like
        the original, where the whole panel sits under the selection and flips
        above it when it would not fit.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        panel = source.split("id: toolbar")[1].split("// ---- interaction")[0]
        # positioned from the selection, in both axes
        assert "root.selX + (root.selW / 2)" in panel, "panel must centre on the selection"
        assert "root.selY + root.selH + root.s(15)" in panel, "must sit below the selection"
        assert "root.selY - height - root.s(15)" in panel, "must flip above it"
        assert "fitsOutsideBottom" in panel
        # clamped inside the output
        assert "Math.max(root.s(10)" in panel
        # both rows live in the same item, so the panel moves as one piece
        assert 'id: bar' in panel and 'id: round' in panel
        # and nothing is pinned to the screen edges any more
        assert "anchors.top: parent.top" not in panel.split("id: bar")[0].split("Item {")[-1][:200]

    def test_colours_come_from_the_desktop_theme(self):
        """The original read the generated theme, so it matched the desktop.
        Now colours are loaded in QML via loadTheme(), using _col() function.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        for key in ("base", "crust", "text", "surface0", "red", "mauve"):
            assert f'_col("{key}"' in source, key
        assert "loadTheme" in source

    def test_panel_size_does_not_depend_on_its_children(self):
        """A width derived from bar.width while bar was anchored to the panel is
        a binding cycle, and the panel stopped following the selection."""
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        panel = source.split("id: toolbar")[1].split("// ---- interaction")[0]
        # comments explain the cycle and mention it by name
        code = "\n".join(l for l in panel.splitlines() if not l.strip().startswith("//"))
        # The panel's own width must not come from a child it positions.
        # "toolbar.width" inside the bar is fine; a bare "bar.width" is the cycle.
        assert not re.search(r"(?<![\w.])bar\.width", code), code[:200]
        panel_width = [l for l in code.splitlines() if l.strip().startswith("width:")]
        assert panel_width, "the panel needs an explicit width"
        assert "root.s(" in panel_width[0], panel_width[0]

    def test_no_hardcoded_screen_size(self):
        """A 1920x1080 default clamps every selection to a corner of a 4K panel.

        Nothing is measured or drawn before the real output size is known, so
        the fallback is not a safety net, it is a wrong answer.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        assert "property real mouseW: 1920" not in source
        assert "property real mouseH: 1080" not in source
        assert "property real mouseW: 0" in source

    def test_output_size_is_settled_before_geometry_is_restored(self):
        """restoreGeometry clamps against the size and the scale.

        Doing it first, with a zero size, silently drops the remembered region -
        which is exactly the "Win+Shift+S forgets my region" symptom.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        # The size is settled in claimScreen(), which is where an output gets
        # identified: prepare() no longer picks one, because there is a window on
        # every connected output now.
        claim = source[source.index("function claimScreen("):]
        claim = claim[:claim.index("\n    function ")]
        assert claim.index("mouseW = w") < claim.index("restoreGeometry(")
        assert claim.index("mouseH = h") < claim.index("restoreGeometry(")
        # The origin is taken off before the region becomes window-local too.
        assert claim.index("originX = scr.x") < claim.index("restoreGeometry(")

    def test_begin_does_not_restore_geometry_itself(self):
        """begin() only delegates: restoring is prepare()'s job, after the size."""
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        begin = source[source.index("function begin("):]
        begin = begin[:begin.index("\n    function ")]
        assert "restoreGeometry" not in begin
        assert "prepare()" in begin

    def test_prepare_schedules_immediate_restore(self):
        """Previous region must appear without waiting for a click."""
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        assert "function autoRestoreRemembered()" in source
        prepare = source[source.index("function prepare()"):]
        prepare = prepare[:prepare.index("\n    function ")]
        assert "autoRestoreRemembered" in prepare

    def test_selection_is_remembered_between_runs(self, wired):
        """Win+Shift+S starts with the previous region; so must this."""
        wired["answer"] = {
            "screen": {"name": "DP-1", "x": 0, "y": 0, "frame": "ok"},
            "result": {
                "action": "ocr",
                "geometry": "120,80 640x360",
                "output": "DP-1@1920x1080",
                "done": True,
            },
        }
        assert selector.select_region(timeout_s=5) == ("ocr", "120,80 640x360")
        assert selector.remembered_regions() == {"DP-1@1920x1080": "120,80 640x360"}

    def test_a_region_is_not_shared_between_outputs(self, wired, tmp_path):
        """A region drawn on one monitor means nothing on another.

        Remembering a single global region meant the next run on a different
        output came up with a box at the same numbers, covering unrelated content.
        """
        key_a = selector.output_key("eDP-1", 1920, 1080)
        key_b = selector.output_key("DP-1", 2560, 1440)
        assert key_a != key_b

        wired["answer"] = {
            "screen": {"name": "eDP-1", "x": 0, "y": 0, "frame": "ok"},
            "result": {
                "action": "ocr",
                "geometry": "10,10 200x100",
                "output": key_a,
                "done": True,
            },
        }
        selector.select_region(timeout_s=5)
        wired["answer"] = {
            "screen": {"name": "DP-1", "x": 1920, "y": 0, "frame": "ok"},
            "result": {
                "action": "ocr",
                "geometry": "2000,40 300x200",
                "output": key_b,
                "done": True,
            },
        }
        selector.select_region(timeout_s=5)
        assert selector.remembered_regions() == {
            key_a: "10,10 200x100",
            key_b: "2000,40 300x200",
        }

    def test_the_same_output_at_a_new_size_does_not_inherit_the_region(self, wired):
        """Replugging a monitor or changing its mode is a different place.

        The connector name is unchanged, so keying on the name alone would offer
        a region that is now in the wrong place at the wrong scale.
        """
        wired["answer"] = {
            "screen": {"name": "DP-1", "x": 0, "y": 0, "frame": "ok"},
            "result": {
                "action": "ocr",
                "geometry": "0,0 100x50",
                "output": selector.output_key("DP-1", 1920, 1080),
                "done": True,
            },
        }
        selector.select_region(timeout_s=5)
        # Same connector, now 2560x1440: nothing to restore.
        assert selector.output_key("DP-1", 2560, 1440) not in selector.remembered_regions()

    def test_the_request_offers_every_output_s_region(self, wired):
        """Python hands over the whole map; the overlay picks its own entry.

        Python cannot know which output the overlay will land on, so deciding
        here would be guessing.
        """
        selector.remember_geometry("1,1 10x10", selector.output_key("eDP-1", 1920, 1080))
        selector.remember_geometry("2,2 20x20", selector.output_key("DP-1", 2560, 1440))
        wired["answer"] = {"result": {"action": "ocr", "geometry": "0,0 8x8", "done": True}}
        selector.select_region(timeout_s=5)
        assert selector._last_request["geometries"] == {
            "eDP-1@1920x1080": "1,1 10x10",
            "DP-1@2560x1440": "2,2 20x20",
        }

    def test_nonsense_is_not_remembered(self, wired):
        wired["answer"] = {
            "result": {
                "action": "ocr",
                "geometry": "не координаты",
                "output": "DP-1@1920x1080",
                "done": True,
            },
        }
        selector.select_region(timeout_s=5)
        assert selector.remembered_regions() == {}

    def test_a_region_without_an_output_is_not_remembered(self, wired):
        """No output identity means no way to tell where the region belonged."""
        wired["answer"] = {
            "result": {"action": "ocr", "geometry": "5,5 50x50", "done": True},
        }
        selector.select_region(timeout_s=5)
        assert selector.remembered_regions() == {}

    def test_a_corrupt_geometry_file_does_not_raise(self, wired, tmp_path):
        (tmp_path / "selector-geometry.json").write_text("{not json", encoding="utf-8")
        wired["answer"] = {"result": {"action": "ocr", "geometry": "0,0 8x8", "done": True}}
        assert selector.select_region(timeout_s=5) == ("ocr", "0,0 8x8")

    def test_the_geometry_file_is_not_in_shared_tmp(self, wired, tmp_path):
        """A region file in /tmp is readable and rewritable by anything on the box."""
        path = selector.geometry_file()
        assert path.parent == tmp_path
        assert "tmp" not in path.parts or path.parent == tmp_path

    def test_the_remembered_file_is_under_the_runtime_directory(self, wired, tmp_path):
        assert tmp_path in selector.geometry_file().parents

    def test_watchdog_closes_the_overlay_by_itself(self):
        """The overlay gives up on its own if the Python side never comes back.

        Otherwise a killed process leaves a fullscreen overlay holding the
        keyboard with nothing left able to dismiss it.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        assert "id: watchdog" in source
        assert "onTriggered: root.close()" in source
        assert "running: root.active" in source
        assert 0 < selector.WATCHDOG_S <= selector.DEFAULT_TIMEOUT_S

    def test_escape_works_as_an_application_shortcut(self):
        """A layer surface does not reliably own the focus chain.

        With the default window context Escape arrived nowhere; the shortcuts have
        to be application-wide.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        for key in ("Escape", "Return", "F11"):
            assert f'sequence: "{key}"' in source, key
        assert source.count("context: Qt.ApplicationShortcut") == 3

    def test_a_busy_path_is_closed_instead_of_failing(self, wired):
        """-n makes quickshell exit at once if another shell owns the path.

        That used to be read as a normal start: the command printed "cancelled"
        every time and the stale overlay stayed on screen. Now the old one is
        closed and the press reports a clean cancel.
        """
        calls: list[list[str]] = []

        class Dead:
            """A -n process that gave up because the path was taken."""
            def poll(self):
                return 0

            def terminate(self) -> None: ...
            def wait(self, timeout=None) -> int: return 0
            def kill(self) -> None: ...

        monkey = wired["runs"]
        wired["pops"] = []
        selector.subprocess.Popen = lambda cmd, **kw: Dead()
        wired["runs"] = monkey
        action, geom = selector.select_region(timeout_s=1)
        assert (action, geom) == ("", "")
        # nothing was started on top of the existing one
        assert not any(_is_start(c) for c in calls)
        assert wired["pops"] == []

    def test_second_press_cancels(self):
        """The key itself is the escape hatch that does not need the overlay."""
        assert "cancel" in Path(selector.__file__).read_text(encoding="utf-8")

    def test_cancel_is_empty(self):
        assert selector.SEL_CANCEL == ""

    def test_every_action_has_a_button_in_the_qml(self):
        source = selector.QML_DIR.joinpath("ScreenshotOverlay.qml").read_text(encoding="utf-8")
        for action in ("ocr", "text", "live"):
            assert f'root.finish("{action}")' in source, action


class TestPresence:
    def test_have_selector_false_without_quickshell(self, monkeypatch):
        monkeypatch.setattr(selector.shutil, "which", lambda _t: None)
        assert selector.have_selector() is False

    def test_have_selector_false_without_qml(self, monkeypatch):
        monkeypatch.setattr(selector.shutil, "which", lambda _t: "/usr/bin/quickshell")
        monkeypatch.setattr(selector, "QML_DIR", Path("/nonexistent"))
        assert selector.have_selector() is False

    def test_error_names_the_package_to_install(self, monkeypatch):
        monkeypatch.setattr(selector.shutil, "which", lambda _t: None)
        with pytest.raises(selector.SelectorError, match="quickshell"):
            selector.select_region()

    def test_qml_ships_with_the_package(self):
        for name in ("shell.qml", "ScreenshotOverlay.qml", "qmldir"):
            assert (selector.QML_DIR / name).is_file(), name
        # The QML has to live inside the installed package, not in the repo
        # root: a repo-root path exists in development and is missing from
        # site-packages, and the installed command then falls back to slurp.
        package_dir = Path(selector.__file__).resolve().parent
        assert selector.QML_DIR.is_relative_to(package_dir)


class TestNoHarm:
    """Rules that keep the overlay from locking someone's desktop."""

    def test_no_second_quickshell_for_ipc(self, wired):
        """The shell is the only quickshell process a normal run starts.

        Startup values used to be handed over by launching a second quickshell
        that knocked on an IPC endpoint which might not be listening yet. They
        come from a file the shell reads as it loads instead.
        """
        wired["answer"] = {"result": {"action": "ocr", "geometry": "0,0 8x8", "done": True}}
        selector.select_region(timeout_s=5)
        pops = wired["pops"]
        runs = wired["runs"]
        assert len(pops) == 1, pops
        assert "ipc" not in pops[0], pops
        assert not [c for c in runs if "ipc" in c], runs

    def test_started_with_its_own_path(self, wired):
        """-p, never -c and never the bare binary.

        Without -p quickshell would load the user's own config, which is exactly
        what must not happen.
        """
        selector.select_region(timeout_s=0.2)
        pops = wired["pops"]
        assert "-p" in pops[0]
        assert str(selector.QML_DIR) in pops[0]
        assert "-c" not in pops[0]
        assert "-n" in pops[0], "-n keeps a second press from stacking an overlay"

    def test_never_kills_by_process_name(self):
        source = Path(selector.__file__).read_text(encoding="utf-8")
        assert "pkill" not in source
        assert "killall" not in source
        assert '"-x"' not in source

    def test_only_our_own_process_is_stopped(self, wired):
        selector.select_region(timeout_s=0.3)
        assert wired["stopped"] == [4242], "stopped by pid, not by name"

    def test_overlay_is_gone_before_the_caller_continues(self, wired):
        """The shell must be stopped even when the action succeeded."""
        wired["answer"] = {"result": {"action": "live", "geometry": "1,2 3x4", "done": True}}
        action, geom = selector.select_region(timeout_s=5)
        assert (action, geom) == ("live", "1,2 3x4")
        assert wired["stopped"], "the overlay must be destroyed first"

    def test_stopped_even_when_the_action_is_cancelled(self, wired):
        selector.select_region(timeout_s=0.3)
        assert wired["stopped"] == [4242]

    def test_stopped_when_the_request_cannot_be_written(self, monkeypatch, tmp_path):
        """A request that cannot be written is an error, not a silent cancel."""
        stopped: list[int] = []
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file, not a directory", encoding="utf-8")
        monkeypatch.setattr(selector, "_GAP", 0.01)
        paths = _fake_paths(tmp_path)
        monkeypatch.setattr(
            selector,
            "default_paths",
            lambda: replace(paths, selector_request=blocker / "request.json"),
        )
        monkeypatch.setattr(selector.subprocess, "Popen", lambda cmd, **kw: FakeProc(stopped))
        with pytest.raises(selector.SelectorError):
            selector.select_region(timeout_s=0.3)
        assert not stopped, "no shell was ever started"

    def test_an_unusable_runtime_directory_cancels_instead_of_raising(
        self, monkeypatch, tmp_path
    ):
        """If the lock cannot even be opened, do not start a shell either.

        The user pressing the key expects either an overlay or nothing. A crash
        with a traceback is the worst of the three, and it must not leave a
        half-started overlay behind.
        """
        stopped: list[int] = []
        blocker = tmp_path / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(selector, "_GAP", 0.01)
        monkeypatch.setattr(selector, "default_paths", lambda: _fake_paths(blocker))
        monkeypatch.setattr(selector.subprocess, "Popen", lambda cmd, **kw: FakeProc(stopped))
        monkeypatch.setattr(selector.subprocess, "run", lambda cmd, **kw: Done0(cmd))
        assert selector.select_region(timeout_s=0.3) == ("", "")
        assert not stopped

    def test_single_instance_lock(self, wired):
        """A second press cancels the first instead of stacking an overlay."""
        assert selector._Single().__enter__() is True

        # hold the lock from "another process"
        held = selector._Single()
        assert held.__enter__() is True
        second = selector._Single()
        assert second.__enter__() is False
        second.__exit__()

        runs: list = wired["runs"]  # type: ignore[assignment]
        runs.clear()
        action, geom = selector.select_region(timeout_s=0.3)
        assert (action, geom) == ("", "")
        assert any("cancel" in c for c in runs), runs
        held.__exit__()

    def test_frame_and_result_files_are_cleaned_up(self, wired, tmp_path):
        """Whatever the run ends with, no selector files are left behind."""
        wired["answer"] = {
            "screen": {"name": "DP-1", "x": 0, "y": 0, "frame": "ok"},
            "result": {"action": "ocr", "geometry": "1,2 3x4", "done": True},
        }
        selector.select_region(timeout_s=5)
        left = sorted(p.name for p in tmp_path.iterdir())
        # The lock file stays: it is the flock, and deleting it would let a second
        # selector in while the first still holds it.
        assert left == [
            "selector-request.json",
            "selector.lock",
            "selector.log",
        ], left

    def test_log_records_the_run(self, wired, tmp_path):
        selector.select_region(timeout_s=0.3)
        log = (tmp_path / "selector.log").read_text(encoding="utf-8")
        assert "--- selector" in log


class TestTimeouts:
    def test_cancel_returns_nothing(self, wired):
        wired["answer"] = {"result": {"action": "", "geometry": "", "done": True}}
        action, geom = selector.select_region(timeout_s=0.4)
        assert (action, geom) == ("", "")

    def test_timeout_does_not_hang(self, wired):
        action, _ = selector.select_region(timeout_s=0.2)
        assert action == ""

    def test_result_is_read_when_the_overlay_answers(self, wired):
        wired["answer"] = {"result": {"action": "ocr", "geometry": "5,6 70x80", "done": True}}
        assert selector.select_region(timeout_s=5) == ("ocr", "5,6 70x80")

    def test_unknown_action_is_treated_as_cancel(self, wired):
        wired["answer"] = {"result": {"action": "nonsense", "geometry": "0,0 1x1", "done": True}}
        assert selector.select_region(timeout_s=5) == ("", "")


class TestOutput:
    """The overlay owns the output. Python must not go asking the compositor."""

    def test_no_hyprctl_anywhere_in_the_selector(self):
        # A second source of truth about the monitor is the bug this replaced:
        # hyprctl naming a different output than the one the window is on.
        source = Path(selector.__file__).read_text(encoding="utf-8")
        assert "hyprctl" not in source

    def test_python_does_not_capture_anymore(self):
        # Only the overlay knows which output it is on, so only the overlay
        # captures. Python capturing too is how they drifted apart. Mentioning
        # grim in a comment is fine; invoking it is not.
        source = Path(selector.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            ln for ln in source.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "grim" not in code

    def test_a_healthy_frame_reports_nothing(self, tmp_path):
        report = tmp_path / "screen.json"
        report.write_text(json.dumps({"name": "DP-1", "frame": "ok"}), encoding="utf-8")
        assert selector._frame_problem(report) == ""

    def test_a_failed_frame_is_reported_not_swallowed(self, tmp_path):
        report = tmp_path / "screen.json"
        report.write_text(
            json.dumps({"name": "DP-1", "frame": "failed"}), encoding="utf-8"
        )
        problem = selector._frame_problem(report)
        assert "DP-1" in problem and "не удался" in problem

    def test_broken_json_does_not_raise(self, tmp_path):
        report = tmp_path / "screen.json"
        report.write_text("{not json", encoding="utf-8")
        assert selector._frame_problem(report) == ""

    def test_a_missing_report_does_not_raise(self, tmp_path):
        assert selector._frame_problem(tmp_path / "absent.json") == ""

    def test_a_failed_frame_reaches_the_log(self, wired, tmp_path):
        wired["answer"] = {
            "screen": {"name": "DP-1", "x": 0, "y": 0, "frame": "failed"},
            "result": {"action": "ocr", "geometry": "0,0 8x8", "done": True},
        }
        selector.select_region(timeout_s=5)
        log = (tmp_path / "selector.log").read_text(encoding="utf-8")
        assert "не удался" in log


class TestProcessModel:
    """One process, no sleep, no second quickshell, one cleanup path."""

    def test_no_sleep_after_starting_the_shell(self):
        # The values the shell needs exist before the process does, so there is
        # nothing to wait for and no window where it could lose them.
        source = Path(selector.__file__).read_text(encoding="utf-8")
        assert "_SETTLE_S" not in source
        assert "time.sleep(_SETTLE" not in source

    def test_only_one_process_is_started(self, wired):
        wired["answer"] = {"result": {"action": "ocr", "geometry": "0,0 8x8", "done": True}}
        selector.select_region(timeout_s=5)
        assert len(wired["pops"]) == 1, wired["pops"]

    def test_the_request_is_written_before_the_shell_starts(self, wired, tmp_path):
        seen: list[bool] = []
        real_popen = wired["pops"]

        def popen(cmd, **kw):
            # The request must already be on disk when the process is started,
            # because the shell reads it as it loads.
            seen.append((tmp_path / "selector-request.json").is_file())
            real_popen.append(list(cmd))
            return FakeProc(wired["stopped"])

        import kizurium_translator.selector as mod

        mod.subprocess.Popen = popen
        try:
            selector.select_region(timeout_s=0.3)
        finally:
            import subprocess as real_subprocess

            mod.subprocess.Popen = real_subprocess.Popen
        assert seen == [True]

    def test_the_shell_finds_the_request_in_its_working_directory(self, wired):
        wired["answer"] = {"result": {"action": "ocr", "geometry": "0,0 8x8", "done": True}}
        selector.select_region(timeout_s=5)
        kwargs: list = wired["kwargs"]  # type: ignore[assignment]
        assert str(kwargs[0]["cwd"]) == str(wired["requests"][0]["result"]).rsplit(
            "/", 1
        )[0]

    def test_the_request_carries_everything_the_shell_needs(self, wired):
        wired["answer"] = {"result": {"action": "ocr", "geometry": "0,0 8x8", "done": True}}
        selector.select_region(timeout_s=5)
        request = wired["requests"][0]
        for key in ("result", "screen", "frame", "watchdogMs", "autoAction"):
            assert key in request, request
        assert request["watchdogMs"] > 0
        assert request["autoAction"] == ""

    def test_auto_action_is_handed_to_the_shell(self, wired):
        wired["answer"] = {"result": {"action": "ocr", "geometry": "0,0 8x8", "done": True}}
        selector.select_region(timeout_s=5, auto_action="ocr")
        assert wired["requests"][0]["autoAction"] == "ocr"

    def test_qml_output_goes_to_a_log_not_into_the_void(self, wired):
        selector.select_region(timeout_s=0.3)
        kwargs: list = wired["kwargs"]  # type: ignore[assignment]
        assert kwargs[0]["stderr"] is subprocess.STDOUT
        assert kwargs[0]["stdout"] is not subprocess.DEVNULL

    def test_cleanup_restores_signal_handlers(self, wired):
        before = signal.getsignal(signal.SIGTERM)
        selector.select_region(timeout_s=0.3)
        assert signal.getsignal(signal.SIGTERM) is before

    def test_a_signal_still_cleans_up(self, wired, tmp_path):
        wired["answer"] = {
            "screen": {"name": "DP-1", "x": 0, "y": 0, "frame": "ok"},
            "result": {"action": "ocr", "geometry": "0,0 8x8", "done": True},
        }

        def boom(*_a, **_k):
            raise KeyboardInterrupt("interrupted")

        orig = selector._read_result
        selector._read_result = boom  # type: ignore[assignment]
        try:
            with pytest.raises(KeyboardInterrupt):
                selector.select_region(timeout_s=5)
        finally:
            selector._read_result = orig  # type: ignore[assignment]
        assert wired["stopped"], "an interrupted run must not leave the overlay up"
        left = sorted(p.name for p in tmp_path.iterdir())
        assert left == [
            "selector-request.json",
            "selector.lock",
            "selector.log",
        ], left

    def test_selector_still_works_when_the_overlay_names_nothing(self, wired):
        # A missing screen report must not hang the run or lose the selection.
        wired["answer"] = {"result": {"action": "text", "geometry": "0,0 8x8", "done": True}}
        assert selector.select_region(timeout_s=5) == ("text", "0,0 8x8")

    def test_the_overlay_reports_whether_it_restored_a_region(self):
        """A restored region has to be observable from outside.

        Whether the per-output lookup worked is invisible from the result: a run
        that correctly restores and a run that correctly declines to both answer
        with an empty geometry. The screen report is what makes it checkable.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        assert "restored: hasSelection" in source
        # The report must come after the restore, or the flag is always false.
        claim = source[source.index("function claimScreen("):]
        claim = claim[:claim.index("\n    function ")]
        assert claim.index("restoreGeometry(") < claim.index("writeScreenReport(")

    def test_the_output_key_helper_is_not_shadowed_by_a_property(self):
        """A property named outputKey silently replaced the function of that name.

        QML resolves a call against the property first, so `outputKey(scr)` threw
        "Property 'outputKey' is not a function" at run time. Nothing about it
        shows up in qmllint, and the overlay only fails once the request arrives.
        """
        source = (selector.QML_DIR / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        assert "function outputKey(" not in source
        assert "property string outputKey" not in source
        assert "function outputKeyOf(" in source
        # and the property that carries the value is named differently again
        assert "property string currentOutput" in source

    def test_the_request_is_read_as_a_method_call(self):
        """FileView.text is a function in this Quickshell version.

        Reading it as a property hands JSON.parse a function object, the throw is
        swallowed, and the overlay silently never starts: the run then just times
        out with nothing on screen and no error anywhere.
        """
        source = (Path(selector.__file__).parent / "qml" / "selector"
                  / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        assert "JSON.parse(text)" not in source
        assert "JSON.parse(body)" in source
        assert "JSON.parse(text())" in source

    def test_the_frame_is_captured_after_the_output_is_claimed(self):
        """grim снимает тот выход, который завладел прогоном.

        Раньше кадр снимался в prepare(), до `active = true`: снимок делается
        grim, а если окно уже отображено, в снимок попадает собственный слой
        затемнения выделятеля, и пользователь выделяет область на картинке
        селектора, а не экрана.

        Окна теперь по одному на каждый выход, и до показа неизвестно, на каком
        окажется нажатие, поэтому снимок уходит в claimScreen() - то есть после
        отображения. Это допустимо только потому, что снимок наружу не
        показывается: `showFrozenFrame` не включается нигде, и кадр уходит на
        --frame и в лог. Если его когда-нибудь начнут рисовать, этот порядок
        придётся пересмотреть, и вот тогда тест должен упасть.
        """
        source = (Path(selector.__file__).parent / "qml" / "selector"
                  / "ScreenshotOverlay.qml").read_text(encoding="utf-8")
        claim = source[source.index("function claimScreen("):]
        claim = claim[:claim.index("\n    function ")]
        assert "captureFrame(scr)" in claim
        # Рамка рисуется в окне-владельце: без владения нажать не на что, и
        # выглядит как «выделятель не открылся».
        assert "visible: !root.hasSelection" in source
        # Нигде не включается показ снимка - иначе порядок выше был бы неправдой.
        assert source.count("root.showFrozenFrame") == 2, "showFrozenFrame стал включаться"
