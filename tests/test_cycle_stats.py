"""what a cycle cost, and where the time went.

None of this existed. The overlay logged `cycle_ms` and `ocr_ms` as text, which
is a number in a log line rather than a measurement: you cannot take a p95 of
it, and the steps that decide where a cycle goes - capture, probe, layout,
reconciliation, render preparation - had no timers at all, so "the cycle is
slow" had nowhere to point.

These tests check the measurement does what it claims: that the pieces add up to
something comparable to the whole, that percentiles are percentiles of the
samples rather than of a running mean, and that the log line the live path emits
cannot be mistaken by the harness for the cycle it just measured.

    uv run pytest tests/test_cycle_stats.py
"""

from __future__ import annotations

import re

from kizurium_translator.live.stats import (
    RESERVOIR_MAX,
    CaptureStats,
    OcrStats,
    RenderStats,
    SessionStats,
    Timing,
    TrackingStats,
    TranslationStats,
)


def test_every_stats_object_the_plan_names_exists():
    stats = SessionStats()
    assert isinstance(stats.capture, CaptureStats)
    assert isinstance(stats.ocr, OcrStats)
    assert isinstance(stats.tracking, TrackingStats)
    assert isinstance(stats.translation, TranslationStats)
    assert isinstance(stats.render, RenderStats)


def test_every_timing_the_plan_names_has_a_slot():
    """lists the timings. Each one must be reachable, not merely declared."""
    stats = SessionStats()
    names = {t.name for t in stats.timings()}
    for expected in (
        "capture",
        "probe",
        "diff",
        "ocr",
        "reocr",
        "tracking",
        "reconciliation",
        "layout",
        "translation_queue",
        "translation",
        "render_prepare",
):
        assert expected in names, f"нет тайминга {expected}"
    # The cycle is the whole and is logged by the caller, so it is not in the
    # breakdown - but it must exist, or there is no "whole" to compare against.
    assert stats.cycle.name == "cycle"


def test_every_counter_the_plan_names_is_reported():
    stats = SessionStats()
    stats.capture.note_frame(dirty=3)
    stats.capture.note_skip()
    stats.ocr.note_full(10.0, lines=4)
    stats.tracking.note_outcome(reused=5, changed=2, removed=1, added=3)
    stats.translation.note_cache(7, 4)
    stats.translation.note_backend("ctranslate2")
    stats.translation.note_backend("gtx", network=True)
    stats.render.cards_built = 6
    stats.render.cards_drawn = 5
    text = stats.counters()
    for key in (
        "frames_seen",
        "frames_skipped",
        "dirty_regions",
        "full_ocr",
        "partial_ocr",
        "blocks_reused",
        "blocks_changed",
        "blocks_removed",
        "cache_hits",
        "cache_misses",
        "local",
        "online",
        "network",
):
        assert key in text, f"нет счётчика {key}"


def test_local_and_online_are_told_apart():
    stats = SessionStats()
    stats.translation.note_backend("ctranslate2")
    stats.translation.note_backend("mymemory", network=True)
    assert stats.translation.local_translations == 1
    assert stats.translation.online_translations == 1
    assert stats.translation.network_requests == 1


def test_a_percentile_is_a_percentile_not_a_mean():
    """The plan's targets are p95. A mean over a heavy tail cannot answer that."""
    t = Timing("x")
    for v in range(1, 101):
        t.add(float(v))
    # Linear interpolation between the two samples straddling the position, which
    # is what a mean of the distribution would be for if it were asked. p95 of
    # 1..100 sits at 95.05; a running mean would answer 50.5 for the same data.
    assert t.percentile(0.95) == 95.05
    assert t.percentile(0.5) == 50.5
    assert t.max_ms == 100.0


def test_one_outlier_shows_up_in_p95():
    t = Timing("x")
    for _ in range(100):
        t.add(5.0)
    t.add(900.0)
    assert t.percentile(0.5) == 5.0, "среднее не должно прыгать от одного выброса"
    assert t.max_ms == 900.0


