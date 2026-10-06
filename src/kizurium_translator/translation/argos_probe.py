"""Argos Translate offline probe

Argos remains an *optional candidate*, not a core backend. Before it can be
wired into strict offline mode we must prove:

```text
network blocked → local translation still works
```

If the installed Argos stack still reaches the network (helper models,
package index, etc.), ``strict_offline_allowed`` stays False and Argos
must not be treated as an offline guarantee.

This module never registers itself on the live Translator path.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ArgosProbeResult:
    """Outcome of an Argos availability / offline probe."""

    available: bool
    offline_ok: bool
    strict_offline_allowed: bool
    reason: str
    sample: str = ""


class _NetworkBlocked(Exception):
    """Raised when a deliberate offline probe hits the network."""


def _block_network(monkeypatch_setattr: Callable | None = None) -> Callable[[], None]:
    """Install a socket guard that fails any real connect.

    Returns an undo callback. Tests may pass ``monkeypatch.setattr``; production
    uses a local swap of ``socket.socket.connect``.
    """
    real_connect = socket.socket.connect

    def blocked(self, address):  # noqa: ANN001
        raise _NetworkBlocked(f"network blocked during Argos probe: {address!r}")

    if monkeypatch_setattr is not None:
        monkeypatch_setattr(socket.socket, "connect", blocked)

        def undo() -> None:
            return None

        return undo

    socket.socket.connect = blocked  # type: ignore[method-assign]

    def undo() -> None:
        socket.socket.connect = real_connect  # type: ignore[method-assign]

    return undo


def probe_argos(
    *,
    source: str = "en",
    target: str = "ru",
    text: str = "Hello",
    force_offline: bool = True,
) -> ArgosProbeResult:
    """Probe whether Argos is installed and usable without the network.

    Does not install packages or download language models. A missing Argos
    install or missing language pack is reported as ``available=False``.
    """
    try:
        import argostranslate.translate as argos_translate
    except ImportError:
        return ArgosProbeResult(
            available=False,
            offline_ok=False,
            strict_offline_allowed=False,
            reason="argostranslate not installed (optional candidate only)",
        )

    undo = (lambda: None)
    if force_offline:
        undo = _block_network()
    try:
        try:
            got = argos_translate.translate(text, source, target)
        except _NetworkBlocked as exc:
            return ArgosProbeResult(
                available=True,
                offline_ok=False,
                strict_offline_allowed=False,
                reason=f"hidden network dependency: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - probe must never raise to callers
            return ArgosProbeResult(
                available=True,
                offline_ok=False,
                strict_offline_allowed=False,
                reason=f"argos translate failed offline: {type(exc).__name__}: {exc}",
            )
        if not (got or "").strip():
            return ArgosProbeResult(
                available=True,
                offline_ok=False,
                strict_offline_allowed=False,
                reason="argos returned empty translation (language pack missing?)",
            )
        return ArgosProbeResult(
            available=True,
            offline_ok=True,
            strict_offline_allowed=True,
            reason="argos translated with network blocked",
            sample=got.strip(),
        )
    finally:
        undo()


def argos_allowed_in_strict_offline() -> bool:
    """Offline gate: only True after a successful offline probe."""
    return probe_argos(force_offline=True).strict_offline_allowed
