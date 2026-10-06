"""full re-anchor triggers (no tracked / scene / dirty / match / periodic).

"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.change import (  # noqa: E402
    REANCHOR_DIRTY_REGIONS_MAX,
    REANCHOR_MATCH_RATE_MIN,
    REANCHOR_PERIODIC_S,
    needs_full_reanchor,
)


def test_phase43_starting_points():
    assert REANCHOR_DIRTY_REGIONS_MAX == 8
    assert REANCHOR_MATCH_RATE_MIN == 0.65
    assert REANCHOR_PERIODIC_S == 5.0


def test_reanchor_reasons():
    assert needs_full_reanchor(tracked_count=0) == (True, "no_tracked")
    assert needs_full_reanchor(tracked_count=3, strong_scene_change=True) == (
        True,
        "scene_change",
    )
    assert needs_full_reanchor(tracked_count=3, dirty_region_count=9) == (
        True,
        "many_dirty",
    )
    assert needs_full_reanchor(tracked_count=3, match_rate=0.5) == (True, "low_match")
    assert needs_full_reanchor(tracked_count=3, seconds_since_full=5.0) == (
        True,
        "periodic",
    )
    assert needs_full_reanchor(tracked_count=3, dirty_region_count=2, match_rate=0.9) == (
        False,
        "",
    )


def test_tracking_match_rate():
    from kizurium_translator.live.tracking import tracking_match_rate

    assert tracking_match_rate(10, 10, 10) == 1.0
    assert tracking_match_rate(10, 5, 10) == 0.5
    assert tracking_match_rate(4, 4, 10) == 0.4
    assert needs_full_reanchor(
        tracked_count=10, match_rate=tracking_match_rate(10, 5, 10)
    ) == (True, "low_match")
