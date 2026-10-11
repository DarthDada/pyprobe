"""Contract tests for cpython/runtime.py (R1–R6).

Graph construction is Layout-driven; version-dependent branches (cframe
indirection vs direct current_frame, 3.12-only trampoline) are pinned in
pairs across the matrix.
"""

import pytest

from pyprobe.cpython.runtime import (
    MAX_THREADS,
    current_frame_of,
    read_thread_chain,
    resolve_interpreter,
    resolve_trampoline,
)
from pyprobe.errors import NoInterpreterState, NoThreadState
from pyprobe.target.layout import resolve_layout
from tests.fakemem import FakeMemory, build_tstate

LAYOUTS = ["3.11", "3.12", "3.13", "3.14"]
NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"
RUNTIME_ADDR = 0x100000
INTERP_ADDR = 0x200000
TSTATE_BASE = 0x300000


@pytest.fixture(params=LAYOUTS, ids=LAYOUTS)
def layout(request):
    return resolve_layout(request.param, overrides_path=NO_OVERRIDE)


def _interp_base(layout):
    return RUNTIME_ADDR + layout.get("RuntimeState.interpreters")


class TestResolveInterpreter:
    def test_main_preferred(self, layout):
        """R5：main 非 0 时优先（单解释器进程的常态路径）。"""
        mem = FakeMemory()
        mem.add_ptr(_interp_base(layout) + layout.get("pyinterpreters.main"),
                    INTERP_ADDR)
        assert resolve_interpreter(mem, layout, RUNTIME_ADDR) == INTERP_ADDR

    def test_head_fallback(self, layout):
        """R5：main 为 0 → head 回退（main 尚未发布/已析构的窗口期）。"""
        mem = FakeMemory()
        base = _interp_base(layout)
        mem.add_ptr(base + layout.get("pyinterpreters.main"), 0)
        mem.add_ptr(base + layout.get("pyinterpreters.head"), INTERP_ADDR)
        assert resolve_interpreter(mem, layout, RUNTIME_ADDR) == INTERP_ADDR

    def test_both_zero_raises(self, layout):
        """R5 失败模式：main/head 均 0 → NoInterpreterState()（默认消息
        复刻，CLI `[!]` 可见）。"""
        mem = FakeMemory()
        base = _interp_base(layout)
        mem.add_ptr(base + layout.get("pyinterpreters.main"), 0)
        mem.add_ptr(base + layout.get("pyinterpreters.head"), 0)
        with pytest.raises(NoInterpreterState):
            resolve_interpreter(mem, layout, RUNTIME_ADDR)

    def test_unreadable_raises(self, layout):
        """R5 失败模式：_PyRuntime 区域不可读 → NoInterpreterState()。"""
        with pytest.raises(NoInterpreterState):
            resolve_interpreter(FakeMemory(), layout, RUNTIME_ADDR)


class TestResolveTrampoline:
    def test_312_reads_value(self):
        """R6：3.12 有 interpreter_trampoline 字段——读出即返回（蹦床帧
        过滤的输入）。"""
        lay = resolve_layout("3.12", overrides_path=NO_OVERRIDE)
        mem = FakeMemory()
        mem.add_ptr(INTERP_ADDR
                    + lay.get("InterpreterState.interpreter_trampoline"),
                    0xDEAD0000)
        assert resolve_trampoline(mem, lay, INTERP_ADDR) == 0xDEAD0000

    @pytest.mark.parametrize("version", ["3.11", "3.13", "3.14"])
    def test_no_field_returns_zero(self, version):
        """R6：字段缺席的版本 → 0（"无蹦床可跳过"哨兵，frames 据此不过滤）。"""
        lay = resolve_layout(version, overrides_path=NO_OVERRIDE)
        assert resolve_trampoline(FakeMemory(), lay, INTERP_ADDR) == 0

    def test_read_failure_returns_zero(self):
        """R6 失败模式：字段存在但读失败 → 0（防御降级，不抛异常）。"""
        lay = resolve_layout("3.12", overrides_path=NO_OVERRIDE)
        assert resolve_trampoline(FakeMemory(), lay, INTERP_ADDR) == 0


