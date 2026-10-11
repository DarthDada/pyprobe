"""Contract tests for observe/snapshot.py (SN1–SN5).

Thread graphs are built Layout-driven (fakemem) on a 3.13 layout
(direct-current_frame branch); the cframe branch is covered by
test_runtime.py's R4 pair, so the matrix is not repeated here.
"""

import pytest

import pyprobe.observe.snapshot as snap_mod
from pyprobe import dto
from pyprobe.errors import NoThreadState
from pyprobe.observe.session import Session
from pyprobe.observe.snapshot import (
    build_thread,
    collect_snapshot,
    is_thread_idle_by_frames,
    is_thread_idle_by_stat,
)
from pyprobe.target.layout import resolve_layout
from tests.fakemem import (
    FakeMemory,
    build_code,
    build_frame,
    build_unicode,
)

NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"
INTERP = 0x200000
TSTATE_BASE = 0x300000
FRAME_BASE = 0x400000
CODE_BASE = 0x500000
PID = 4242


@pytest.fixture(scope="module")
def layout():
    return resolve_layout("3.13", overrides_path=NO_OVERRIDE)


def _session(layout, mem):
    return Session(
        pid=PID, exe_path="/x/python", runtime_addr=0x100000,
        interp_addr=INTERP, trampoline_addr=0, layout=layout,
        proc_info=dto.ProcessInfo(pid=PID, cmdline="python3 x",
                                  exe_path="/x/python"),
        view_factory=lambda pid: mem,
        names={101: "main", 102: "bg-worker"})


def _build_thread_graph(mem, layout, tref_addr, frame_addr, code_addr, *,
                        thread_id, native_tid, name, next_addr=0):
    """One tstate (direct current_frame) + one frame + code + name string."""
    import struct

    end = max(layout.get("ThreadState.next"),
              layout.get("ThreadState.thread_id"),
              layout.get("ThreadState.native_thread_id"),
              layout.get("ThreadState.current_frame")) + 8
    blob = bytearray(end)
    struct.pack_into("<Q", blob, layout.get("ThreadState.next"), next_addr)
    struct.pack_into("<Q", blob, layout.get("ThreadState.thread_id"),
                     thread_id)
    struct.pack_into("<Q", blob, layout.get("ThreadState.native_thread_id"),
                     native_tid)
    struct.pack_into("<Q", blob, layout.get("ThreadState.current_frame"),
                     frame_addr)
    mem.add(tref_addr, bytes(blob))

    name_addr = 0x380000 + code_addr
    build_unicode(mem, layout, name_addr, name)
    build_code(mem, layout, code_addr, name_addr, name_addr, 10, 0)
    build_frame(mem, layout, frame_addr, code_addr, 0,
                code_addr + layout.get("CodeObject.co_code_adaptive"))


def _link_head(mem, layout, first_tstate):
    mem.add_ptr(INTERP + layout.get("InterpreterState.threads")
                + layout.get("pythreads.head"), first_tstate)


class TestCollectSnapshot:
    def test_threads_sorted_and_named(self, layout):
        """SN2：names 按 thread_id 贴名；native_tid 升序（复刻约束——
        链序 102→101 打乱，输出必须升序）。"""
        mem = FakeMemory()
        _build_thread_graph(mem, layout, TSTATE_BASE, FRAME_BASE,
                            CODE_BASE, thread_id=102, native_tid=102,
                            name="worker", next_addr=TSTATE_BASE + 0x1000)
        _build_thread_graph(mem, layout, TSTATE_BASE + 0x1000,
                            FRAME_BASE + 0x1000, CODE_BASE + 0x2000,
                            thread_id=101, native_tid=101, name="run")
        _link_head(mem, layout, TSTATE_BASE)
        threads = collect_snapshot(_session(layout, mem))
        assert [t.native_tid for t in threads] == [101, 102]
        by_tid = {t.native_tid: t for t in threads}
        assert by_tid[101].name == "main"
        assert by_tid[102].name == "bg-worker"
        assert by_tid[102].frames[0].name == "worker"

    def test_fresh_view_per_call(self, layout):
        """SN1（A4）：每次调用经 view_factory 新建视图——跨调用复用会
        供陈旧数据，工厂调用计数即守护。"""
        mem = FakeMemory()
        _link_head(mem, layout, 0)  # head=0 → 空链，隔离构图干扰
        calls = []
        s = _session(layout, mem)
        s.view_factory = lambda pid: (calls.append(pid), mem)[1]
        collect_snapshot(s)
        collect_snapshot(s)
        assert calls == [PID, PID]

    def test_no_thread_state_propagates(self, layout):
        """SN1：threads.head 读失败 → NoThreadState 原样传播（快照语义；
        ProcessExited 转换是 sampling 的策略，两层不混）。"""
        with pytest.raises(NoThreadState):
            collect_snapshot(_session(layout, FakeMemory()))


