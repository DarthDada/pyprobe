"""Contract tests for observe/profile.py (PF1–PF5).

The sampling loop is driven by a fake clock (module-level ``time`` seam)
and a stub sampler factory, so scheduling semantics (PF2) are pinned
deterministically without real sleeps.
"""

import pytest

import pyprobe.observe.profile as prof_mod
from pyprobe import dto
from pyprobe.errors import ProcessExited
from pyprobe.observe.profile import collect_profile, fold_key

PID = 4242


def _thread(name="", native_tid=101, frames=(), idle=False):
    return dto.ThreadInfo(native_tid=native_tid, name=name, idle=idle,
                          frames=list(frames))


def _frame(name):
    return dto.FrameInfo(name=name, filename="app/x.py", line=1)


class TestFoldKey:
    def test_root_first(self):
        """PF1：帧从 leaf→root 反转为 root→leaf（flamegraph 折叠格式的
        栈底在前约定，复刻约束）。"""
        t = _thread(frames=[_frame("leaf"), _frame("mid"), _frame("root")])
        assert fold_key(t) == 'tid-101;root;mid;leaf'

    def test_thread_name_prefix(self):
        """PF1：有名线程前缀为带引号的名字（聚合键的人读性）。"""
        t = _thread(name="worker", frames=[_frame("f")])
        assert fold_key(t) == '"worker";f'

    def test_tid_prefix_when_unnamed(self):
        """PF1：无名线程前缀 tid-<native_tid>（键仍稳定唯一）。"""
        t = _thread(native_tid=4321, frames=[_frame("f")])
        assert fold_key(t).startswith("tid-4321;")

    def test_none_frame_name_placeholder(self):
        """PF1：None 帧名 → "?"（C 帧/陈旧帧占位，键不因 None 崩坏）。"""
        t = _thread(frames=[_frame(None), _frame("root")])
        assert fold_key(t) == "tid-101;root;?"


class FakeClock:
    """monotonic/sleep 假时钟：sleep 推进时间（PF2 调度的确定性守护）。"""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, d):
        self.t += d


class StubSampler:
    """Scripted sampler: sample() yields queued rounds or raises."""

    def __init__(self, pid, rounds, clock=None):
        self.pid = pid
        self.rounds = list(rounds)
        self.proc_info = dto.ProcessInfo(pid=pid, cmdline="python3 x",
                                         exe_path="/x/python")
        self.session = type("S", (), {"version_warning": "[!] stub warn"})()
        self._clock = clock

    def sample(self):
        r = self.rounds.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def _run(monkeypatch, rounds, *, rate=10, duration=0.35, clock=None):
    clock = clock or FakeClock()
    monkeypatch.setattr(prof_mod, "time", clock)

    def factory(pid):
        return StubSampler(pid, rounds, clock)

    return collect_profile(PID, rate=rate, duration=duration,
                           sampler_factory=factory), clock


class TestCollectProfile:
    def test_counts_and_idle_exclusion(self, monkeypatch):
        """PF3：active 入 counts+samples；idle 只计 idle_samples 不入键。
        duration=0.25 使调度恰停 3 轮（t=0/0.1/0.2），与 rounds 数一致。"""
        active = _thread(name="w", frames=[_frame("f")])
        idle = _thread(native_tid=102, idle=True)
        rounds = [[active, idle]] * 3
        profile, _ = _run(monkeypatch, rounds, duration=0.25)
        assert profile.samples == 3
        assert profile.idle_samples == 3
        assert profile.counts == {'"w";f': 3}

    def test_absolute_scheduling_no_drift(self, monkeypatch):
        """PF2：绝对时间调度——sample 零耗时下 rate=10 恰好 0.1s 一格，
        duration 0.35 采 4 轮（0/0.1/0.2/0.3s），不漂移不少采。"""
        rounds = [[_thread(frames=[_frame("f")])]] * 10
        profile, clock = _run(monkeypatch, rounds)
        assert profile.samples == 4
        assert clock.t == pytest.approx(0.4)

    def test_behind_schedule_resets_baseline(self, monkeypatch):
        """PF2：单轮采样耗时超 interval → 重置基线而非补发爆发（旧
        record 的防抖动语义：落后不追，采样率不失真）。"""
        clock = FakeClock()

        class SlowSampler(StubSampler):
            def sample(self):
                clock.t += 0.25  # 单轮耗时 0.25s > interval 0.1s
                return super().sample()

        monkeypatch.setattr(prof_mod, "time", clock)
        rounds = [[_thread(frames=[_frame("f")])]] * 10

        def factory(pid):
            return SlowSampler(pid, rounds, clock)

        profile = collect_profile(PID, rate=10, duration=0.3,
                                  sampler_factory=factory)
        # t: 0 → sample(0.25) → 重置 → 0.25 → sample(0.5) → 0.5 ≥ 0.3 停
        assert profile.samples == 2

    def test_keyboardinterrupt_returns_partial(self, monkeypatch):
        """PF4：Ctrl-C 吸收 → 已采部分保留返回（record 的中断语义）。"""
        rounds = [[_thread(frames=[_frame("f")])],
                  [_thread(frames=[_frame("f")])],
                  KeyboardInterrupt()]
        profile, _ = _run(monkeypatch, rounds, duration=None)
        assert profile.samples == 2
        assert profile.counts == {"tid-101;f": 2}

    def test_process_exited_returns_partial(self, monkeypatch):
        """PF4：目标中途退出吸收 → 部分数据 + warning 透传（会话警告
        不随退出丢失，dump 层要显示）。"""
        rounds = [[_thread(frames=[_frame("f")])], ProcessExited(PID)]
        profile, _ = _run(monkeypatch, rounds, duration=None)
        assert profile.samples == 1
        assert profile.version_warning == "[!] stub warn"

    def test_duration_none_until_interrupt(self, monkeypatch):
        """PF5：duration=None 时只有中断/退出能终止循环（守护不会
        意外提前退出）。"""
        rounds = [[_thread(frames=[_frame("f")])]] * 5 + [KeyboardInterrupt()]
        profile, _ = _run(monkeypatch, rounds, duration=None)
        assert profile.samples == 5

    def test_elapsed_and_proc_info(self, monkeypatch):
        """PF4 配套：ProfileData 元数据齐备（elapsed 由时钟度量、
        proc_info 来自 sampler，复刻聚合输出形状）。duration=0.05
        恰停 1 轮，与 rounds 数一致。"""
        rounds = [[_thread(frames=[_frame("f")])]]
        profile, _ = _run(monkeypatch, rounds, duration=0.05)
        assert profile.proc_info.pid == PID
        assert profile.elapsed >= 0.0
