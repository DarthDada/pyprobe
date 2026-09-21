"""Unit tests for the pure helpers of pyprobe/syscall_render.py.

No ptrace involved: a FakeReader (tests/helpers.py) supplies remote memory.
Split out of ``test_syscall_trace.py`` (TODO §8.3) alongside the source
split — pure rendering helpers now live in ``syscall_render.py``.
"""

import struct

import pytest

from pyprobe.syscall_render import (
    escape_bytes, truncate_escaped, render_str_arg,
    _read_cstr, _read_timespec, _decode_flags,
    _decode_args, _fill_out_args,
    TraceFilter, format_summary, SyscallStat, STR_MAX,
)
from pyprobe.types import SyscallEvent
from tests.helpers import FakeReader


class TestEscapeBytes:
    def test_plain_ascii(self):
        assert escape_bytes(b"hello") == "hello"

    def test_quotes_and_backslash(self):
        assert escape_bytes(b'a"b\\c') == 'a\\"b\\\\c'

    def test_newline_octal(self):
        assert escape_bytes(b"a\nb") == "a\\012b"

    def test_nul_octal(self):
        assert escape_bytes(b"a\x00b") == "a\\000b"

    def test_high_bytes(self):
        assert escape_bytes(b"\xff") == "\\377"

    def test_empty(self):
        assert escape_bytes(b"") == ""


class TestTruncate:
    def test_short_untouched(self):
        assert truncate_escaped("abc") == "abc"

    def test_exact_limit_untouched(self):
        s = "a" * STR_MAX
        assert truncate_escaped(s) == s

    def test_over_limit_ellipsis(self):
        out = truncate_escaped("a" * (STR_MAX + 10))
        assert out == "a" * STR_MAX + "..."

    def test_render_str_arg_truncates(self):
        out = render_str_arg(b"x" * 100)
        assert out == '"' + "x" * STR_MAX + '..."'

    def test_render_str_arg_verbose_keeps(self):
        out = render_str_arg(b"x" * 100, verbose=True)
        assert out == '"' + "x" * 100 + '"'


class TestReadCstr:
    def test_reads_nul_terminated(self):
        r = FakeReader()
        r.add(0x1000, b"/tmp/data.txt\x00rest")
        assert _read_cstr(r, 0x1000) == '"/tmp/data.txt"'

    def test_truncates_long(self):
        r = FakeReader()
        r.add(0x1000, b"a" * 300 + b"\x00")
        out = _read_cstr(r, 0x1000)
        assert out == '"' + "a" * STR_MAX + '..."'

    def test_null_addr(self):
        assert _read_cstr(FakeReader(), 0) == "NULL"

    def test_unreadable_addr(self):
        out = _read_cstr(FakeReader(), 0xdeadbeef)
        assert out == "0xdeadbeef"

    def test_partial_then_unreadable(self):
        r = FakeReader()
        # first chunk readable without NUL, second chunk missing
        r.add(0x1000, b"abc")
        out = _read_cstr(r, 0x1000)
        assert out == '"abc"'


class TestReadTimespec:
    def test_reads_struct(self):
        r = FakeReader()
        r.add(0x2000, struct.pack("<qq", 1, 500000000))
        assert _read_timespec(r, 0x2000) == "{1, 500000000}"

    def test_null(self):
        assert _read_timespec(FakeReader(), 0) == "NULL"

    def test_unreadable(self):
        assert _read_timespec(FakeReader(), 0xcafe) == "0xcafe"


class TestDecodeFlags:
    def test_zero_accmode(self):
        assert _decode_flags(0, {"0": "O_RDONLY"} | {0: "O_RDONLY"}) == \
            "O_RDONLY"

    def test_or_combination(self):
        table = {0x40: "O_CREAT", 0x200: "O_TRUNC", 0x80000: "O_CLOEXEC"}
        assert _decode_flags(0x40 | 0x200, table) == "O_CREAT|O_TRUNC"

    def test_unknown_bits_hex(self):
        table = {0x40: "O_CREAT"}
        assert _decode_flags(0x40 | 0x1000, table) == "O_CREAT|0x1000"

    def test_empty_value(self):
        assert _decode_flags(0, {0x1: "PROT_READ"}) == "0"


class TestDecodeArgs:
    def test_openat_full(self):
        r = FakeReader()
        r.add(0x1000, b"/etc/hosts\x00")
        args = [0xFFFFFFFFFFFFFF10, 0x1000, 0x80000, 0]  # AT_FDCWD, path, O_CLOEXEC? no: flags
        # openat(dirfd, path, flags, mode): 0x80000 = O_CLOEXEC
        out = _decode_args(r, "openat", args[:3])
        assert out == 'AT_FDCWD, "/etc/hosts", O_CLOEXEC'

    def test_openat_with_mode(self):
        r = FakeReader()
        r.add(0x1000, b"newfile\x00")
        out = _decode_args(r, "openat",
                           [0xFFFFFFFFFFFFFF10, 0x1000, 0x241, 0o644])
        # 0x241 = O_WRONLY|O_CREAT|O_TRUNC
        assert out == 'AT_FDCWD, "newfile", O_WRONLY|O_CREAT|O_TRUNC, 0o644'

    def test_read_buf_as_addr(self):
        out = _decode_args(FakeReader(), "read", [3, 0x7f00, 512])
        assert out == "3, 0x7f00, 512"

    def test_mmap_flags(self):
        out = _decode_args(FakeReader(), "mmap",
                           [0, 4096, 3, 0x22, -1, 0])
        # prot=3 -> PROT_READ|PROT_WRITE; flags=0x22 -> MAP_PRIVATE|MAP_ANONYMOUS
        assert out == "0, 4096, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0"

    def test_clock_nanosleep(self):
        r = FakeReader()
        r.add(0x3000, struct.pack("<qq", 0, 1000000))
        out = _decode_args(r, "clock_nanosleep",
                           [0, 0, 0x3000, 0])
        assert out == "0, 0, {0, 1000000}, 0"

    def test_unknown_syscall_raw_hex(self):
        out = _decode_args(FakeReader(), "some_future_syscall",
                           [1, 2, 3])
        assert out == "0x1, 0x2, 0x3"

    def test_fewer_args_than_kinds(self):
        out = _decode_args(FakeReader(), "close", [3])
        assert out == "3"

    def test_null_buf(self):
        out = _decode_args(FakeReader(), "read", [3, 0, 100])
        assert out == "3, NULL, 100"


