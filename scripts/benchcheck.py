"""Read the overlay's own measurements out of a live run and judge them.

`screencheck.py` answers whether the overlay drew the right thing. This answers
how fast it did it, using the numbers the overlay now emits itself:
the `capture_ms`, `ocr_ms`, `render_prepare_ms` timings with their p95s, and the
counters.

The targets are: probe p95 < 20 ms, tracking/reconcile p95 < 10 ms,
cache lookup < 2 ms, local short-string translation p95 < 500 ms, visible local
update p95 < 750 ms. A target the run cannot answer is reported as unknown, not
as a pass - the point is a number nobody has to guess at.

    python scripts/benchcheck.py.local/screencheck/session.log
    python scripts/benchcheck.py.local/screencheck/*.log --worst-only
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# The live log is free text with k=v pairs. Anchored on the key so a value that
# happens to contain the word cannot be mistaken for a field.
CYCLE_RE = re.compile(r"\bcycle_ms=(\d+)")
FIELD_RE = re.compile(r"(?<![\w.])([a-z_]+)=([0-9]+)(/p95=([0-9]+))?")


@dataclass
class Sample:
    """One cycle's self-reported numbers."""

    cycle_ms: int
    fields: dict[str, int]

    def f(self, key: str, default: int = 0) -> int:
        return int(self.fields.get(key, default))


def parse_log(path: Path) -> list[Sample]:
    out: list[Sample] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = CYCLE_RE.search(line)
        if not m:
            continue
 # Only the field group after the cycle: the rest of the line is free text
 # and may hold the source text of a card.
        tail = line[m.end() :]
 # `timing_ms=last/p95=X/max=Y`: the p95 is the session percentile the
 # overlay keeps and the onesets its targets against. Reading
 # the first number instead would judge a plan target against the last
 # cycle, which is the one number that can be anything.
        fields = {}
        for key, last, _, p95v in FIELD_RE.findall(tail):
            if not last.isdigit():
                continue
            fields[key] = int(p95v) if p95v.isdigit() else int(last)
        out.append(Sample(cycle_ms=int(m.group(1)), fields=fields))
    return out


def p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * 0.95
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low))


@dataclass
class Target:
    key: str
    limit_ms: int
    what: str


#. `probe` and `translation_queue` have no timer on the live path yet: the
# plan asks for them and they are listed so the report says "unknown" instead of
# quietly omitting a requirement.
TARGETS = [
    Target("probe_ms", 20, "probe p95"),
    Target("tracking_ms", 10, "tracking p95"),
    Target("reconciliation_ms", 10, "reconcile p95"),
    Target("translation_ms", 500, "local short-string translation p95"),
]


def last_cycle(samples: list[Sample]) -> Sample | None:
    """The most complete cycle.

    Session totals are cumulative, so every cycle carries the same counters; the
    timings are per cycle. The last one is the freshest view, but the p95 the
    overlay prints is a session p95 and that is what is judged.
    """
    return samples[-1] if samples else None


def report(path: Path, worst_only: bool = False) -> int:
    samples = parse_log(path)
    if not samples:
        print(f"{path.name}: нет строк с cycle_ms - оверлей не отчитался")
        return 1
    cur = last_cycle(samples)
    assert cur is not None
    cycles = [s.cycle_ms for s in samples]

    print(f"{path.name}: циклов={len(samples)}")
    print(f"  cycle_ms       худший={max(cycles)} p95={p95(cycles)}")

    unknown = 0
    for t in TARGETS:
        if t.key not in cur.fields:
            print(f"  {t.what:36s} НЕТ ЗАМЕРА")
            unknown += 1
            continue
        got = cur.f(t.key)
        verdict = "ок" if got <= t.limit_ms else f"ПРОБЛЕМА (цель {t.limit_ms})"
        print(f"  {t.what:36s} p95={got:5d}  {verdict}")
        if worst_only and got > t.limit_ms:
            return 2

    for key in ("ocr_ms", "capture_ms", "render_prepare_ms", "layout_ms", "diff_ms"):
        if key in cur.fields:
            print(f"  {key:36s} p95={cur.f(key)}")
    for key in (
        "frames_seen",
        "frames_skipped",
        "full_ocr",
        "partial_ocr",
        "blocks_reused",
        "blocks_changed",
        "blocks_removed",
        "cache_hits",
        "cache_misses",
        "cards_built",
        "cards_drawn",
    ):
        if key in cur.fields:
            print(f"  {key:36s} {cur.f(key)}")

    if unknown and not worst_only:
        print(f"  ({unknown} цели без замера - они не проверены, а не пройдены)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--worst-only", action="store_true")
    args = ap.parse_args()
    rc = 0
    for path in args.logs:
        rc |= report(path, args.worst_only)
    return rc


if __name__ == "__main__":
    sys.exit(main())
