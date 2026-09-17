"""Unit tests for pyprobe.pyobject — remote PyLong/PyBytes/PyUnicode readers."""

import pytest

from pyprobe import offsets
from pyprobe.memory import MAX_STR_LEN
from pyprobe.pyobject import read_pylong, read_pybytes, read_pyunicode
from tests.helpers import (
    FakeReader, build_pylong, build_pybytes,
    build_pyunicode, build_pyunicode_utf16, build_pyunicode_noncompact,
)

ADDR = 0x10000


class TestReadPyLong:
    def test_zero(self):
        reader = FakeReader()
        build_pylong(reader, ADDR, 0)
        assert read_pylong(reader, ADDR) == 0

    def test_single_digit(self):
        reader = FakeReader()
        build_pylong(reader, ADDR, 42)
        assert read_pylong(reader, ADDR) == 42

    def test_single_digit_max(self):
        reader = FakeReader()
        build_pylong(reader, ADDR, (1 << 30) - 1)
        assert read_pylong(reader, ADDR) == (1 << 30) - 1

    def test_two_digit(self):
        reader = FakeReader()
        build_pylong(reader, ADDR, (1 << 30))
        assert read_pylong(reader, ADDR) == (1 << 30)

    def test_two_digit_large(self):
        reader = FakeReader()
        value = (1 << 60) - 1
        build_pylong(reader, ADDR, value)
        assert read_pylong(reader, ADDR) == value

    def test_unreadable_returns_none(self):
        reader = FakeReader()
        assert read_pylong(reader, ADDR) is None

    def test_too_large_returns_none(self):
        """Three-digit longs are not handled → read_pylong returns None."""
        reader = FakeReader()
        tag_off = offsets.get("LongObject.long_value.lv_tag")
        digit_off = offsets.get("LongObject.long_value.ob_digit")
        blob = bytearray(digit_off + 12)
        import struct
        struct.pack_into("<Q", blob, tag_off, 24)  # size=3
        reader.add(ADDR, bytes(blob))
        assert read_pylong(reader, ADDR) is None


class TestReadPyBytes:
    def test_normal(self):
        reader = FakeReader()
        data = b"hello world"
        build_pybytes(reader, ADDR, data)
        assert read_pybytes(reader, ADDR) == data

    def test_empty(self):
        reader = FakeReader()
        build_pybytes(reader, ADDR, b"")
        assert read_pybytes(reader, ADDR) == b""

    def test_unreadable_returns_none(self):
        reader = FakeReader()
        assert read_pybytes(reader, ADDR) is None

    def test_oversize_returns_none(self):
        reader = FakeReader()
        import struct
        blob = bytearray(offsets.get("BytesObject.ob_sval"))
        struct.pack_into("<q", blob, offsets.get("VarObject.ob_size"),
                         MAX_STR_LEN + 1)
        reader.add(ADDR, bytes(blob))
        assert read_pybytes(reader, ADDR) is None

    def test_negative_size_returns_none(self):
        reader = FakeReader()
        import struct
        blob = bytearray(offsets.get("BytesObject.ob_sval"))
        struct.pack_into("<q", blob, offsets.get("VarObject.ob_size"), -1)
        reader.add(ADDR, bytes(blob))
        assert read_pybytes(reader, ADDR) is None


class TestReadPyUnicode:
    def test_ascii_compact(self):
        reader = FakeReader()
        build_pyunicode(reader, ADDR, "hello")
        assert read_pyunicode(reader, ADDR) == "hello"

    def test_ascii_compact_empty(self):
        reader = FakeReader()
        build_pyunicode(reader, ADDR, "")
        assert read_pyunicode(reader, ADDR) == ""

    def test_ascii_compact_unicode_in_ascii(self):
        reader = FakeReader()
        build_pyunicode(reader, ADDR, "cafe")
        assert read_pyunicode(reader, ADDR) == "cafe"

    def test_utf16_compact(self):
        reader = FakeReader()
        text = "héllo wörld"
        build_pyunicode_utf16(reader, ADDR, text)
        assert read_pyunicode(reader, ADDR) == text

    def test_non_compact(self):
        reader = FakeReader()
        text = "naïve"
        build_pyunicode_noncompact(reader, ADDR, text, ADDR + 0x100)
        assert read_pyunicode(reader, ADDR) == text

    def test_unreadable_returns_none(self):
        reader = FakeReader()
        assert read_pyunicode(reader, ADDR) is None

    def test_zero_length_ascii(self):
        reader = FakeReader()
        build_pyunicode(reader, ADDR, "")
        assert read_pyunicode(reader, ADDR) == ""

    def test_non_compact_null_data_ptr(self):
        reader = FakeReader()
        import struct
        full_sz = offsets.get("PyUnicodeObject.data_any") + 8
        blob = bytearray(full_sz)
        blob[32] = 0x08  # compact=0, kind=2, is_ascii=0
        # data_any = 0 (null)
        reader.add(ADDR, bytes(blob))
        assert read_pyunicode(reader, ADDR) is None
