"""The machine's cores, and how much of them each subsystem may use.

No accidental oversubscription, and a total worker count derived
from the physical core count. CTranslate2's own documentation warns against
`inter_threads * intra_threads` exceeding the core count, and the reason is not
theoretical: on a 16-core laptop the overlay once ran RapidOCR at two ONNX
threads *and* CTranslate2 at `intra_threads = cpus // 2 = 8`, which is sixteen
runnable threads competing with the GTK main loop and the capture thread, on a
process whose whole job is to stay off the CPU between cycles.

Everything here derives from one number, so the budget can be recomputed for a
different machine without editing five call sites. Physical cores rather than
logical: hyperthread siblings share an execution unit, and a budget computed
from logical cores hands out twice the parallelism the chip can actually run.

Cgroups are respected because a container or a systemd unit can be limited to
part of the machine, and `os.cpu_count()` does not know about that.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MIN_CORES = 2
"""Below this the split below cannot be satisfied and everything gets one thread.

A two-core machine running a live overlay already has nothing to spare. Handing
each subsystem a share that rounds to zero would produce zero-thread pools, and
those raise rather than degrade.
"""


def physical_cores() -> int:
    """Cores this process may actually use, honouring a cgroup limit.

    `os.cpu_count()` reports the machine, not the quota, so a service with
    `CPUQuota=400%` on a 16-core box would size its pools for sixteen cores and
    be throttled mid-cycle - which shows up as latency spikes nobody can explain
    from inside the process.
    """
    limit = _cgroup_cores()
    if limit:
        return max(MIN_CORES, min(limit, _os_cores()))
    return max(MIN_CORES, _os_cores())


def _os_cores() -> int:
    return os.cpu_count() or MIN_CORES


def _cgroup_cores() -> int | None:
    """Cores allowed by cgroup v2, or None when there is no limit."""
    for path, per_cpu in (
        ("/sys/fs/cgroup/cpu.max", True),
        ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", False),
    ):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError:
            continue
        if not raw or raw == "max":
            continue
        try:
            if per_cpu:
                # "max 100000" or "400000 100000"
                quota, period = raw.split()
                if quota == "max":
                    continue
                allowed = int(quota) / int(period)
            else:
                quota = int(raw)
                with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", encoding="utf-8") as p:
                    allowed = quota / int(p.read().strip())
        except (OSError, ValueError, ZeroDivisionError):
            continue
        if allowed >= 1:
            return int(allowed)
    return None


@dataclass(frozen=True)
class ThreadBudget:
    """How many threads each part of the process may use.

    One number in, one plan out. The parts do not each guess: OCR, local MT and
    the translation executor adding their own numbers together is how a machine
    ends up with more workers than cores.
    """

    cores: int

    @property
    def ocr(self) -> int:
        """ONNX Runtime threads for the text detector.

        Two on any machine: the detector runs on a screen-sized crop for a few
        hundred milliseconds and has to leave the machine idle between cycles,
        and a wider pool raised idle CPU rather than lowering latency - ONNX
        Runtime keeps its intra-op pool spinning after the call returns.
        """
        return 2

    @property
    def local_mt_intra(self) -> int:
        """CTranslate2 intra-op threads.

        Capped at 4 and never more than an eighth of the machine: translation
        runs while OCR waits on the next frame, so a pool that saturates every
        core here directly lengthens the OCR it is competing with. An eighth
        rather than the half it used to read is the difference between sixteen
        runnable threads on a 16-core laptop and ten.
        """
        return max(1, min(4, self.cores // 8))

    @property
    def local_mt_inter(self) -> int:
        """CTranslate2 inter-op threads: one.

        One batch at a time is the whole point of the scheduler, so a second
        worker batch would reorder results rather than finish them sooner.
        """
        return 1

    @property
    def translation_executor(self) -> int:
        """Threads for translation requests.

        A quarter of the machine, capped at four: a request is a network call or
        a short local batch, so the pool is mostly waiting - it is also the pool
        that can hold the machine while the overlay tries to capture a frame. A
        quarter rather than a half is what keeps the pools fitting on a four-core
        machine, where OCR already has two of them.
        """
        return max(1, min(4, self.cores // 4))

    def total(self) -> int:
        """Every thread this process will run at once.

        Reported by `--doctor` and asserted against `cores` in the tests. It is
        allowed to exceed the core count - the GTK main loop and the compositor's
        own threads are not in this budget, and pretending otherwise would make
        the number wrong rather than make the machine faster - but the pools
        under our control must not exceed it on their own.
        """
        return self.ocr + self.local_mt_inter * self.local_mt_intra + self.translation_executor


_BUDGET: ThreadBudget | None = None


def budget(cores: int | None = None) -> ThreadBudget:
    """The process-wide budget, computed once.

    Cached because `physical_cores` reads cgroup files, and this is asked for at
    every OCR init and every model load.
    """
    global _BUDGET
    if cores is not None:
        return ThreadBudget(cores=max(MIN_CORES, int(cores)))
    if _BUDGET is None:
        _BUDGET = ThreadBudget(cores=physical_cores())
    return _BUDGET


def reset_budget_cache() -> None:
    _BUDGET = None