class TestReadThreadChain:
    def _build_chain(self, mem, layout, specs):
        """specs: [(thread_id, native_tid), ...] linked in order."""
        mem.add_ptr(INTERP_ADDR + layout.get("InterpreterState.threads")
                    + layout.get("pythreads.head"), TSTATE_BASE)
        for i, (tid, ntid) in enumerate(specs):
            nxt = TSTATE_BASE + (i + 1) * 0x1000 if i + 1 < len(specs) else 0
            build_tstate(mem, layout, TSTATE_BASE + i * 0x1000, nxt, tid,
                         ntid)

    def test_walks_all_threads(self, layout):
        """R3：三线程链全量走出，字段与 tstate_addr 记录正确。"""
        mem = FakeMemory()
        self._build_chain(mem, layout, [(11, 101), (22, 102), (33, 103)])
        chain = read_thread_chain(mem, layout, INTERP_ADDR)
        assert len(chain) == 3
        assert [(t.thread_id, t.native_tid) for t in chain] == [
            (11, 101), (22, 102), (33, 103)]
        assert chain[0].tstate_addr == TSTATE_BASE

    def test_head_unreadable_raises_no_thread_state(self, layout):
        """R1 失败模式：threads.head 读失败 → NoThreadState（消息复刻
        "failed to read threads.head"）。"""
        with pytest.raises(NoThreadState,
                           match="failed to read threads.head"):
            read_thread_chain(FakeMemory(), layout, INTERP_ADDR)

    def test_mid_chain_unreadable_truncates(self, layout):
        """R2：中间节点不可读 → 截断返回已收集（并发退出的线程不让
        整链数据丢失）。"""
        mem = FakeMemory()
        self._build_chain(mem, layout, [(11, 101), (22, 102)])
        # 第三节点地址存在于链中但无内存
        build_tstate(mem, layout, TSTATE_BASE + 0x1000, 0xDEAD00, 22, 102)
        chain = read_thread_chain(mem, layout, INTERP_ADDR)
        assert [(t.thread_id,) for t in chain] == [(11,), (22,)]

    def test_max_threads_cap(self, layout):
        """R3：链长超 MAX_THREADS(256) 截断（损坏链/环链防护，复刻约束）。"""
        mem = FakeMemory()
        mem.add_ptr(INTERP_ADDR + layout.get("InterpreterState.threads")
                    + layout.get("pythreads.head"), TSTATE_BASE)
        # 紧凑自增链，避免 300 个独立区域
        for i in range(MAX_THREADS + 10):
            build_tstate(mem, layout, TSTATE_BASE + i * 0x100,
                         TSTATE_BASE + (i + 1) * 0x100, i, i)
        chain = read_thread_chain(mem, layout, INTERP_ADDR)
        assert len(chain) == MAX_THREADS


class TestCurrentFrameOf:
    def test_direct_field_313_plus(self):
        """R4：3.13/3.14 直达字段 ThreadState.current_frame（cframe
        间接层已随 3.13 移除）。"""
        for version in ("3.13", "3.14"):
            lay = resolve_layout(version, overrides_path=NO_OVERRIDE)
            mem = FakeMemory()
            mem.add_ptr(TSTATE_BASE + lay.get("ThreadState.current_frame"),
                        0xF4A4E)
            assert current_frame_of(mem, lay, TSTATE_BASE) == 0xF4A4E

    def test_cframe_indirection_311_312(self):
        """R4：3.11/3.12 经 cframe→CFrame.current_frame 间接解析
        （版本差异只住 Layout 的探针分支）。"""
        for version in ("3.11", "3.12"):
            lay = resolve_layout(version, overrides_path=NO_OVERRIDE)
            mem = FakeMemory()
            cframe_addr = 0x500000
            mem.add_ptr(TSTATE_BASE + lay.get("ThreadState.cframe"),
                        cframe_addr)
            mem.add_ptr(cframe_addr + lay.get("CFrame.current_frame"),
                        0xF4A4E)
            assert current_frame_of(mem, lay, TSTATE_BASE) == 0xF4A4E

    def test_null_cframe_returns_zero(self):
        """R4 失败模式：cframe==0 → 0（线程无解释器帧的合法态）。"""
        lay = resolve_layout("3.12", overrides_path=NO_OVERRIDE)
        mem = FakeMemory()
        mem.add_ptr(TSTATE_BASE + lay.get("ThreadState.cframe"), 0)
        assert current_frame_of(mem, lay, TSTATE_BASE) == 0

    def test_unreadable_returns_zero(self, layout):
        """R4 失败模式：tstate 不可读 → 0（调用方按"无帧"降级，不抛）。"""
        assert current_frame_of(FakeMemory(), layout, TSTATE_BASE) == 0
