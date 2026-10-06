"""What one cycle cost, and what the session has cost so far.

None of them existed: the overlay logged `cycle_ms` and `ocr_ms` as text, which
is a number in a log line and not a measurement - you cannot take a p95 of it,
and the pieces that decide where the cycle goes were not timed at all, so "the
cycle is slow" had nowhere to point.

Two levels, because they answer different questions: a *cycle* is one pass of the
worker loop and its timings are milliseconds of wall clock for that pass, while a
*session* is every cycle since the overlay started and its counters are totals
plus the distribution of the timings.

The distribution is kept as a bounded reservoir of samples rather than a running
mean, because the target is p95 and the mean of a distribution with a heavy tail
is the one number that cannot tell you whether the tail exists. The reservoir is
bounded because an overlay session runs for hours and an unbounded list of
integers is a slow leak in exactly the process that is supposed to be cheap.

Nothing here logs. The caller decides when to log and what to log: `summary()`
returns text, and the live path logs it on its own cadence so a stats line can
never be the reason a cycle got slower.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

RESERVOIR_MAX = 512
"""Bounded sample count per timing.

A long session would otherwise accumulate one integer per timing per cycle
forever. 512 samples keeps p95 meaningful - the tail is exactly what a p95 is
for - while costing a few kilobytes.
"""

MS = 1000.0

_BATCH_HITS = 0
_BATCH_MISSES = 0
"""Lines answered from cache, and lines a backend had to produce.