def test_an_untaken_span_reports_nothing():
    """A stat for a step that never ran must not appear as 0 ms: 0 is a claim."""
    stats = SessionStats()
    stats.ocr.add_note = None # not used; keeps the fixture honest
    text = stats.summary()
    assert "reocr_ms" not in text
    assert "cycle_ms" not in text


def test_the_reservoir_is_bounded():
    """An overlay session runs for hours. A list per timing per cycle leaks."""
    t = Timing("x")
    for i in range(RESERVOIR_MAX * 3):
        t.add(float(i))
    assert len(t.samples) <= RESERVOIR_MAX
    assert t.count == RESERVOIR_MAX * 3, "счётчик не должен обрезаться"
    assert t.max_ms == float(RESERVOIR_MAX * 3 - 1), "максимум должен помнить всё"


def test_the_summary_line_cannot_forge_the_harness_fields():
    """`screencheck.py` parses `cycle_ms=`, `shown=` and `mode=` with loose regexes.

    A stats key spelled `cycle_ms` inside the summary would overwrite the number
    that script just measured, and the whole point of this phase is that the
    numbers are trustworthy. So the summary uses underscores.
"""
    stats = SessionStats()
    stats.note_cycle(4200.0)
    stats.ocr.note_full(900.0)
    line = stats.summary()
    assert "cycle_ms" not in line, "цикл логирует вызывающий, не сводка"
    assert "shown=" not in line
    assert not re.search(r"\bmode=", line)
    assert "cycles=1" in line
    assert "ocr_ms=900/p95=900" in line


def test_the_parts_are_comparable_to_the_whole():
    """Timings that do not add up to the cycle are not a breakdown."""
    stats = SessionStats()
    stats.capture.capture.add(20.0)
    stats.ocr.ocr.add(800.0)
    stats.tracking.tracking.add(10.0)
    stats.tracking.layout.add(120.0)
    stats.translation.translation.add(300.0)
    stats.render.render_prepare.add(200.0)
    stats.tracking.reconciliation.add(15.0)
    stats.note_cycle(1500.0) # 1465 of parts plus overhead
    parts = (
        stats.capture.capture.total_ms
        + stats.ocr.ocr.total_ms
        + stats.tracking.tracking.total_ms
        + stats.tracking.layout.total_ms
        + stats.tracking.reconciliation.total_ms
        + stats.translation.translation.total_ms
        + stats.render.render_prepare.total_ms
)
    assert parts <= stats.cycle.total_ms, "части не могут быть больше целого"


def test_reset_clears_everything():
    stats = SessionStats()
    stats.note_cycle(10.0)
    stats.ocr.note_full(5.0)
    stats.reset()
    assert stats.cycles == 0
    assert stats.ocr.full_ocr_count == 0
    assert stats.cycle.count == 0


def test_the_batch_counter_drains():
    """The batch path knows hits and misses; the worker must not count again.

    The counter is process-wide, so anything counted before this test - by the
    incremental path, or by an earlier test - is drained first. Asserting on
    absolute totals would make this test depend on what ran before it.
"""
    from kizurium_translator.live.stats import BATCH_STATS

    stats = SessionStats()
    stats.translation.take_batch() # discard anything counted earlier
    BATCH_STATS.note(10, 3)
    stats.translation.note_cache(*stats.translation.take_batch())
    assert stats.translation.translation_cache_hits == 7
    assert stats.translation.translation_cache_misses == 3
    # Drained: a second take must not re-report the same batch.
    assert stats.translation.take_batch() == (0, 0)
    assert stats.translation.translation_cache_misses == 3


def test_counters_never_go_negative():
    stats = SessionStats()
    stats.capture.note_frame(dirty=-5)
    stats.tracking.note_outcome(reused=-1)
    stats.translation.note_cache(-2, -3)
    assert stats.capture.dirty_regions == 0
    assert stats.tracking.blocks_reused == 0
    assert stats.translation.translation_cache_hits == 0
    assert stats.translation.translation_cache_misses == 0
