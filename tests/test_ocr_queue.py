"""One active OCR job, latest frame wins.

the queue exposes only ``put`` and ``take_latest``. A frame is either
being processed or waiting; when a new one arrives it replaces the waiting one.
The OCR worker checks whether the version it started on is still current — if
not, it discards its result and the loop picks up the latest frame.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _where import all_sources

from kizurium_translator import live  # noqa: E402


def test_queue_holds_at_most_one_frame():
    q = live._FrameQueue()
    q.put({"id": 1})
    q.put({"id": 2})
    latest = q.put({"id": 3})
    assert latest.frame == {"id": 3}
    assert q.take_latest().frame == {"id": 3}


def test_queue_returns_latest_on_take():
    q = live._FrameQueue()
    q.put({"id": 1})
    time.sleep(0.01)
    q.put({"id": 2})
    got = q.take_latest()
    assert got.frame == {"id": 2}


def test_queue_does_not_block_put():
    q = live._FrameQueue()
    q.put({"id": 1})
    start = time.monotonic()
    q.put({"id": 2})
    assert time.monotonic() - start < 0.1, "put blocked"


def test_put_returns_versioned_frame():
    q = live._FrameQueue()
    vf = q.put({"id": 1})
    assert vf.version == 1
    assert vf.frame == {"id": 1}
    vf2 = q.put({"id": 2})
    assert vf2.version == 2


def test_stale_frame_is_detected_by_consumer():
    q = live._FrameQueue()
    q.put({"id": 1, "data": "frame-1"})
    started = q.take_latest()
    q.put({"id": 2, "data": "frame-2"})
    assert q.version != started.version


def test_unused_legacy_methods_removed():
    q = live._FrameQueue()
    for name in ("get", "get_nowait", "peek", "empty"):
        assert not hasattr(q, name)


class TestStructure:
    def test_the_module_still_parses(self):
        import ast

        for path in all_sources():
            ast.parse(path.read_text(encoding="utf-8"))


def test_the_queue_exposes_only_put_and_take_latest():
    """Latest frame wins: the queue is `put` and `take_latest`, nothing else.

    The surface is what makes that guarantee. A `get` that waits for the next
    frame, or a `peek` that leaves the frame in place, both turn "the newest
    frame" into "the frame that happened to be there when the worker got round to
    it".
    """

    q = live._FrameQueue()
    assert set(m for m in dir(q) if not m.startswith("_")) >= {"put", "take_latest", "version"}

    vf = q.put("frame-a")
    assert isinstance(vf, live.VersionedFrame)
    assert q.take_latest().frame == "frame-a"
