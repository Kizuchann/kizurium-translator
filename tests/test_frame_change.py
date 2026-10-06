"""overlay masking on probe diffs (union current+previous, pad 4–8).

"""

import ast
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.change import (  # noqa: E402
    OVERLAY_MASK_PAD_PX,
    apply_overlay_mask,
    frame_changed,
    probe_changed,
    union_overlay_boxes,
)


def test_phase37_pad_in_range():
    assert 4 <= OVERLAY_MASK_PAD_PX <= 8


def test_union_overlay_boxes_merges_current_and_previous():
    cur = [{"x": 10, "y": 10, "src_w": 40, "src_h": 16}]
    prev = [
        {"x": 10, "y": 10, "src_w": 40, "src_h": 16},  # dup
        {"x": 100, "y": 20, "w": 30, "h": 12},
    ]
    got = union_overlay_boxes(cur, prev)
    assert len(got) == 2
    xs = {(b["x"], b["y"]) for b in got}
    assert (10, 10) in xs and (100, 20) in xs


def test_overlay_mask_hides_card_motion():
    """Card-area paint alone must not count as frame change once masked."""
    a = Image.new("L", (64, 64), 40)
    b = a.copy()
    # Paint only inside the overlay card region on probe B.
    ImageDraw.Draw(b).rectangle((8, 8, 24, 16), fill=200)
    blocks = [{"x": 80, "y": 80, "src_w": 160, "src_h": 80}]  # maps onto that probe rect
    source = (640, 640)
    assert frame_changed(a, b) is True
    assert probe_changed(a, b, blocks, source) is False


def test_apply_overlay_mask_zeros_union_area():
    a = Image.new("L", (32, 32), 100)
    b = a.copy()
    ImageDraw.Draw(b).rectangle((0, 0, 15, 15), fill=255)
    boxes = [{"x": 0, "y": 0, "src_w": 16, "src_h": 16}]
    ma, mb = apply_overlay_mask(a, b, boxes, (32, 32), pad=0)
    # Masked corner is black on both; outside stays different if we dirtied it —
    # here only the box area changed, so masked frames match.
    assert ma.getpixel((0, 0)) == 0
    assert mb.getpixel((0, 0)) == 0


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.change import (  # noqa: E402
    DIRTY_FRACTION,
    DIRTY_MERGE_GAP_FULL_PX,
    DIRTY_PIXEL_THRESHOLD,
    dirty_regions,
    dirty_tile_coords,
    group_dirty_tiles_bfs,
    merge_close_rects,
)


def test_phase36_starting_points():
    assert DIRTY_PIXEL_THRESHOLD == 24.0
    assert DIRTY_FRACTION == 0.015
    assert 32 <= DIRTY_MERGE_GAP_FULL_PX <= 48


def test_dirty_tiles_detect_changed_cell():
    a = Image.new("L", (64, 64), 0)
    b = a.copy()
    ImageDraw.Draw(b).rectangle((0, 0, 15, 15), fill=255)
    tiles = dirty_tile_coords(a, b, tile_size=16)
    assert (0, 0) in tiles
    assert (3, 3) not in tiles


def test_bfs_groups_8_neighbours():
    # Two diagonal-touching tiles = one component; far tile = another.
    tiles = {(0, 0), (1, 1), (5, 5)}
    comps = group_dirty_tiles_bfs(tiles)
    assert len(comps) == 2
    sizes = sorted(len(c) for c in comps)
    assert sizes == [1, 2]


def test_merge_close_rects_joins_neighbours():
    a = (0, 0, 10, 10)
    b = (20, 0, 30, 10)  # gap 10
    far = (200, 0, 210, 10)
    merged = merge_close_rects([a, b, far], gap=12)
    assert len(merged) == 2
    assert (0, 0, 30, 10) in merged


def test_dirty_regions_two_blobs_merge_when_close():
    a = Image.new("L", (128, 64), 0)
    b = a.copy()
    d = ImageDraw.Draw(b)
    d.rectangle((0, 0, 15, 15), fill=255)
    d.rectangle((24, 0, 39, 15), fill=255)  # neighbouring tiles → one region after merge
    regions = dirty_regions(a, b, tile_size=16, source_size=(1280, 640))
    assert len(regions) >= 1
    # Scaled to full-res; should cover both blobs in one box when merge gap applies.
    x1, y1, x2, y2 = regions[0]
    assert x2 - x1 >= 30  # at least the span of both small rects in probe*scale


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live.change import (  # noqa: E402
    MOTION_LIKELY,
    TEXT_LIKELY,
    UNKNOWN,
    ChangeFeatures,
    classify_change,
)


def test_phase44_labels():
    assert TEXT_LIKELY == "TEXT_LIKELY"
    assert MOTION_LIKELY == "MOTION_LIKELY"
    assert UNKNOWN == "UNKNOWN"


