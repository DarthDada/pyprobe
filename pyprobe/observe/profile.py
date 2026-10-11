"""Folded-stack profiling: periodic sampling + aggregation.

Rendering (``format_folded``) and the CLI wrapper live in present/ (batch
6, ADR A5) — this module is pure aggregation.

契约（contracts.md 批 4 表 PF1–PF5）。当前为骨架：函数返回哨兵，
批次 4 实现填实。
"""

import time

from .. import dto
from ..errors import ProcessExited
from .sampling import Sampler


def fold_key(thread: dto.ThreadInfo) -> str:
    """Fold one thread observation into a folded-stack key (PF1).

    Layout: ``<thread-prefix>;<root>;...;<leaf>``. The prefix is the
    quoted thread name (or ``tid-<n>`` when unnamed); frame names are bare
    (``None`` → ``?``), matching the folded format consumed by
    flamegraph.pl / inferno-flamegraph (复刻约束).
    """
    if thread.name:
        prefix = f'"{thread.name}"'
    else:
        prefix = f"tid-{thread.native_tid}"
    names = [f.name if f.name is not None else "?" for f in thread.frames]
    # ThreadInfo.frames is leaf→root; folded format wants root→leaf (PF1).
    return ";".join([prefix] + list(reversed(names)))


def collect_profile(pid: int, *, rate: float = 50,
                    duration: float | None = None,
                    sampler_factory=Sampler) -> dto.ProfileData:
    """Sample ``pid`` at ``rate`` Hz until ``duration`` seconds elapse
    (PF2–PF5).

    ``duration=None`` samples until interrupted (Ctrl-C) or the target
    exits — in both cases the partial profile is returned (PF4).
    Absolute-time scheduling: slow samples do not accumulate drift; when
    the loop falls behind by more than one interval the baseline resets
    instead of bursting catch-up samples (PF2).
    """
    sampler = sampler_factory(pid)
    interval = 1.0 / rate

    counts: dict[str, int] = {}
    samples = 0
    idle_samples = 0

    start = time.monotonic()
    next_t = start
    try:
        while duration is None or (time.monotonic() - start) < duration:
            for t in sampler.sample():
                if t.idle:
                    # PF3 — idle threads count separately, never enter keys.
                    idle_samples += 1
                    continue
                key = fold_key(t)
                counts[key] = counts.get(key, 0) + 1
                samples += 1

            # PF2 — absolute-time scheduling: no drift accumulation.
            next_t += interval
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # PF2 — fell behind by more than an interval: reset the
                # baseline instead of bursting catch-up samples.
                next_t = time.monotonic()
    except (KeyboardInterrupt, ProcessExited):
        pass  # PF4 — absorb; return the partial profile

    return dto.ProfileData(
        proc_info=sampler.proc_info,
        counts=counts,
        samples=samples,
        idle_samples=idle_samples,
        elapsed=time.monotonic() - start,
        version_warning=sampler.session.version_warning,
    )
