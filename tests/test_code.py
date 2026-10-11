"""Contract tests for cpython/code.py (C1–C7).

Two tiers: hand-built linetables pin the PEP 626 decode rules (C1–C3) and
the remote-read fallbacks (C4); the spec-oracle tier (C7) pins the pure
decoder against CPython's own ``co_lines()`` ground truth on real code
objects — the strongest available oracle for this algorithm (§10.2-1).
"""

import pytest

from pyprobe.cpython.code import (
    addr2line,
    line_for_offset,
    read_code_header,
)
from pyprobe.target.layout import resolve_layout
from tests.fakemem import (
    FakeMemory,
    build_bytes,
    build_code,
    linetable_no_line,
    linetable_simple_increments,
    linetable_with_no_line,
)

LAYOUTS = ["3.11", "3.12", "3.13", "3.14"]
NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"
CODE_ADDR = 0x10000
LT_OBJ_ADDR = 0x20000
NAME_ADDR = 0x30000
FILE_ADDR = 0x30000 + 0x100


@pytest.fixture(params=LAYOUTS, ids=LAYOUTS)
def layout(request):
    return resolve_layout(request.param, overrides_path=NO_OVERRIDE)


class TestLineForOffset:
    """C1–C3：纯解码器（无内存读取），手工 linetable 逐条钉 PEP 626 规则。"""

    def test_simple_increments(self):
        """C1：code 11/12/10 的 +1/+2/+0 行增量与 2 字节区间边界。"""
        lt = linetable_simple_increments()  # firstlineno=10
        assert line_for_offset(lt, 0, 10) == 11
        assert line_for_offset(lt, 2, 10) == 13
        assert line_for_offset(lt, 4, 10) == 13
        assert line_for_offset(lt, 6, 10) == 13

    def test_beyond_table_returns_last_line(self):
        """C2：lasti 超出表末 → 最后计算行（执行点在表尾之后的合法态）。"""
        lt = linetable_simple_increments()
        assert line_for_offset(lt, 100, 10) == 13

    def test_all_no_line_falls_back(self):
        """C3：全 code=15 表 → firstlineno（无行号信息的整体回退）。"""
        assert line_for_offset(linetable_no_line(), 4, 10) == 10

    def test_no_line_entry_falls_back(self):
        """C3：单个 code=15 条目 → firstlineno，其后条目行号不受污染
        （ar_line=-1 不重置 computed_line 的累计）。"""
        lt = linetable_with_no_line()
        assert line_for_offset(lt, 0, 10) == 11
        assert line_for_offset(lt, 2, 10) == 10  # 无行号区间 → 回退
        assert line_for_offset(lt, 4, 10) == 12

    def test_empty_linetable(self):
        """C3 边界：空表 → firstlineno。"""
        assert line_for_offset(b"", 0, 10) == 10

    def test_negative_lasti_returns_firstlineno(self):
        """C1 边界：负 lasti（上游钳位前的防御）→ 无任何条目覆盖 →
        firstlineno。"""
        lt = linetable_simple_increments()
        assert line_for_offset(lt, -1, 10) == 10

    def test_varint_signed_delta(self):
        """C1：code 13/14 的有符号 varint——6 位块、bit6 续位；符号解码为
        CPython signed varint（奇 uval 取 -(uval>>1)，偶取 uval>>1，**非
        zigzag**——Objects/codeobject.c scan_signed_varint 权威语义，
        C7 oracle 互证）。delta=-3 编码为 uval=7。"""
        lt = bytes([
            (14 << 3) | 0x80, 0x07,  # code=14, varint uval=7 → -(7>>1) = -3
            (11 << 3) | 0x80,        # code=11 → +1
        ])
        assert line_for_offset(lt, 0, 10) == 7   # 10 + (-3)
        assert line_for_offset(lt, 2, 10) == 8   # +1


class TestAddr2Line:
    """C4：远程读取包装的 firstlineno 回退矩阵。"""

    def test_resolves_via_code_object(self, layout):
        """C4 主路径：经 co_linetable 指针取 bytes 后解码。"""
        mem = FakeMemory()
        build_code(mem, layout, CODE_ADDR, NAME_ADDR, FILE_ADDR, 10,
                   LT_OBJ_ADDR)
        build_bytes(mem, layout, LT_OBJ_ADDR, linetable_simple_increments())
        assert addr2line(mem, layout, CODE_ADDR, 2, 10) == 13

    def test_no_linetable_ptr_returns_firstlineno(self, layout):
        """C4：co_linetable 指针读失败 → firstlineno（降级不失败）。"""
        assert addr2line(FakeMemory(), layout, CODE_ADDR, 0, 10) == 10

    def test_unreadable_linetable_bytes_returns_firstlineno(self, layout):
        """C4：linetable bytes 对象读失败 → firstlineno。"""
        mem = FakeMemory()
        build_code(mem, layout, CODE_ADDR, NAME_ADDR, FILE_ADDR, 10,
                   LT_OBJ_ADDR)  # LT_OBJ_ADDR 无对象
        assert addr2line(mem, layout, CODE_ADDR, 0, 10) == 10


class TestReadCodeHeader:
    def test_reads_fields(self, layout):
        """C5：firstlineno/filename/name 三字段经单次跨度读取出（A3：
        跨度心算内聚在 code.py，本测试钉其对外的字段正确性）。"""
        mem = FakeMemory()
        build_code(mem, layout, CODE_ADDR, NAME_ADDR, FILE_ADDR, 42,
                   LT_OBJ_ADDR)
        hdr = read_code_header(mem, layout, CODE_ADDR)
        assert hdr is not None
        assert hdr.firstlineno == 42
        assert hdr.filename_addr == FILE_ADDR
        assert hdr.name_addr == NAME_ADDR

    def test_unreadable_returns_none(self, layout):
        """C5 失败模式：code 对象不可读 → None（frames 据此截断帧链）。"""
        assert read_code_header(FakeMemory(), layout, CODE_ADDR) is None


# ---------------------------------------------------------------------------
# C7 spec-oracle: line_for_offset vs CPython co_lines() ground truth
# ---------------------------------------------------------------------------

def _oracle_sample_funcs():
    """Real code objects spanning diverse linetable shapes."""

    def simple(a):
        return a + 1

    def multiline(items):
        total = 0
        for i in items:
            total += i
        return total

    def branches(x):
        if x > 0:
            return "pos"
        elif x < 0:
            return "neg"
        return "zero"

    def nested():
        def inner():
            return [x * 2 for x in range(3)]

        return inner()

    return [simple, multiline, branches, nested]


def _expected_line(code_obj, lasti):
    """Ground truth from co_lines(): (start, end, line) half-open ranges,
    byte offsets matching our lasti convention (2 bytes per code unit)."""
    for start, end, line in code_obj.co_lines():
        if start <= lasti < end:
            return line if line is not None else code_obj.co_firstlineno
    return code_obj.co_firstlineno


class TestSpecOracleCoLines:
    def test_matches_co_lines_ground_truth(self):
        """C7 (§10.2-1)：对真实 code object 的每一个指令偏移，
        line_for_offset 必须与 CPython 自己的 co_lines() 解码逐点一致——
        我们的 PEP 626 解码器与权威实现互证（2026-10 审查验证过的方法）。"""
        for func in _oracle_sample_funcs():
            code_obj = func.__code__
            lt = code_obj.co_linetable
            fl = code_obj.co_firstlineno
            for lasti in range(0, len(code_obj.co_code), 2):
                assert line_for_offset(lt, lasti, fl) == (
                    _expected_line(code_obj, lasti)), (
                    f"{func.__qualname__} lasti={lasti}")