class TestFillOutArgs:
    def _reader_with_buf(self, addr, data):
        r = FakeReader()
        r.add(addr, data)
        return r

    def test_read_content_spliced(self):
        r = self._reader_with_buf(0x7f00, b"hello\x00world")
        rendered = "3, 0x7f00, 512"
        out = _fill_out_args(r, "read", [3, 0x7f00, 512], 5, rendered)
        assert out == '3, 0x7f00/"hello", 512'

    def test_write_content_spliced(self):
        r = self._reader_with_buf(0x7f00, b"output!")
        rendered = "1, 0x7f00, 7"
        out = _fill_out_args(r, "write", [1, 0x7f00, 7], 7, rendered)
        assert out == '1, 0x7f00/"output!", 7'

    def test_error_ret_skips_buf_out(self):
        r = self._reader_with_buf(0x7f00, b"stale")
        rendered = "3, 0x7f00, 512"
        out = _fill_out_args(r, "read", [3, 0x7f00, 512], -1, rendered)
        assert out == rendered  # unchanged

    def test_no_buf_syscall_unchanged(self):
        out = _fill_out_args(FakeReader(), "close", [3], 0, "3")
        assert out == "3"

    def test_unreadable_buf_unchanged(self):
        rendered = "3, 0x7f00, 512"
        out = _fill_out_args(FakeReader(), "read", [3, 0x7f00, 512], 5,
                             rendered)
        assert out == rendered

    def test_truncated_content(self):
        r = self._reader_with_buf(0x7f00, b"x" * 100)
        rendered = "3, 0x7f00, 100"
        out = _fill_out_args(r, "read", [3, 0x7f00, 100], 100, rendered)
        assert '0x7f00/"' in out
        assert '...' in out  # truncated content marker

    def test_verbose_content(self):
        r = self._reader_with_buf(0x7f00, b"x" * 100)
        rendered = "3, 0x7f00, 100"
        out = _fill_out_args(r, "read", [3, 0x7f00, 100], 100, rendered,
                             verbose=True)
        assert 'x' * 100 in out
        assert "..." not in out


class TestTraceFilter:
    def test_empty_traces_everything(self):
        f = TraceFilter("")
        assert f.matches("read") and f.matches("anything")

    def test_group(self):
        f = TraceFilter("file")
        assert f.matches("openat")
        assert f.matches("close")
        assert not f.matches("read")
        assert not f.matches("connect")

    def test_mixed_groups_and_names(self):
        f = TraceFilter("file,connect")
        assert f.matches("openat")
        assert f.matches("connect")
        assert not f.matches("read")

    def test_explicit_names(self):
        f = TraceFilter("read,write")
        assert f.matches("read") and f.matches("write")
        assert not f.matches("openat")

    def test_exclusion(self):
        f = TraceFilter("!futex")
        assert not f.matches("futex")
        assert f.matches("read")

    def test_exclusion_group(self):
        f = TraceFilter("!memory")
        assert not f.matches("mmap")
        assert not f.matches("futex")
        assert f.matches("read")

    def test_unknown_name_still_matches_nothing(self):
        f = TraceFilter("nosuchcall")
        assert not f.matches("read")

    def test_whitespace_tolerant(self):
        f = TraceFilter(" file , read ")
        assert f.matches("openat") and f.matches("read")

    def test_network_group(self):
        f = TraceFilter("network")
        assert f.matches("epoll_wait")
        assert f.matches("sendto")
        assert not f.matches("openat")


class TestFormatSummary:
    def _ev(self, name, error=None, elapsed=0.0):
        return SyscallEvent(tid=1, nr=0, name=name, ret=0,
                            error=error, elapsed=elapsed)

    def test_header_row(self):
        out = format_summary([])
        assert "syscall" in out.splitlines()[0]
        assert "calls" in out.splitlines()[0]

    def test_totals_row(self):
        out = format_summary([self._ev("read"), self._ev("write")])
        assert "total" in out
        assert "2" in out  # 2 calls

    def test_counts_and_errors(self):
        evs = [self._ev("read"), self._ev("read", error=2),
               self._ev("write", error=13)]
        out = format_summary(evs)
        lines = out.splitlines()
        # read row: 2 calls 1 error; write row: 1 call 1 error
        read_row = next(l for l in lines if l.startswith("read "))
        assert " 2 " in read_row and " 1 " in read_row
        write_row = next(l for l in lines if l.startswith("write "))
        assert "1" in write_row

    def test_sorted_by_total_time(self):
        evs = [self._ev("fast", elapsed=0.001),
               self._ev("slow", elapsed=1.0),
               self._ev("fast", elapsed=0.002)]
        out = format_summary(evs)
        lines = [l for l in out.splitlines() if l.split() and l.split()[0] in
                 ("fast", "slow")]
        assert lines[0].startswith("slow")

    def test_syscall_stat_object(self):
        st = SyscallStat("read")
        assert st.name == "read"
        assert st.calls == 0 and st.errors == 0 and st.total_time == 0.0
