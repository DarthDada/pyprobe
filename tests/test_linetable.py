"""Unit tests for pyprobe.linetable — PEP 626 line-table resolution."""

import pytest

from pyprobe import offsets
from pyprobe.linetable import addr2line
from tests.helpers import (
    FakeReader, build_code_object, build_pybytes,
    linetable_no_line, linetable_simple_increments, linetable_with_no_line,
)

CODE_ADDR = 0x10000
LT_ADDR = 0x20000
FIRSTLINENO = 10


def _setup(reader, linetable_bytes):
    build_pybytes(reader, LT_ADDR, linetable_bytes)
    build_code_object(reader, CODE_ADDR, 0, 0, FIRSTLINENO, LT_ADDR)


class TestAddr2Line:
    def test_no_line_table_returns_firstlineno(self):
        """When co_linetable is NULL, addr2line returns firstlineno."""
        reader = FakeReader()
        build_code_object(reader, CODE_ADDR, 0, 0, FIRSTLINENO, 0)
        assert addr2line(reader, CODE_ADDR, 0, FIRSTLINENO) == FIRSTLINENO

    def test_all_no_line_entries(self):
        reader = FakeReader()
        _setup(reader, linetable_no_line(8))
        for lasti in [0, 2, 4, 16, 32]:
            assert addr2line(reader, CODE_ADDR, lasti, FIRSTLINENO) == FIRSTLINENO

    def test_simple_increments_lasti_0(self):
        """lasti=0 falls in the first entry (code=11, +1) → line 11."""
        reader = FakeReader()
        _setup(reader, linetable_simple_increments())
        assert addr2line(reader, CODE_ADDR, 0, FIRSTLINENO) == 11

    def test_simple_increments_lasti_2(self):
        """lasti=2 falls in the second entry (code=12, +2) → line 13."""
        reader = FakeReader()
        _setup(reader, linetable_simple_increments())
        assert addr2line(reader, CODE_ADDR, 2, FIRSTLINENO) == 13

    def test_simple_increments_lasti_4(self):
        """lasti=4 falls in the third entry (code=10, +0) → line 13."""
        reader = FakeReader()
        _setup(reader, linetable_simple_increments())
        assert addr2line(reader, CODE_ADDR, 4, FIRSTLINENO) == 13

    def test_simple_increments_lasti_6(self):
        """lasti=6 falls in the fourth entry (code=10, +0) → line 13."""
        reader = FakeReader()
        _setup(reader, linetable_simple_increments())
        assert addr2line(reader, CODE_ADDR, 6, FIRSTLINENO) == 13

    def test_lasti_beyond_table_returns_last_line(self):
        """lasti beyond the table returns the last computed ar_line."""
        reader = FakeReader()
        _setup(reader, linetable_simple_increments())
        # table covers [0,8); lasti=100 is beyond → returns last ar_line=13
        assert addr2line(reader, CODE_ADDR, 100, FIRSTLINENO) == 13

    def test_no_line_entry_falls_back_to_firstlineno(self):
        """A code=15 entry in the middle falls back to firstlineno."""
        reader = FakeReader()
        _setup(reader, linetable_with_no_line())
        assert addr2line(reader, CODE_ADDR, 0, FIRSTLINENO) == 11   # code=11, +1
        assert addr2line(reader, CODE_ADDR, 2, FIRSTLINENO) == FIRSTLINENO  # code=15
        assert addr2line(reader, CODE_ADDR, 4, FIRSTLINENO) == 12   # code=11, +1

    def test_empty_linetable(self):
        reader = FakeReader()
        _setup(reader, b"")
        assert addr2line(reader, CODE_ADDR, 0, FIRSTLINENO) == FIRSTLINENO

    def test_unreadable_linetable_returns_firstlineno(self):
        """If read_pybytes fails, addr2line returns firstlineno."""
        reader = FakeReader()
        build_code_object(reader, CODE_ADDR, 0, 0, FIRSTLINENO, LT_ADDR)
        # No PyBytes object at LT_ADDR → read returns None
        assert addr2line(reader, CODE_ADDR, 0, FIRSTLINENO) == FIRSTLINENO

    def test_negative_lasti_treated_as_zero(self):
        """Negative lasti (shouldn't happen but defensive) → treated as 0."""
        reader = FakeReader()
        _setup(reader, linetable_simple_increments())
        result = addr2line(reader, CODE_ADDR, -1, FIRSTLINENO)
        # lasti=-1: ar_end(0) <= -1 is False, loop doesn't run → ar_line=-1 → firstlineno
        assert result == FIRSTLINENO
