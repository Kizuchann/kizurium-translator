"""Preload gtk4-layer-shell so GI can resolve symbols on strict linkers.

On Arch the soname is on the default linker path. On Nix the library lives
under /nix/store and must be found via find_library / LD_LIBRARY_PATH
(flake wrapProgram sets the latter). Never assume /usr/lib.
"""

from __future__ import annotations

from ctypes import CDLL, util
from typing import Iterable


def _candidate_names() -> Iterable[str]:
    found = util.find_library("gtk4-layer-shell")
    if found:
        yield found
    yield "libgtk4-layer-shell.so.0"
    yield "libgtk4-layer-shell.so"


def preload() -> bool:
    """Return True if the shared library loaded (or was already loadable)."""
    for name in _candidate_names():
        try:
            CDLL(name)
            return True
        except OSError:
            continue
    return False
