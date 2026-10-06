"""Safe archive extraction rejects zip-slip paths."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from kizurium_translator.translation.packs import _extract_archive


def test_zip_slip_rejected(tmp_path: Path) -> None:
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../outside.txt", "nope")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        _extract_archive(evil, dest)
    assert not (tmp_path / "outside.txt").exists()


def test_normal_zip_ok(tmp_path: Path) -> None:
    zpath = tmp_path / "ok.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("model/config.json", "{}")
    dest = tmp_path / "out"
    dest.mkdir()
    _extract_archive(zpath, dest)
    assert (dest / "model" / "config.json").is_file()
