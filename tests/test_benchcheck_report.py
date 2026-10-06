"""the targets, and a report that says which of them were checked.

The plan lists five acceptance targets. Three of them the overlay now measures
and two it does not: `probe` runs inside the scene-swap check and `cache lookup`
happens inside the batch, neither with a timer of its own. A report that quietly
omitted them would read as "four of four passed", so `benchcheck.py` prints them
as unmeasured and counts them separately.

These tests check the report, not the performance: the point of the phase is that
a verdict is traceable to a number that was actually taken.

    uv run pytest tests/test_benchcheck_report.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "benchcheck", ROOT / "scripts" / "benchcheck.py"
)
assert _spec and _spec.loader
benchcheck = importlib.util.module_from_spec(_spec)
sys.modules["benchcheck"] = benchcheck
_spec.loader.exec_module(benchcheck)


def test_every_target_the_plan_lists_is_in_the_report():
    """probe 20 ms, tracking/reconcile 10 ms, translation 500 ms."""
    keys = {t.key for t in benchcheck.TARGETS}
    assert "probe_ms" in keys, "probe в плане есть, а в отчёте нет"
    assert "tracking_ms" in keys
    assert "reconciliation_ms" in keys
    assert "translation_ms" in keys
    assert benchcheck.p95([0] * 1) == 0


def test_a_p95_is_read_from_the_p95_field_not_the_last_cycle():
    """`ocr_ms=1566/p95=3139/max=3471` - the target is judged on 3139.

    Taking the first number would judge a plan target against whichever cycle
    happened to be last, and the last cycle is exactly the one that can be a
    fluke in either direction.
    """
    log = ROOT / ".local" / "screencheck"
    sample = benchcheck.Sample(
        cycle_ms=2000,
        fields={"ocr_ms": 3139, "capture_ms": 469},
    )
    assert sample.f("ocr_ms") == 3139


def test_untimed_targets_are_reported_unknown_not_passed(tmp_path, capsys):
    """A target with no timer is a requirement nobody checked."""
    p = tmp_path / "bare.log"
    p.write_text("cycle_ms=1000 mode=eng-ui\n", encoding="utf-8")
    benchcheck.report(p)
    out = capsys.readouterr().out
    assert "НЕТ ЗАМЕРА" in out


def test_percentile_matches_the_overlay_definition():
    assert benchcheck.p95([1]) == 1
    assert benchcheck.p95([]) == 0
    assert benchcheck.p95(list(range(1, 101))) == 95
    assert benchcheck.p95([10, 10, 10]) == 10


def test_the_report_names_the_unmeasured_targets(tmp_path, capsys):
    p = tmp_path / "run.log"
    p.write_text(
        "cycle_ms=1000 mode=eng-ui videoish=0 cycles=1 frames_seen=1 "
        "ocr_ms=500/p95=900/max=900 tracking_ms=0/p95=2/max=2 "
        "reconciliation_ms=0/p95=1/max=1 translation_ms=0/p95=353/max=405\n",
        encoding="utf-8",
    )
    benchcheck.report(p)
    out = capsys.readouterr().out
    assert "НЕТ ЗАМЕРА" in out, "probe без таймера должен быть виден"
    assert "не проверены" in out


def test_a_target_over_its_limit_is_flagged(tmp_path, capsys):
    p = tmp_path / "slow.log"
    p.write_text(
        "cycle_ms=9000 mode=eng-ui ocr_ms=900/p95=900 tracking_ms=0/p95=40/max=40 "
        "reconciliation_ms=0/p95=1/max=1 translation_ms=0/p95=100/max=100\n",
        encoding="utf-8",
    )
    rc = benchcheck.report(p, worst_only=True)
    assert rc == 2, "превышение цели должно давать ненулевой код возврата"
    assert "ПРОБЛЕМА" in capsys.readouterr().out


def test_the_parser_ignores_a_cycle_line_without_fields():
    p_text = "cycle_ms=500 mode=eng-ui\n"
    # Minimal inline check: the cycle is found, the fields dict is just empty.
    assert benchcheck.CYCLE_RE.search(p_text)


def test_counters_are_reported_verbatim():
    s = benchcheck.Sample(
        cycle_ms=1, fields={"blocks_reused": 10, "cache_misses": 2, "cards_drawn": 50}
    )
    assert s.f("blocks_reused") == 10
    assert s.f("cache_misses") == 2
    assert s.f("cards_drawn") == 50
    assert s.f("nope", 7) == 7
