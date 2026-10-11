"""Contract tests for observe/topstats.py (TS1–TS4, A7 P0-1 修复 TS2).

TS2 is the acceptance-pinned behavior fix: the old guard consulted
``current`` (which idle threads never enter), so the same tid was
appended every sampling round and active→idle threads kept a stale frame
in the Active table (old §9 P0-1, reproduced live in 2026-10).
"""

from pyprobe import dto
from pyprobe.observe.topstats import TopStats


def _thread(native_tid=101, name="", frames=(), idle=False):
    return dto.ThreadInfo(native_tid=native_tid, name=name, idle=idle,
                          frames=list(frames))


def _frame(name, filename="app/x.py", line=1):
    return dto.FrameInfo(name=name, filename=filename, line=line)


class TestUpdate:
    def test_own_and_total(self):
        """TS1：own 计叶子帧、total 计栈内出现（去重——同帧递归出现
        两次只计一次 total，复刻旧语义）。"""
        s = TopStats()
        s.update([_thread(frames=[_frame("leaf"), _frame("root"),
                                  _frame("leaf")])])
        assert s.own == {"leaf": 1}
        assert s.total == {"leaf": 1, "root": 1}
        assert s.samples == 1

    def test_none_name_normalized_to_question_mark(self):
        """TS1：None 帧名归一 "?"（update 侧归一是 render 侧 A7 P1-8
        修复的前提——两处必须共用同一键）。"""
        s = TopStats()
        s.update([_thread(frames=[_frame(None)])])
        assert s.own == {"?": 1}
        assert s.total == {"?": 1}

    def test_idle_excluded_from_counts(self):
        """TS3/TS4 边界：idle 线程只计 idle_samples，不进 own/total/samples。"""
        s = TopStats()
        s.update([_thread(idle=True)])
        assert s.samples == 0
        assert s.idle_samples == 1
        assert s.own == {}

    def test_current_frame_tracked(self):
        """TS3：active 线程 current 记录顶帧与线程名（Active 表数据源）。"""
        s = TopStats()
        s.update([_thread(name="worker", frames=[_frame("f")])])
        top, name = s.current[101]
        assert top.name == "f"
        assert name == "worker"

    def test_empty_stack_active_counts_sample(self):
        """TS4：空帧 active 线程计 samples 且 current 记 (None, name)
        （C 代码中的线程是合法活跃态，不是 idle）。"""
        s = TopStats()
        s.update([_thread(name="c-thread", frames=[])])
        assert s.samples == 1
        assert s.current[101] == (None, "c-thread")


class TestIdleTrackingA7:
    """TS2（A7 P0-1 修复的守护对——旧 test_top.py:100 只 update 一次
    恰好掩盖的缺陷，此处成对钉死）。"""

    def test_same_idle_thread_not_duplicated(self):
        """TS2：同一 idle 线程连续两个采样周期只记录一次（旧守卫误查
        current → 每周期重复 append，50Hz 下无限膨胀刷屏）。"""
        s = TopStats()
        s.update([_thread(native_tid=5, idle=True)])
        s.update([_thread(native_tid=5, idle=True)])
        s.update([_thread(native_tid=5, idle=True)])
        assert s.idle_threads == [5]
        assert s.idle_samples == 3  # 计数照算，列表不膨胀

    def test_active_to_idle_transition(self):
        """TS2：曾活跃线程转 idle——从 current 移除（不带陈旧帧留
        Active 表）并进入 idle_threads（一次）。"""
        s = TopStats()
        s.update([_thread(native_tid=5, frames=[_frame("f")])])
        assert 5 in s.current
        s.update([_thread(native_tid=5, idle=True)])
        assert 5 not in s.current
        assert s.idle_threads == [5]

    def test_idle_to_active_transition(self):
        """TS3：idle 线程转活跃——从 idle_threads 移除并回到 current。"""
        s = TopStats()
        s.update([_thread(native_tid=5, idle=True)])
        s.update([_thread(native_tid=5, frames=[_frame("f")])])
        assert s.idle_threads == []
        assert 5 in s.current
        assert s.samples == 1