class TestBuildThread:
    def test_frames_and_fields(self, layout):
        """SN3：current_frame → walk_frames → ThreadInfo 全字段
        （thread_id 传递是 names 贴名的前提）。"""
        mem = FakeMemory()
        _build_thread_graph(mem, layout, TSTATE_BASE, FRAME_BASE,
                            CODE_BASE, thread_id=7, native_tid=101,
                            name="worker")
        from pyprobe.cpython.runtime import ThreadStateRef

        tref = ThreadStateRef(TSTATE_BASE, 7, 101)
        t = build_thread(_session(layout, mem), mem, tref)
        assert t.native_tid == 101
        assert t.thread_id == 7
        assert [f.name for f in t.frames] == ["worker"]

    def test_idle_hint_skips_frame_walk(self, layout):
        """SN3：idle_hint=True → 空帧 idle ThreadInfo——帧区故意不建，
        走了帧遍历就会失败（剪枝路径的负向守护）。"""
        mem = FakeMemory()
        import struct

        end = layout.get("ThreadState.current_frame") + 8
        blob = bytearray(end)
        struct.pack_into("<Q", blob,
                         layout.get("ThreadState.current_frame"), 0xDEAD00)
        mem.add(TSTATE_BASE, bytes(blob))
        from pyprobe.cpython.runtime import ThreadStateRef

        t = build_thread(_session(layout, mem), mem,
                         ThreadStateRef(TSTATE_BASE, 7, 101),
                         idle_hint=True)
        assert t.idle is True
        assert t.frames == []


class TestIdleByFrames:
    """SN4（复刻约束 空闲双启发式之帧侧）：顶帧惯用法匹配；纯函数。"""

    def _frames(self, name, filename):
        return [dto.FrameInfo(name=name, filename=filename, line=1)]

    def test_empty_frames(self):
        """SN4 边界：无帧 → False（无证据不判闲）。"""
        assert is_thread_idle_by_frames([]) is False

    def test_wait_threading(self):
        """SN4：threading.wait 是典型空闲（锁等待）。"""
        assert is_thread_idle_by_frames(
            self._frames("wait", "threading.py")) is True

    def test_wait_wrong_file(self):
        """SN4：同名 wait 但非 threading.py → False（防误杀业务 wait）。"""
        assert is_thread_idle_by_frames(
            self._frames("wait", "app.py")) is False

    def test_select_selectors(self):
        """SN4：selectors.select 是典型空闲（事件循环等待）。"""
        assert is_thread_idle_by_frames(
            self._frames("select", "selectors.py")) is True

    @pytest.mark.parametrize("filename", [
        "asyncore.py", "/vendor/zmq/loop.py", "gevent/hub.py",
        "tornado/ioloop.py"])
    def test_poll_family(self, filename):
        """SN4：poll + asyncore/zmq/gevent/tornado 文件名族。"""
        assert is_thread_idle_by_frames(
            self._frames("poll", filename)) is True

    def test_poll_wrong_name(self):
        """SN4：文件名命中但函数名非 poll → False。"""
        assert is_thread_idle_by_frames(
            self._frames("epoll_wait", "zmq/loop.py")) is False

    def test_none_filename(self):
        """SN4：filename None → False（无证据不判闲，同空帧边界）。"""
        assert is_thread_idle_by_frames(
            self._frames("wait", None)) is False

    def test_busy_function(self):
        """SN4：普通业务函数 → False。"""
        assert is_thread_idle_by_frames(
            self._frames("compute", "app/calc.py")) is False

    def test_second_frame_not_checked(self):
        """SN4：仅查顶帧——次帧命中空闲惯用法不影响判定（忙中调闲的
        嵌套不误判）。"""
        frames = [dto.FrameInfo("compute", "app.py", 1),
                  dto.FrameInfo("wait", "threading.py", 2)]
        assert is_thread_idle_by_frames(frames) is False


class TestIdleByStat:
    """SN4/SN5（复刻约束 空闲双启发式之 stat 侧）：经 procfs.read_stat_state。"""

    def test_non_running_state_is_idle(self, monkeypatch):
        """SN4：state != "R"（如 S 睡眠）→ idle。"""
        monkeypatch.setattr(snap_mod, "read_stat_state",
                            lambda pid, tid: "S")
        assert is_thread_idle_by_stat(PID, 101) is True

    def test_running_state_not_idle(self, monkeypatch):
        """SN4：state == "R" → 非 idle。"""
        monkeypatch.setattr(snap_mod, "read_stat_state",
                            lambda pid, tid: "R")
        assert is_thread_idle_by_stat(PID, 101) is False

    def test_unreadable_conservative_false(self, monkeypatch):
        """SN4 失败模式：stat 读不出（None）→ False 保守（旧 docstring
        语义：不压抑无法核实的线程）。"""
        monkeypatch.setattr(snap_mod, "read_stat_state",
                            lambda pid, tid: None)
        assert is_thread_idle_by_stat(PID, 101) is False
