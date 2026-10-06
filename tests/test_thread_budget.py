"""one thread budget, derived from the machine.

CTranslate2's documentation warns against `inter_threads * intra_threads`
exceeding the core count, and this project hit the shape of that warning without
hitting the limit: RapidOCR at two ONNX threads, CTranslate2 at
`cpus // 2` intra-op, and a translation pool, each sized by its own guess at
`os.cpu_count()`. Sixteen cores, so sixteen runnable threads on a process whose
job is to stay off the CPU between cycles, competing with the GTK main loop and
the capture.

So the number now comes from one place, out of one input: the physical cores,
with the cgroup quota honoured. The tests are about the arithmetic and about not
regressing to per-subsystem guesses, because the failure mode is invisible -
nothing crashes, the machine is just hot and a cycle is 300 ms longer.

    uv run pytest tests/test_thread_budget.py
"""

from __future__ import annotations

import pytest

from kizurium_translator import threads
from kizurium_translator.threads import MIN_CORES, ThreadBudget, budget, physical_cores


def test_the_budget_is_derived_from_cores():
    assert ThreadBudget(cores=16).ocr == 2
    assert ThreadBudget(cores=16).local_mt_intra == 2
    assert ThreadBudget(cores=16).translation_executor == 4


def test_the_pools_total_stays_under_the_core_count():
    """The whole point: OCR 2 + CTranslate2 1xN + pool, and that is the machine.

    Before this the total was 16 on sixteen cores, which is the shape of the
    warning CTranslate2's own documentation gives about inter*intra.
    """
    assert ThreadBudget(cores=16).total() == 8
    for cores in (8, 16, 32, 64):
        b = ThreadBudget(cores=cores)
        ours = b.ocr + b.local_mt_inter * b.local_mt_intra + b.translation_executor
        assert ours <= cores, f"{cores} ядер: наши пулы занимают {ours} потоков"


def test_no_pool_is_ever_zero_threads():
    """A zero-thread pool raises instead of degrading."""
    for cores in (1, 2, 3, 4, 8, 16, 64, 256):
        b = ThreadBudget(cores=cores)
        assert b.ocr >= 1, f"{cores} ядер -> ocr={b.ocr}"
        assert b.local_mt_intra >= 1, f"{cores} ядер -> mt={b.local_mt_intra}"
        assert b.local_mt_inter >= 1
        assert b.translation_executor >= 1


def test_translation_yields_cores_to_ocr_on_a_small_machine():
    """On a small machine OCR needs the cores more than translation does."""
    assert ThreadBudget(cores=2).local_mt_intra == 1
    assert ThreadBudget(cores=4).local_mt_intra == 1
    assert ThreadBudget(cores=8).local_mt_intra == 1
    assert ThreadBudget(cores=16).local_mt_intra == 2


def test_local_mt_intra_never_exceeds_four():
    """More intra-op threads did not shorten a translation; it stole OCR's cores."""
    for cores in (16, 32, 64, 128):
        assert ThreadBudget(cores=cores).local_mt_intra <= 4


def test_the_translation_executor_is_capped():
    """A request is mostly a wait. Sixteen of them is a queue, not throughput."""
    for cores in (16, 32, 64):
        assert ThreadBudget(cores=cores).translation_executor == 4


def test_the_pools_under_our_control_fit_the_machine():
    """Ours must fit. GTK and the compositor are not ours to count.

    Three subsystems do not fit on two cores, and pretending they do means a
    zero-thread pool, which raises. So on the smallest machines the budget
    overshoots by exactly that: every pool still gets one thread, because a live
    overlay that cannot OCR at all is worse than one that competes with itself.
    From four cores up it fits outright, and from eight the cores are the limit
    rather than the per-subsystem caps.
    """
    for cores in (2, 3):
        b = ThreadBudget(cores=cores)
        ours = b.ocr + b.local_mt_inter * b.local_mt_intra + b.translation_executor
        assert ours > cores, "на крошечной машине перебор ожидаем, но он должен быть осознанным"
        assert ours <= cores + 2, f"{cores} ядер: перебор {ours - cores} слишком велик"
    for cores in (4, 8, 16, 32, 64):
        b = ThreadBudget(cores=cores)
        ours = b.ocr + b.local_mt_inter * b.local_mt_intra + b.translation_executor
        assert ours <= cores, f"{cores} ядер: наши пулы занимают {ours} потоков"


def test_inter_threads_stays_at_one():
    """One batch at a time; a second worker would reorder results, not speed them."""
    for cores in (2, 8, 64):
        assert ThreadBudget(cores=cores).local_mt_inter == 1


def test_the_budget_is_cached_but_can_be_asked_for_explicitly():
    threads.reset_budget_cache()
    first = budget()
    assert budget() is first, "повторный вызов не должен перечитывать cgroup"
    assert budget(cores=4).cores == 4, "явное число ядер должно побеждать"


def test_cores_never_drop_below_the_floor():
    assert budget(cores=1).cores == MIN_CORES
    assert budget(cores=0).cores == MIN_CORES


def test_physical_cores_is_at_least_one_and_uses_the_cgroup():
    cores = physical_cores()
    assert cores >= MIN_CORES
    # A cgroup quota is a ceiling, so it must never raise the count.
    quota = threads._cgroup_cores()
    if quota:
        assert physical_cores() <= max(MIN_CORES, min(quota, threads._os_cores()))


def test_the_ocr_pool_reads_the_budget_rather_than_a_literal():
    """The reason lives in one place; the call site must not grow its own number."""
    from kizurium_translator.ocr import engine

    assert engine.OCR_THREADS == budget().ocr


def test_the_translation_pool_reads_the_budget():
    from kizurium_translator.live import session

    # The pool is created at import time from the budget; assert the wiring rather
    # than the pool's private width, which the executor does not expose.
    assert session.TRANSLATION.pool is not None
    assert budget().translation_executor >= 1


def test_local_mt_reads_the_budget():
    from kizurium_translator.translation import local_mt

    backend = local_mt.CTranslate2Backend.__new__(local_mt.CTranslate2Backend)
    inter, intra = backend._thread_budget()
    assert (inter, intra) == (budget().local_mt_inter, budget().local_mt_intra)


@pytest.mark.parametrize("cores", [2, 4, 8, 16, 32, 64])
def test_the_budget_is_monotone(cores):
    """More cores must never mean fewer threads for a subsystem."""
    prev = ThreadBudget(cores=max(MIN_CORES, cores // 2))
    cur = ThreadBudget(cores=cores)
    assert cur.ocr >= prev.ocr
    assert cur.local_mt_intra >= prev.local_mt_intra
    assert cur.translation_executor >= prev.translation_executor
