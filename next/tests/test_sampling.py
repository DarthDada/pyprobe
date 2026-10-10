"""Contract tests for observe/sampling.py (SA1–SA6).

open_session is monkeypatched with a stub session; thread graphs are
Layout-driven FakeMemory (3.13 layout, direct-current_frame branch).
"""

import struct

import pytest

import pyprobe.observe.sampling as samp_mod
from pyprobe import dto
from pyprobe.cpython.runtime import ThreadStateRef
from pyprobe.errors import NoThreadState, ProcessExited
from pyprobe.observe.sampling import Sampler
from pyprobe.target.layout import resolve_layout
from tests.fakemem import FakeMemory

NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"
INTERP = 0x200000
TSTATE_BASE = 0x300000
FRAME_BASE = 0x400000
PID = 4242
LAYOUT = resolve_layout("3.13", overrides_path=NO_OVERRIDE)


class StubSession:
    """Session double: only the fields Sampler actually consumes."""

    def __init__(self, mem, names=None):
        self.pid = PID
        self.interp_addr = INTERP
        self.trampoline_addr = 0
        self.layout = LAYOUT
        self.names = dict(names or {})
        self.view_factory = lambda pid: mem
        self.view_calls = 0
        base_factory = self.view_factory
        self.view_factory = lambda pid: (
            self._count(), base_factory(pid))[1]
        self.proc_info = dto.ProcessInfo(pid=PID, cmdline="python3 x",
                                         exe_path="/x/python")

    def _count(self):
        self.view_calls += 1


def _make_sampler(monkeypatch, mem, names=None):
    session = StubSession(mem, names)
    monkeypatch.setattr(samp_mod, "open_session", lambda pid, **kw: session)
    return Sampler(PID), session


def _add_tstate(mem, tref_addr, *, thread_id, native_tid, next_addr=0,
                frame_addr=0):
    end = max(LAYOUT.get("ThreadState.next"),
              LAYOUT.get("ThreadState.thread_id"),
              LAYOUT.get("ThreadState.native_thread_id"),
              LAYOUT.get("ThreadState.current_frame")) + 8
    blob = bytearray(end)
    struct.pack_into("<Q", blob, LAYOUT.get("ThreadState.next"), next_addr)
    struct.pack_into("<Q", blob, LAYOUT.get("ThreadState.thread_id"),
                     thread_id)
    struct.pack_into("<Q", blob, LAYOUT.get("ThreadState.native_thread_id"),
                     native_tid)
    struct.pack_into("<Q", blob, LAYOUT.get("ThreadState.current_frame"),
                     frame_addr)
    mem.add(tref_addr, bytes(blob))


def _link_head(mem, first):
    mem.add_ptr(INTERP + LAYOUT.get("InterpreterState.threads")
                + LAYOUT.get("pythreads.head"), first)


