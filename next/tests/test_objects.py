"""Contract tests for cpython/objects.py (O1–O11).

Version matrix per 测试数据纪律: builders in fakemem.py are Layout-driven,
so the same scenario runs against every supported version's real offsets —
a version-specific offset mistake fails loudly instead of being masked by
shared assumptions.
"""

import pytest

from pyprobe.cpython.objects import (
    MAX_STR_LEN,
    read_bytes,
    read_long,
    read_unicode,
)
from pyprobe.target.layout import resolve_layout
from tests.fakemem import (
    FakeMemory,
    build_bytes,
    build_long,
    build_unicode,
    build_unicode_kind,
    build_unicode_noncompact,
)

LAYOUTS = ["3.11", "3.12", "3.13", "3.14"]
NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"
ADDR = 0x10000


@pytest.fixture(params=LAYOUTS, ids=LAYOUTS)
def layout(request):
    return resolve_layout(request.param, overrides_path=NO_OVERRIDE)


class TestReadLong:
    def test_zero(self, layout):
        """O3：size==0 → 整数 0（空 digit 数组的合法值，非失败）。"""
        mem = FakeMemory()
        build_long(mem, layout, ADDR, 0)
        assert read_long(mem, layout, ADDR) == 0

    def test_single_digit(self, layout):
        """O3：单 digit 直读（小整数是 threading ident 的常态）。"""
        mem = FakeMemory()
        build_long(mem, layout, ADDR, 12345)
        assert read_long(mem, layout, ADDR) == 12345

    def test_single_digit_max(self, layout):
        """O3 边界：2**30-1（30 位 digit 容量的上沿，进位错误的暴露点）。"""
        mem = FakeMemory()
        build_long(mem, layout, ADDR, (1 << 30) - 1)
        assert read_long(mem, layout, ADDR) == (1 << 30) - 1

    def test_two_digit(self, layout):
        """O4：双 digit 合成 d0 | d1<<30（30 位基数拼接，位移错即错值）。"""
        mem = FakeMemory()
        build_long(mem, layout, ADDR, 1 << 30)
        assert read_long(mem, layout, ADDR) == 1 << 30

    def test_two_digit_large(self, layout):
        """O4 边界：2**60-1（双 digit 容量上沿）。"""
        mem = FakeMemory()
        build_long(mem, layout, ADDR, (1 << 60) - 1)
        assert read_long(mem, layout, ADDR) == (1 << 60) - 1

    def test_too_large_returns_none(self, layout):
        """O5：size>2 → None（超出支持范围不猜值——threading ident 恒为
        小整数，硬解大数只会产出误导性垃圾）。"""
        import struct as _st

        mem = FakeMemory()
        lv_tag_off = layout.get_or("LongObject.long_value.lv_tag")
        digit_off = layout.get_or("LongObject.long_value.ob_digit",
                                  layout.get_or("LongObject.ob_digit"))
        blob = bytearray(digit_off + 3 * layout.get("digit_size"))
        if lv_tag_off is not None:
            _st.pack_into("<Q", blob, lv_tag_off, 3 << 3)  # size=3
        else:
            _st.pack_into("<q", blob, layout.get("LongObject.ob_size"), 3)
        mem.add(ADDR, bytes(blob))
        assert read_long(mem, layout, ADDR) is None

    def test_unreadable_returns_none(self, layout):
        """O1/O2 失败模式：对象地址不可读 → None。"""
        assert read_long(FakeMemory(), layout, ADDR) is None

    def test_unreadable_digit_returns_none(self, layout):
        """O3/O4 失败模式：tag/size 可读但 digit 区不可读 → None 传播
        （旧 read_pylong L44-45 语义：单 digit 失败不伪装成 0）。"""
        import struct as _st

        mem = FakeMemory()
        lv_tag_off = layout.get_or("LongObject.long_value.lv_tag")
        digit_off = layout.get_or("LongObject.long_value.ob_digit",
                                  layout.get_or("LongObject.ob_digit"))
        blob = bytearray(digit_off)  # 只到 digit 之前，digit 区缺席
        if lv_tag_off is not None:
            _st.pack_into("<Q", blob, lv_tag_off, 1 << 3)  # size=1
        else:
            _st.pack_into("<q", blob, layout.get("LongObject.ob_size"), 1)
        mem.add(ADDR, bytes(blob))
        assert read_long(mem, layout, ADDR) is None


