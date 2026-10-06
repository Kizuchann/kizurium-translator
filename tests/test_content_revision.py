""": same block_id keeps identity; content_revision bumps on text change."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.state import TrackedBlock  # noqa: E402
from kizurium_translator.live.tracking import (  # noqa: E402
    block_content_changed,
    punctuation_skeleton,
    track_blocks,
)


def _blk(text: str, box, *, rev: int = 0) -> TrackedBlock:
    return TrackedBlock(
        id=1,
        box=box,
        text=text,
        norm=text.casefold(),
        script="en",
        lang="en",
        conf=90,
        engine="rapid",
        content_revision=rev,
)


def test_punctuation_skeleton_ignores_words():
    assert punctuation_skeleton("Hello!") == "!"
    assert punctuation_skeleton("Hello?") == "?"
    assert punctuation_skeleton("Hello!") != punctuation_skeleton("Hello?")


def test_same_id_bumps_content_revision_on_text_change():
    prev = [_blk("Hello world", (0, 0, 80, 16), rev=2)]
    lines = [
    {
            "text": "Hello there",
            "box": (0, 0, 80, 16),
            "lang": "en",
        "conf": 90,
            "engine": "rapid",
}
]
    current, pairs, _ = track_blocks(prev, lines, now=1.0, next_id=2)
    assert len(pairs) == 1
    assert current[0].id == 1
    assert current[0].content_revision == 3


def test_punctuation_only_change_counts_as_content_change():
    old = _blk("Ready!", (0, 0, 40, 16))
    assert block_content_changed(old, "Ready?", "en", 90.0) is True


    # Async results carry revisions; a stale one is discarded rather than drawn.
    #
    # Kept from the revision tests after the phase-numbered files went away.
    # (from _merge_47.py)



import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translation.scheduler import (  # noqa: E402
    ResultIdentity,
    Scheduler,
    TranslationRequest,
)


def test_result_identity_fields():
    token = ResultIdentity(
        session_id="live",
        frame_revision=3,
        state_revision=7,
        block_id=12,
        content_revision=2,
)
    assert token.session_id == "live"
    assert token.frame_revision == 3
    assert token.state_revision == 7
    assert token.block_id == 12
    assert token.content_revision == 2


def test_accept_identity_rejects_stale_frame_and_content():
    sched = Scheduler()
    token = sched.capture_identity(block_id=1, content_revision=1)
    sched.note_block_content(1, 1)
    assert sched.accept_identity(token)

    sched.bump_frame()
    stamped = ResultIdentity(
        session_id=token.session_id,
        frame_revision=token.frame_revision or 1,
        state_revision=token.state_revision,
        block_id=1,
        content_revision=1,
)
    # Stamp a non-zero frame so the check applies.
    bad_frame = ResultIdentity(
        session_id=stamped.session_id,
        frame_revision=999,
        state_revision=stamped.state_revision,
        block_id=1,
        content_revision=1,
)
    assert not sched.accept_identity(bad_frame)

    sched.note_block_content(1, 2)
    stale_content = ResultIdentity(
        session_id=stamped.session_id,
        frame_revision=0,
        state_revision=stamped.state_revision,
        block_id=1,
        content_revision=1,
)
    assert not sched.accept_identity(stale_content)


def test_translation_request_identity_roundtrip():
    sched = Scheduler()
    rev = sched.capture()
    frame = sched.bump_frame()
    req = TranslationRequest(
        session_id=sched.session_id,
        state_revision=rev,
        block_id=4,
        content_revision=1,
        source="Hi",
        source_language="en",
        target_language="ru",
        text="Hi",
        frame_revision=frame,
)
    sched.note_block_content(4, 1)
    assert sched.accept(req)
    sched.bump()
    assert not sched.accept(req)