class TestSample:
    def test_returns_sorted_named_threads(self, monkeypatch):
        """SA4：native_tid 升序 + names 按 thread_id 贴名（链序打乱，
        输出必须升序）。"""
        mem = FakeMemory()
        _add_tstate(mem, TSTATE_BASE, thread_id=2, native_tid=102,
                    next_addr=TSTATE_BASE + 0x1000)
        _add_tstate(mem, TSTATE_BASE + 0x1000, thread_id=1, native_tid=101)
        _link_head(mem, TSTATE_BASE)
        sampler, _ = _make_sampler(monkeypatch, mem, {1: "main"})
        threads = sampler.sample()
        assert [t.native_tid for t in threads] == [101, 102]
        assert threads[0].name == "main"
        assert threads[1].name == ""

    def test_new_view_per_sample(self, monkeypatch):
        """SA1（A4）：每次 sample 经 view_factory 新建视图（跨样本复用
        会供陈旧数据——调用计数即守护）。"""
        mem = FakeMemory()
        _link_head(mem, 0)
        sampler, session = _make_sampler(monkeypatch, mem)
        sampler.sample()
        sampler.sample()
        assert session.view_calls == 2

    def test_no_thread_state_becomes_process_exited(self, monkeypatch):
        """SA2：线程链读失败 → ProcessExited 且链式保留 NoThreadState
        原因（目标退出与解析故障的诊断区分）。"""
        sampler, _ = _make_sampler(monkeypatch, FakeMemory())
        with pytest.raises(ProcessExited) as ei:
            sampler.sample()
        assert ei.value.pid == PID
        assert isinstance(ei.value.__cause__, NoThreadState)

    def test_empty_chain_returns_empty(self, monkeypatch):
        """SA6：head==0 空链 → []（进程无线程的退化态不报错）。"""
        mem = FakeMemory()
        _link_head(mem, 0)
        sampler, _ = _make_sampler(monkeypatch, mem)
        assert sampler.sample() == []

    def test_idle_hint_prunes_frame_walk(self, monkeypatch):
        """SA3：stat-idle 线程 idle_hint 剪枝——其 current_frame 指向垃圾
        地址，若走了帧遍历必然失败；idle=True 且空帧即剪枝证据。"""
        mem = FakeMemory()
        _add_tstate(mem, TSTATE_BASE, thread_id=2, native_tid=102,
                    frame_addr=0xDEAD00)
        _link_head(mem, TSTATE_BASE)
        monkeypatch.setattr(samp_mod, "is_thread_idle_by_stat",
                            lambda pid, tid: True)
        sampler, _ = _make_sampler(monkeypatch, mem)
        threads = sampler.sample()
        assert len(threads) == 1
        assert threads[0].idle is True
        assert threads[0].frames == []

    def test_sample_does_not_refresh_names(self, monkeypatch):
        """SA5：sample() 不重读 names（record 聚合键稳定性——名字变动会
        把同一线程折成两个键）。"""
        mem = FakeMemory()
        _add_tstate(mem, TSTATE_BASE, thread_id=2, native_tid=102)
        _link_head(mem, TSTATE_BASE)
        calls = []
        monkeypatch.setattr(samp_mod, "get_thread_names",
                            lambda v, lay, i: calls.append(1) or {})
        sampler, _ = _make_sampler(monkeypatch, mem)
        sampler.sample()
        assert calls == []

    def test_refresh_names_updates_session(self, monkeypatch):
        """SA5：refresh_names() 显式刷新并回写 session.names（top 的
        显示节奏通道）。"""
        mem = FakeMemory()
        _link_head(mem, 0)
        monkeypatch.setattr(samp_mod, "get_thread_names",
                            lambda v, lay, i: {2: "renamed"})
        sampler, session = _make_sampler(monkeypatch, mem, {2: "old"})
        sampler.refresh_names()
        assert session.names == {2: "renamed"}


class TestInit:
    def test_open_session_called_with_view_factory(self, monkeypatch):
        """SA 组合：Sampler 把 view_factory 透传给 open_session（A4 注入
        链不断裂）。"""
        seen = {}

        def fake_open(pid, *, view_factory=None):
            seen.update(pid=pid, vf=view_factory)
            return StubSession(FakeMemory())

        monkeypatch.setattr(samp_mod, "open_session", fake_open)

        def vf(pid):
            return FakeMemory()

        Sampler(PID, view_factory=vf)
        assert seen["pid"] == PID
        assert seen["vf"] is vf

    def test_session_exposed(self, monkeypatch):
        """SA 组合：session 公开可及（dump 层读 version_warning/proc_info
        的通道，复刻旧 sampler.session 语义）。"""
        sampler, session = _make_sampler(monkeypatch, FakeMemory())
        assert sampler.session is session


class TestThreadStateRefUsage:
    def test_tref_fields_drive_build(self, monkeypatch):
        """SA4 配套：ThreadStateRef 三字段原样进入 ThreadInfo
        （thread_id 是 names 贴名的键，错配即张冠李戴）。"""
        mem = FakeMemory()
        _add_tstate(mem, TSTATE_BASE, thread_id=42, native_tid=777)
        _link_head(mem, TSTATE_BASE)
        sampler, _ = _make_sampler(monkeypatch, mem, {42: "answer"})
        t = sampler.sample()[0]
        assert (t.thread_id, t.native_tid, t.name) == (42, 777, "answer")
        assert isinstance(t, dto.ThreadInfo)
        assert ThreadStateRef(TSTATE_BASE, 42, 777).tstate_addr == TSTATE_BASE
