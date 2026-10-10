"""Contract tests for cpython/frames.py (F1–F8).

Frame/code construction is Layout-driven (fakemem); version matrix pins
the walk against every supported layout since InterpreterFrame field
offsets diverge (3.11 f_code=32/previous=48 vs 3.12+ 0/8).
"""

import pytest

from pyprobe.cpython.frames import MAX_FRAMES, walk_frames
from pyprobe.target.layout import resolve_layout
from tests.fakemem import (
    FakeMemory,
    build_code,
    build_frame,
    build_unicode,
    linetable_simple_increments,
)

LAYOUTS = ["3.11", "3.12", "3.13", "3.14"]
NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"
FRAME_ADDR = 0x40000
CODE_ADDR = 0x10000
LT_OBJ_ADDR = 0x20000
NAME_ADDR = 0x30000
FILE_ADDR = 0x30100


@pytest.fixture(params=LAYOUTS, ids=LAYOUTS)
def layout(request):
    return resolve_layout(request.param, overrides_path=NO_OVERRIDE)


def _frame_with_code(mem, layout, frame_addr, code_addr, previous=0,
                     lasti=0, name="func", filename="app/main.py",
                     firstlineno=10, linetable=None):
    """Assemble one frame + its code object + name/filename strings.

    String addresses derive from code_addr so chained frames never share
    (and overwrite) each other's name/filename objects.
    """
    name_addr = 0x380000 + code_addr
    file_addr = name_addr + 0x800
    build_unicode(mem, layout, name_addr, name)
    build_unicode(mem, layout, file_addr, filename)
    lt_addr = 0
    if linetable is not None:
        from tests.fakemem import build_bytes
        build_bytes(mem, layout, LT_OBJ_ADDR, linetable)
        lt_addr = LT_OBJ_ADDR
    build_code(mem, layout, code_addr, name_addr, file_addr, firstlineno,
               lt_addr)
    prev_instr = (code_addr + layout.get("CodeObject.co_code_adaptive")
                  + lasti)
    build_frame(mem, layout, frame_addr, code_addr, previous, prev_instr)


class TestWalkFrames:
    def test_null_frame_addr(self, layout):
        """F2 边界：frame_addr==0 → 空栈（无线程帧的合法态，非错误）。"""
        assert walk_frames(FakeMemory(), layout, 0, 0) == []

    def test_unreadable_frame(self, layout):
        """F1 失败模式：帧地址不可读 → 空列表（截断语义，不抛异常）。"""
        assert walk_frames(FakeMemory(), layout, FRAME_ADDR, 0) == []

    def test_single_frame(self, layout):
        """F7/F8：单帧解析——名称/文件名/行号三字段齐备（行号经 linetable
        解析：lasti=2 → 第二区间 → firstlineno+3）。"""
        mem = FakeMemory()
        _frame_with_code(mem, layout, FRAME_ADDR, CODE_ADDR, lasti=2,
                         linetable=linetable_simple_increments())
        frames = walk_frames(mem, layout, FRAME_ADDR, 0)
        assert len(frames) == 1
        f = frames[0]
        assert f.name == "func"
        assert f.filename == "app/main.py"
        assert f.line == 13

    def test_two_frame_chain(self, layout):
        """F7：两帧链内层在前（栈展示顺序的复刻约束）。"""
        mem = FakeMemory()
        _frame_with_code(mem, layout, FRAME_ADDR, CODE_ADDR,
                         previous=FRAME_ADDR + 0x1000, name="inner")
        _frame_with_code(mem, layout, FRAME_ADDR + 0x1000, CODE_ADDR + 0x2000,
                         name="outer")
        frames = walk_frames(mem, layout, FRAME_ADDR, 0)
        assert [f.name for f in frames] == ["inner", "outer"]

    def test_skip_null_code(self, layout):
        """F3：f_code==0 的帧跳过不产出（CPython 帧链中的占位条目）。"""
        mem = FakeMemory()
        # 首帧无 code，previous 指向一个正常帧
        build_frame(mem, layout, FRAME_ADDR, 0, FRAME_ADDR + 0x1000, 0)
        _frame_with_code(mem, layout, FRAME_ADDR + 0x1000, CODE_ADDR,
                         name="real")
        frames = walk_frames(mem, layout, FRAME_ADDR, 0)
        assert [f.name for f in frames] == ["real"]

    def test_skip_trampoline(self, layout):
        """F4：f_code==trampoline_addr 的蹦床帧跳过（3.12 解释器蹦床
        不属于用户栈）。"""
        mem = FakeMemory()
        _frame_with_code(mem, layout, FRAME_ADDR, CODE_ADDR, name="trampoline")
        _frame_with_code(mem, layout, FRAME_ADDR + 0x1000, CODE_ADDR + 0x2000,
                         name="real", previous=0)
        # 链：real(previous=trampoline) → trampoline(previous=0)
        build_frame(mem, layout, FRAME_ADDR + 0x1000, CODE_ADDR + 0x2000,
                    FRAME_ADDR, 0)
        frames = walk_frames(mem, layout, FRAME_ADDR + 0x1000, CODE_ADDR)
        assert [f.name for f in frames] == ["real"]

    def test_trampoline_zero_does_not_skip(self, layout):
        """F4 配套：trampoline_addr==0 时同名 code 不跳过（3.11/3.13 无
        蹦床，0 不得被当蹦床地址误杀正常帧）。"""
        mem = FakeMemory()
        _frame_with_code(mem, layout, FRAME_ADDR, CODE_ADDR, name="func")
        frames = walk_frames(mem, layout, FRAME_ADDR, 0)
        assert [f.name for f in frames] == ["func"]

    def test_circular_chain_hits_max(self, layout):
        """F2：环形帧链在 MAX_FRAMES 处截断（损坏内存/并发改写防护，
        防无限循环挂住观测）。"""
        mem = FakeMemory()
        _frame_with_code(mem, layout, FRAME_ADDR, CODE_ADDR,
                         previous=FRAME_ADDR)  # 自环
        frames = walk_frames(mem, layout, FRAME_ADDR, 0)
        assert len(frames) == MAX_FRAMES

    def test_stale_tail_frame_filtered(self, layout):
        """F6：name 与 filename 皆 None 的陈旧尾帧终止遍历（3.13
        datastack 残留指向回收 code 对象的实战教训，注释复刻）。"""
        mem = FakeMemory()
        _frame_with_code(mem, layout, FRAME_ADDR, CODE_ADDR, name="real",
                         previous=FRAME_ADDR + 0x1000)
        # 尾帧指向的 code 对象区域全 0（name/filename 地址为 0 → 皆 None）
        build_frame(mem, layout, FRAME_ADDR + 0x1000, CODE_ADDR + 0x2000, 0, 0)
        mem.add(CODE_ADDR + 0x2000,
                bytes(layout.get("CodeObject.co_code_adaptive") + 8))
        frames = walk_frames(mem, layout, FRAME_ADDR, 0)
        assert [f.name for f in frames] == ["real"]

    def test_code_header_unreadable_truncates(self, layout):
        """F5：帧可读但 code 头不可读 → 截断（已收集帧保留，不整链丢弃）。"""
        mem = FakeMemory()
        _frame_with_code(mem, layout, FRAME_ADDR, CODE_ADDR, name="real",
                         previous=FRAME_ADDR + 0x1000)
        # 第二帧存在，但其 code 区域缺失
        build_frame(mem, layout, FRAME_ADDR + 0x1000, 0xDEAD00, 0, 0)
        frames = walk_frames(mem, layout, FRAME_ADDR, 0)
        assert [f.name for f in frames] == ["real"]