class TestReadBytes:
    def test_normal(self, layout):
        """O6：payload 原样读出（linetable 等二进制体的主路径）。"""
        mem = FakeMemory()
        build_bytes(mem, layout, ADDR, b"\x01\x02\x03\x04")
        assert read_bytes(mem, layout, ADDR) == b"\x01\x02\x03\x04"

    def test_empty(self, layout):
        """O6 边界：size=0 → b""（合法空 bytes，非失败）。"""
        mem = FakeMemory()
        build_bytes(mem, layout, ADDR, b"")
        assert read_bytes(mem, layout, ADDR) == b""

    def test_unreadable_returns_none(self, layout):
        """O6 失败模式：头部不可读 → None。"""
        assert read_bytes(FakeMemory(), layout, ADDR) is None

    def test_oversize_returns_none(self, layout):
        """O6：size > MAX_STR_LEN → None（损坏内存里的天文数字尺寸防御，
        防按垃圾尺寸发起巨型读）。"""
        import struct as _st

        mem = FakeMemory()
        sval_off = layout.get("BytesObject.ob_sval")
        blob = bytearray(sval_off)
        _st.pack_into("<q", blob, layout.get("VarObject.ob_size"),
                      MAX_STR_LEN + 1)
        mem.add(ADDR, bytes(blob))
        assert read_bytes(mem, layout, ADDR) is None


class TestReadUnicode:
    def test_ascii_compact(self, layout):
        """O9：compact+ascii 内联数据（CPython 字符串的绝对主流形态）。"""
        mem = FakeMemory()
        build_unicode(mem, layout, ADDR, "hello")
        assert read_unicode(mem, layout, ADDR) == "hello"

    def test_ascii_compact_empty(self, layout):
        """O9 边界：空串 → ""（length=0 合法，非失败）。"""
        mem = FakeMemory()
        build_unicode(mem, layout, ADDR, "")
        assert read_unicode(mem, layout, ADDR) == ""

    def test_utf16_compact(self, layout):
        """O10：compact kind=2（utf-16-le）非 ascii 数据。"""
        mem = FakeMemory()
        build_unicode_kind(mem, layout, ADDR, "héllo", kind=2)
        assert read_unicode(mem, layout, ADDR) == "héllo"

    def test_latin1_compact(self, layout):
        """O10：kind=1（latin-1）解码分支。"""
        mem = FakeMemory()
        build_unicode_kind(mem, layout, ADDR, "café", kind=1)
        assert read_unicode(mem, layout, ADDR) == "café"

    def test_utf32_compact(self, layout):
        """O10：kind=4（utf-32-le）解码分支（含 BMP 外字符的串形态）。"""
        mem = FakeMemory()
        build_unicode_kind(mem, layout, ADDR, "a🐍b", kind=4)
        assert read_unicode(mem, layout, ADDR) == "a🐍b"

    def test_unknown_kind_returns_none(self, layout):
        """O10 失败模式：kind=3 非法 → None（损坏 state 不硬解）。"""
        import struct as _st

        mem = FakeMemory()
        compact_sz = layout.get("PyCompactUnicodeObject_size")
        blob = bytearray(compact_sz + 8)
        _st.pack_into("<q", blob, 16, 2)
        blob[32] = 0x20 | (3 << 2)  # compact, kind=3（保留值）
        mem.add(ADDR, bytes(blob))
        assert read_unicode(mem, layout, ADDR) is None

    def test_non_compact(self, layout):
        """O11：非 compact 经 data_any 指针寻址数据。"""
        mem = FakeMemory()
        build_unicode_noncompact(mem, layout, ADDR, "wörld", 0x20000)
        assert read_unicode(mem, layout, ADDR) == "wörld"

    def test_non_compact_null_data_ptr(self, layout):
        """O11 失败模式：data_any NULL → None（未物化数据不硬解）。"""
        mem = FakeMemory()
        full_sz = layout.get("PyUnicodeObject.data_any") + 8
        blob = bytearray(full_sz)
        import struct as _st

        _st.pack_into("<q", blob, 16, 5)
        blob[32] = 2 << 2  # compact=0, kind=2, data ptr 留 0
        mem.add(ADDR, bytes(blob))
        assert read_unicode(mem, layout, ADDR) is None

    def test_unreadable_header_returns_none(self, layout):
        """O7 失败模式：ASCII 头不可读 → None。"""
        assert read_unicode(FakeMemory(), layout, ADDR) is None

    def test_oversize_length_returns_none(self, layout):
        """O7：length > MAX_STR_LEN → None（损坏长度防御，同 O6）。"""
        import struct as _st

        mem = FakeMemory()
        ascii_sz = layout.get("PyASCIIObject_size")
        blob = bytearray(ascii_sz)
        _st.pack_into("<q", blob, 16, MAX_STR_LEN + 1)
        blob[32] = 0x60
        mem.add(ADDR, bytes(blob))
        assert read_unicode(mem, layout, ADDR) is None