Counted in `translation/batch.py`, which is the only place that sees the miss,
and drained by the worker with `SessionStats.translation.take_batch()`.
Module-level rather than threaded through `translate_many`'s signature because
that function is called from the incremental path too and a parameter would
have to be optional to keep both callers working - an optional counter is a
counter nobody sets.
"""


class BatchStats:
    """The process-wide counter the batch path adds to."""

    def note(self, total: int, missing: int) -> None:
        global _BATCH_HITS, _BATCH_MISSES
        _BATCH_HITS += max(0, int(total) - max(0, int(missing)))
        _BATCH_MISSES += max(0, int(missing))


BATCH_STATS = BatchStats()


@dataclass
class Timing:
    """One timed span: how long it took, and how often it has been taken.

    `last_ms` is what a single cycle reports, `samples` is what a session
    reports. Keeping both means the log line for one cycle stays a single
    number and does not have to compute an average nobody asked for.
    """

    name: str
    last_ms: float = 0.0
    total_ms: float = 0.0
    count: int = 0
    max_ms: float = 0.0
    samples: list[float] = field(default_factory=list)

    def add(self, ms: float) -> None:
        v = max(0.0, float(ms))
        self.last_ms = v
        self.total_ms += v
        self.count += 1
        if v > self.max_ms:
            self.max_ms = v
        self.samples.append(v)
        if len(self.samples) > RESERVOIR_MAX:
            # Drop the oldest half rather than one sample: a FIFO of 1 would
            # decimate the distribution down to its last few cycles and p95
            # would report the recent past as if it were the whole session.
            del self.samples[: RESERVOIR_MAX // 2]

    def percentile(self, q: float) -> float:
        """The q-th percentile of the samples, 0 if the span never ran."""
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        if len(ordered) == 1:
            return ordered[0]
        pos = (len(ordered) - 1) * max(0.0, min(1.0, q))
        low = int(pos)
        high = min(low + 1, len(ordered) - 1)
        frac = pos - low
        return ordered[low] * (1.0 - frac) + ordered[high] * frac

    def text(self) -> str:
        if not self.count:
            return ""
        return (
            f"{self.name}_ms={self.last_ms:.0f}"
            f"/p95={self.percentile(0.95):.0f}"
            f"/max={self.max_ms:.0f}"
        )


@dataclass
class CaptureStats:
    """Grabbing the frame: the compositor call and the pixels it handed back."""

    frames_seen: int = 0
    """Frames the capture path actually produced."""

    frames_skipped: int = 0
    """Frames the loop decided not to read at all."""

    dirty_regions: int = 0
    """Regions found changed since the last frame, summed."""

    capture: Timing = field(default_factory=lambda: Timing("capture"))
    probe: Timing = field(default_factory=lambda: Timing("probe"))
    diff: Timing = field(default_factory=lambda: Timing("diff"))

    def note_skip(self) -> None:
        self.frames_skipped += 1

    def note_frame(self, *, dirty: int = 0) -> None:
        self.frames_seen += 1
        self.dirty_regions += max(0, int(dirty))


@dataclass
class OcrStats:
    """Reading pixels into lines: the full pass, and the partial ones."""

    full_ocr_count: int = 0
    partial_ocr_count: int = 0
    lines_found: int = 0
    ocr: Timing = field(default_factory=lambda: Timing("ocr"))
    reocr: Timing = field(default_factory=lambda: Timing("reocr"))

    def note_full(self, ms: float, lines: int = 0) -> None:
        self.full_ocr_count += 1
        self.ocr.add(ms)
        self.lines_found += max(0, int(lines))

    def note_partial(self, ms: float, lines: int = 0) -> None:
        self.partial_ocr_count += 1
        self.reocr.add(ms)
        self.lines_found += max(0, int(lines))


@dataclass
class TrackingStats:
    """Deciding which block is which block from one frame to the next."""

    blocks_reused: int = 0
    blocks_changed: int = 0
    blocks_removed: int = 0
    blocks_added: int = 0
    tracking: Timing = field(default_factory=lambda: Timing("tracking"))
    reconciliation: Timing = field(default_factory=lambda: Timing("reconciliation"))
    layout: Timing = field(default_factory=lambda: Timing("layout"))

    def note_outcome(
        self, *, reused: int = 0, changed: int = 0, removed: int = 0, added: int = 0
    ) -> None:
        self.blocks_reused += max(0, int(reused))
        self.blocks_changed += max(0, int(changed))
        self.blocks_removed += max(0, int(removed))
        self.blocks_added += max(0, int(added))


@dataclass
class TranslationStats:
    """Turning lines into Russian: the queue, the backends, the cache."""

    translation_cache_hits: int = 0
    translation_cache_misses: int = 0
    local_translations: int = 0
    online_translations: int = 0
    network_requests: int = 0
    translation_queue: Timing = field(default_factory=lambda: Timing("translation_queue"))
    translation: Timing = field(default_factory=lambda: Timing("translation"))

    def note_cache(self, hits: int = 0, misses: int = 0) -> None:
        self.translation_cache_hits += max(0, int(hits))
        self.translation_cache_misses += max(0, int(misses))

    def take_batch(self) -> tuple[int, int]:
        """Drain what the batch translation path counted, and return it.

        `translation/batch.py` is the only place that knows whether a line was
        answered from the cache or by a backend, because it is the only place
        that saw the cache miss. The counter lives here and is drained by the
        worker after `translate_many` returns; without that, the worker would be
        counting hits by asking the cache again, which is a second lookup per
        line for a number it already had.
        """
        global _BATCH_HITS, _BATCH_MISSES
        hits, misses = _BATCH_HITS, _BATCH_MISSES
        _BATCH_HITS = 0
        _BATCH_MISSES = 0
        return hits, misses

    def note_backend(self, backend: str, *, network: bool = False) -> None:
        b = (backend or "").casefold()
        if b in ("ctranslate2", "local", "argu", "argostranslate", "offline"):
            self.local_translations += 1
        else:
            self.online_translations += 1
        if network:
            self.network_requests += 1


@dataclass
class RenderStats:
    """Turning blocks into cards, and putting them on the screen."""

    cards_built: int = 0
    cards_drawn: int = 0
    render_prepare: Timing = field(default_factory=lambda: Timing("render_prepare"))


@dataclass
class SessionStats:
    """Everything above, for one live session.

    The plan's targets are percentiles over a session, so this is where a p95
    lives; `cycle` is the whole pass and is the number the live path already
    logged as `cycle_ms`.
    """

    capture: CaptureStats = field(default_factory=CaptureStats)
    ocr: OcrStats = field(default_factory=OcrStats)
    tracking: TrackingStats = field(default_factory=TrackingStats)
    translation: TranslationStats = field(default_factory=TranslationStats)
    render: RenderStats = field(default_factory=RenderStats)
    cycle: Timing = field(default_factory=lambda: Timing("cycle"))
    cycles: int = 0

    def note_cycle(self, ms: float) -> None:
        self.cycles += 1
        self.cycle.add(ms)

    def timings(self) -> Iterable[Timing]:
        """Every timing except `cycle`.

        The cycle is logged by the caller as `cycle_ms=` before this summary is
        appended, and the harness reads that field off the same line. Listing it
        here as well would put a second `cycle_ms=` on one line for the same
        number, which is exactly the collision this module exists to avoid.
        """
        return (
            self.capture.capture,
            self.capture.probe,
            self.capture.diff,
            self.ocr.ocr,
            self.ocr.reocr,
            self.tracking.tracking,
            self.tracking.reconciliation,
            self.tracking.layout,
            self.translation.translation_queue,
            self.translation.translation,
            self.render.render_prepare,
        )

    def counters(self) -> str:
        c = self.capture
        o = self.ocr
        t = self.tracking
        tr = self.translation
        return (
            f"cycles={self.cycles}"
            f" frames_seen={c.frames_seen} frames_skipped={c.frames_skipped}"
            f" dirty_regions={c.dirty_regions}"
            f" full_ocr={o.full_ocr_count} partial_ocr={o.partial_ocr_count}"
            f" blocks_reused={t.blocks_reused} blocks_changed={t.blocks_changed}"
            f" blocks_removed={t.blocks_removed} blocks_added={t.blocks_added}"
            f" cache_hits={tr.translation_cache_hits}"
            f" cache_misses={tr.translation_cache_misses}"
            f" local={tr.local_translations} online={tr.online_translations}"
            f" network={tr.network_requests}"
            f" cards_built={self.render.cards_built}"
            f" cards_drawn={self.render.cards_drawn}"
        )

    def summary(self) -> str:
        """One line: the counters, then every timing that has a sample.

        Keys use underscores on purpose. The live log is free text parsed by
        `scripts/screencheck.py` with loose regexes for `cycle_ms`, `shown=` and
        `mode=`, and a key spelled `cycle_ms` inside this line would overwrite
        what that script reads as the cycle it just measured.
        """
        parts = [self.counters()]
        parts.extend(t.text() for t in self.timings() if t.count)
        return " ".join(p for p in parts if p)

    def reset(self) -> None:
        self.capture = CaptureStats()
        self.ocr = OcrStats()
        self.tracking = TrackingStats()
        self.translation = TranslationStats()
        self.render = RenderStats()
        self.cycle = Timing("cycle")
        self.cycles = 0
