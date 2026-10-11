"""Incremental ``top`` aggregation — pure data in, pure stats out.

``own`` counts samples where a function was the leaf frame; ``total``
counts samples where it appeared anywhere on a stack. Rendering lives in
present/ (batch 6, ADR A5 / TS5) — this module is state only.

契约（contracts.md 批 4 表 TS1–TS5，含 A7 P0-1 修复 TS2）。当前为骨架：
状态字段已锁定，update 为哨兵，批次 4 实现填实。
"""

from .. import dto


class TopStats:
    """Incremental aggregation of sampled threads (TS1–TS4)."""

    def __init__(self):
        self.own: dict[str, int] = {}
        self.total: dict[str, int] = {}
        self.samples = 0
        self.idle_samples = 0
        # tid → (top FrameInfo | None, thread name) of the most recent
        # active sample
        self.current: dict[int, tuple[dto.FrameInfo | None, str]] = {}
        self.idle_threads: list[int] = []

    def update(self, threads: list[dto.ThreadInfo]) -> None:
        """Fold one round of sampled threads into the aggregates (TS1–TS4).

        A7 P0-1 修复（TS2）：idle 守卫查 idle_threads 去重（旧误查
        current，同 tid 每周期重复 append 无限膨胀）；idle 线程同时从
        current 移除（曾活跃转 idle 不带陈旧帧留 Active 表）。
        """
        for t in threads:
            if t.idle:
                self.idle_samples += 1
                # TS2 (A7 P0-1) — dedupe against idle_threads, not current
                # (idle threads never enter current, so the old guard let
                # the same tid be appended every round).
                if t.native_tid not in self.idle_threads:
                    self.idle_threads.append(t.native_tid)
                # TS2 (A7 P0-1) — a formerly active thread going idle must
                # not keep a stale frame in the Active table.
                self.current.pop(t.native_tid, None)
                continue
            # TS3 — back to active: leave the idle list, count the sample.
            self.idle_threads = [tid for tid in self.idle_threads
                                 if tid != t.native_tid]
            self.samples += 1
            if t.frames:
                top = t.frames[0]
                # TS1 — own counts the leaf frame; None normalizes to "?".
                name = top.name if top.name is not None else "?"
                self.own[name] = self.own.get(name, 0) + 1
                self.current[t.native_tid] = (top, t.name)
            else:
                # TS4 — empty stack is a legitimate active state (in C code).
                self.current[t.native_tid] = (None, t.name)
            # TS1 — total counts a name once per stack (dedup recursion).
            seen = set()
            for f in t.frames:
                name = f.name if f.name is not None else "?"
                if name not in seen:
                    seen.add(name)
                    self.total[name] = self.total.get(name, 0) + 1