def test_classify_text_likely():
    assert (
        classify_change(
            ChangeFeatures(
                changed_area=0.02,
                changed_tile_count=4,
                component_count=5,
                mean_component_size=80.0,
                edge_density=0.02,
            )
        )
        == TEXT_LIKELY
    )


def test_classify_motion_bar_and_cursor():
    assert (
        classify_change(
            ChangeFeatures(
                changed_area=0.05,
                component_count=1,
                mean_component_size=800.0,
                edge_density=0.05,
            )
        )
        == MOTION_LIKELY
    )
    assert (
        classify_change(
            ChangeFeatures(
                changed_area=0.01,
                component_count=1,
                mean_component_size=12.0,
                edge_density=0.01,
            )
        )
        == MOTION_LIKELY
    )


def test_classify_unknown_is_conservative():
    verdict = classify_change(
        ChangeFeatures(
            changed_area=0.03,
            changed_tile_count=20,
            component_count=12,
            mean_component_size=40.0,
            edge_density=0.002,
        )
    )
    assert verdict == UNKNOWN


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.live import change  # noqa: E402
from kizurium_translator.live.change import (  # noqa: E402
    PIPELINE_STAGES,
    dirty_block_ids,
    frame_diff_metrics,
    probe,
)
from kizurium_translator.live.state import TrackedBlock  # noqa: E402


def test_pipeline_stages_documented():
    assert PIPELINE_STAGES[0] == "probe"
    assert "mask_overlay" in PIPELINE_STAGES
    assert "partial_ocr" in PIPELINE_STAGES


def test_no_opencv_in_change_module():
    src = Path(change.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("cv2")
                assert alias.name != "opencv"
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("cv2")


def test_probe_is_cheap_grayscale_thumbnail():
    img = Image.new("RGB", (1920, 1080), (30, 40, 50))
    p = probe(img, side=320)
    assert p.mode == "L"
    assert max(p.size) <= 320


def test_frame_changed_and_masked_probe(monkeypatch):
    a = Image.new("L", (100, 100), 0)
    b = Image.new("L", (100, 100), 0)
    draw = ImageDraw.Draw(b)
    draw.rectangle((10, 10, 40, 40), fill=255)
    assert frame_changed(a, b, threshold=5.0) is True

    # Mask the changed box as an overlay card → should ignore that motion.
    blocks = [{"x": 10, "y": 10, "w": 30, "h": 30}]
    # With mask covering the white square, probes look identical in the rest.
    assert probe_changed(a, b, blocks, (100, 100), threshold=50.0) is False


def test_dirty_block_ids_associates_tracked_blocks():
    a = Image.new("L", (100, 100), 0)
    b = a.copy()
    ImageDraw.Draw(b).rectangle((20, 20, 50, 50), fill=200)
    blocks = [
        TrackedBlock(
            id=1,
            box=(20, 20, 50, 50),
            text="moved",
            norm="moved",
            script="latn",
            lang="en",
            conf=0.9,
            engine="rapid",
        ),
        TrackedBlock(
            id=2,
            box=(70, 70, 90, 90),
            text="still",
            norm="still",
            script="latn",
            lang="en",
            conf=0.9,
            engine="rapid",
        ),
    ]
    ids = dirty_block_ids(a, b, blocks, 100, 100, threshold=1.0)
    assert 1 in ids
    assert 2 not in ids


def test_frame_diff_metrics_returns_three_floats():
    a = Image.new("L", (60, 60), 10)
    b = Image.new("L", (60, 60), 40)
    g, c, tile = frame_diff_metrics(a, b)
    assert g > 0 and c > 0 and tile > 0


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.config import Config  # noqa: E402
from kizurium_translator.live.change import (  # noqa: E402
    PROBE_INTERVAL_S,
    PROBE_MAX_SIDE,
    PROBE_TILE_SIZE,
    tile_diff_metrics,
)


def test_phase34_starting_points():
    assert PROBE_INTERVAL_S == 0.11
    assert PROBE_MAX_SIDE == 320
    assert PROBE_TILE_SIZE == 16
    assert Config().probe_side == PROBE_MAX_SIDE


def test_tile_diff_mean_and_fraction():
    a = Image.new("L", (64, 64), 0)
    b = a.copy()
    ImageDraw.Draw(b).rectangle((0, 0, 15, 15), fill=255)
    mean, frac = tile_diff_metrics(a, b, tile_size=16, pixel_threshold=24.0)
    assert mean > 50
    assert frac > 0.5  # that 16×16 tile is mostly changed


def test_frame_diff_uses_16px_tiles():
    a = Image.new("L", (64, 64), 0)
    b = a.copy()
    ImageDraw.Draw(b).rectangle((48, 48, 63, 63), fill=200)
    _g, _c, tile = frame_diff_metrics(a, b)
    assert tile > 0
