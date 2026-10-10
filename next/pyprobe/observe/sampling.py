"""Sampling engine: one-time session resolution + periodic cheap samples.

``__init__`` opens the session (all process-lifetime-invariant work);
``sample()`` is the hot path: a fresh view per call (ADR A4 — reusing one
across samples would serve stale memory), the thread chain, idle pruning
via /proc stat state, and the frame walk for active threads only.

契约（contracts.md 批 4 表 SA1–SA6）。当前为骨架：方法返回哨兵，
批次 4 实现填实。
"""

from ..cpython.names import get_thread_names
from ..cpython.runtime import read_thread_chain
from ..errors import NoThreadState, ProcessExited
from .session import open_session
from .snapshot import build_thread, is_thread_idle_by_stat


class Sampler:
    """Periodic Python-stack sampler for a running CPython process."""

    def __init__(self, pid: int, *, view_factory=None):
        self.pid = pid
        self.session = open_session(pid, view_factory=view_factory)

    def sample(self):
        """Take one snapshot of all threads (SA1–SA4, SA6).

        Raises ``ProcessExited`` when the target's thread chain becomes
        unreadable (SA2).
        """
        # SA1 (A4) — fresh view per sample; reuse would serve stale memory.
        view = self.session.view_factory(self.pid)
        try:
            trefs = read_thread_chain(view, self.session.layout,
                                      self.session.interp_addr)
        except NoThreadState as e:
            # SA2 — an unreadable chain means the target is gone; the cause
            # chain keeps the parse-failure detail for diagnostics.
            raise ProcessExited(self.pid) from e

        threads = []
        for tref in trefs:
            # SA3 — /proc stat check first: stat-idle threads are pruned
            # with idle_hint, skipping the frame walk on the hot path.
            idle = is_thread_idle_by_stat(self.pid, tref.native_tid)
            threads.append(build_thread(self.session, view, tref,
                                        idle_hint=idle))
        threads.sort(key=lambda t: t.native_tid)  # SA4 — 复刻约束 线程升序
        return threads

    def refresh_names(self) -> None:
        """Re-read the thread-name map from the target (SA5).

        Cheap enough for display cadence (``top``), too expensive for the
        sampling hot loop. ``record`` never calls it: names are part of
        the folded aggregation key and must stay stable for a recording.
        """
        view = self.session.view_factory(self.pid)
        self.session.names = get_thread_names(view, self.session.layout,
                                              self.session.interp_addr)
