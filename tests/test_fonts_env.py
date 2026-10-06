"""Fontconfig / GTK env hygiene used at CLI startup.

Regression: a blank ``FcConfigCreate`` had no cache dirs, so ``BuildFonts``
printed ``No writable cache directories`` once per bundled face, and the old
quiet helper rewrote ``XDG_CACHE_HOME`` to its parent (``~/.cache`` → ``~``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.fonts import ensure_fontconfig_cache, prepare_gui_env


def test_ensure_fontconfig_cache_keeps_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    path = ensure_fontconfig_cache()
    assert path == tmp_path / "fontconfig"
    assert path.is_dir()
    assert os.environ["XDG_CACHE_HOME"] == str(tmp_path)


def test_prepare_gui_env_sets_gtk_a11y(monkeypatch):
    monkeypatch.delenv("GTK_A11Y", raising=False)
    prepare_gui_env()
    assert os.environ["GTK_A11Y"] == "none"


def test_font_registration_is_silent(tmp_path):
    """Fresh subprocess: registration must not spam fontconfig on stderr."""
    env = dict(os.environ)
    env["XDG_CACHE_HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    code = """
from kizurium_translator import fonts
fonts._STATE.done = False
fonts._STATE.config = None
fonts._STATE.font_map = None
fonts._STATE.available = set()
fonts._STATE.reason = ""
from kizurium_translator.fonts import prepare_gui_env, ensure_registered, _STATE
prepare_gui_env()
ensure_registered()
assert _STATE.available, _STATE.reason
"""
    res = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    assert "Fontconfig" not in res.stderr
    assert "No writable cache" not in res.stderr
