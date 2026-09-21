"""Unit tests for the pure helpers of pyprobe/syscall_trace.py.

No ptrace involved: a FakeReader (tests/helpers.py) supplies remote memory.
"""

import struct

import pytest

from pyprobe.syscall_trace import (
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


# ---------------------------------------------------------------------------
# ptrace engine (monkeypatch stub tests — no real ptrace)
# ---------------------------------------------------------------------------

import pyprobe.syscall_trace as st_mod
from pyprobe.errors import (
    AttachFailed, ProcessNotFound, UnsupportedArchitecture,
)


class StubPtrace:
    """Records _ptrace calls; each thread follows a scripted stop sequence.

    ``stops``: {tid: [status, ...]} — statuses delivered by _waitpid in
    round-robin tid order (all threads make progress), matching how a
    real multithreaded tracee interleaves.
    """

    def __init__(self, tids, stops, regs=None):
        self.tids = tids
        self.stops = {t: list(s) for t, s in stops.items()}
        self.calls = []          # (request, tid, addr, data)
        self.regs = regs or {}   # tid -> _UserRegs-like
        self.exit_status = {}    # tid -> exit status (0x0 = running)
        self._rr = []            # round-robin cursor queue

    def ptrace(self, request, tid, addr=0, data=0):
        self.calls.append((request, tid, addr, data))
        return 0

    def _next_tid(self):
        """Round-robin over tids that still have pending stops."""
        if not self._rr:
            self._rr = [t for t in sorted(self.stops) if self.stops[t]]
        while self._rr:
            tid = self._rr.pop(0)
            if self.stops[tid]:
                return tid
        return None

    def waitpid(self, pid, flags):
        tid = self._next_tid()
        if tid is not None and (pid == -1 or pid == tid):
            status = self.stops[tid][0]
            if not (flags & 1):  # blocking wait consumes
                self.stops[tid].pop(0)
            return tid, status
        if pid == -1:
            raise ChildProcessError
        raise OSError(10, "no child")  # ECHILD


def _syscall_entry_status():
    # waitpid stop status: (sig << 8) | 0x7f; syscall stops under
    # PTRACE_O_TRACESYSGOOD report SIGTRAP|0x80 = 133
    return (133 << 8) | 0x7f


def _stop_status(sig):
    # signal-delivery/group stop
    return (sig << 8) | 0x7f


def _event_stop_status(sig, event):
    # ptrace event stop: event in bits 16+
    return (event << 16) | (sig << 8) | 0x7f


def _exit_status():
    # WIFEXITED: code in bits 8..15
    return 0 << 8


class _FakeRegs:
    def __init__(self, orig_rax, rax, args):
        self.orig_rax = orig_rax
        self.rax = rax
        for i, a in enumerate(args):
            setattr(self, st_mod._ARG_REGS[i], a)


def make_tracer(monkeypatch, stub, pid=100):
    monkeypatch.setattr(st_mod, "_ptrace", stub.ptrace)
    monkeypatch.setattr(st_mod, "_waitpid", stub.waitpid)
    monkeypatch.setattr(st_mod, "_list_tids", lambda p: list(stub.tids))

    tracer = st_mod.SyscallTracer(pid)
    tracer.reader = None  # decode path reads via reader; paths absent here
    tracer.tids = set(stub.tids)
    return tracer


class TestAttach:
    def test_seize_all_threads(self, monkeypatch):
        stub = StubPtrace(tids=[100, 101, 102], stops={})
        monkeypatch.setattr(st_mod, "_ptrace", stub.ptrace)
        monkeypatch.setattr(st_mod, "_waitpid", stub.waitpid)
        monkeypatch.setattr(st_mod, "_list_tids", lambda p: [100, 101, 102])
        # give each thread one interrupt stop to collect
        for tid in (100, 101, 102):
            stub.stops[tid] = [_stop_status(5)]  # SIGTRAP after INTERRUPT
        tracer = st_mod.SyscallTracer(100)
        tracer.attach()
        seizes = [c for c in stub.calls if c[0] == st_mod.PTRACE_SEIZE]
        assert [c[1] for c in seizes] == [100, 101, 102]
        # options passed with seize: TRACESYSGOOD|TRACECLONE|TRACEEXEC
        opts = st_mod.PTRACE_O_TRACESYSGOOD | \
            st_mod.PTRACE_O_TRACECLONE | st_mod.PTRACE_O_TRACEEXEC
        assert all(c[3] == opts for c in seizes)
        # then interrupted and put into syscall tracing
        intr = [c for c in stub.calls if c[0] == st_mod.PTRACE_INTERRUPT]
        assert len(intr) >= 3
        syscalls = [c for c in stub.calls
                    if c[0] == st_mod.PTRACE_SYSCALL]
        assert len(syscalls) == 3

    def test_attach_failure_rolls_back(self, monkeypatch):
        stub = StubPtrace(tids=[100], stops={})
        monkeypatch.setattr(st_mod, "_list_tids", lambda p: [100])

        def failing_ptrace(request, tid, addr=0, data=0):
            stub.calls.append((request, tid, addr, data))
            return -13 if request == st_mod.PTRACE_SEIZE else 0

        monkeypatch.setattr(st_mod, "_ptrace", failing_ptrace)
        monkeypatch.setattr(st_mod, "_waitpid", stub.waitpid)
        tracer = st_mod.SyscallTracer(100)
        with pytest.raises(AttachFailed):
            tracer.attach()

    def test_attach_missing_process(self, monkeypatch):
        def no_proc(pid):
            raise ProcessNotFound(pid)
        monkeypatch.setattr(st_mod, "_list_tids", no_proc)
        tracer = st_mod.SyscallTracer(9999)
        with pytest.raises(ProcessNotFound):
            tracer.attach()


class TestRunStateMachine:
    def test_entry_exit_pairing_emits_event(self, monkeypatch):
        # openat(257): entry then exit with fd 3
        regs_entry = _FakeRegs(257, 0xFFFFFFFFFFFFFFFF, [100, 200, 0, 0, 0, 0])
        regs_exit = _FakeRegs(257, 3, [0] * 6)
        stub = StubPtrace(
            tids=[100],
            stops={100: [_syscall_entry_status(),
                         _syscall_entry_status(),
                         _exit_status()]},
            regs={100: [regs_entry, regs_exit]},
        )
        tracer = make_tracer(monkeypatch, stub)
        orig_getregs = tracer._getregs

        seq = iter([regs_entry, regs_exit])
        monkeypatch.setattr(tracer, "_getregs", lambda tid: next(seq, None))
        events = tracer.run(max_events=1)
        assert len(events) == 1
        ev = events[0]
        assert ev.nr == 257 and ev.name == "openat"
        assert ev.ret == 3 and ev.error is None
        assert ev.tid == 100
        assert ev.elapsed >= 0.0

    def test_error_return_becomes_errno(self, monkeypatch):
        regs_entry = _FakeRegs(257, 0, [100, 200, 0, 0, 0, 0])
        # -ENOENT as unsigned 64-bit
        regs_exit = _FakeRegs(257, (1 << 64) - 2, [0] * 6)
        stub = StubPtrace(
            tids=[100],
            stops={100: [_syscall_entry_status(),
                         _syscall_entry_status(),
                         _exit_status()]},
        )
        tracer = make_tracer(monkeypatch, stub)
        seq = iter([regs_entry, regs_exit])
        monkeypatch.setattr(tracer, "_getregs", lambda tid: next(seq, None))
        events = tracer.run(max_events=1)
        ev = events[0]
        assert ev.error == 2
        assert ev.ret == -1

    def test_filter_drops_non_matching(self, monkeypatch):
        regs_entry = _FakeRegs(0, 0, [1, 2, 3, 0, 0, 0])   # read
        regs_exit = _FakeRegs(0, 3, [0] * 6)
        stub = StubPtrace(
            tids=[100],
            stops={100: [_syscall_entry_status(),
                         _syscall_entry_status(),
                         _exit_status()]},
        )
        tracer = make_tracer(monkeypatch, stub)
        seq = iter([regs_entry, regs_exit])
        monkeypatch.setattr(tracer, "_getregs", lambda tid: next(seq, None))
        events = tracer.run(trace="file", max_events=None)
        # read is not in file group; loop should end via ECHILD after exit
        stub.exit_status[100] = _exit_status()
        assert all(e.name not in ("read",) for e in events)

    def test_multithread_events(self, monkeypatch):
        # two tids each doing one write(1): entry+exit each
        stub = StubPtrace(
            tids=[100, 101],
            stops={100: [_syscall_entry_status(),
                         _syscall_entry_status(),
                         _exit_status()],
                   101: [_syscall_entry_status(),
                         _syscall_entry_status(),
                         _exit_status()]},
        )
        tracer = make_tracer(monkeypatch, stub)
        regs = {
            100: iter([_FakeRegs(1, 0, [1, 10, 5, 0, 0, 0]),
                       _FakeRegs(1, 5, [0] * 6)]),
            101: iter([_FakeRegs(1, 0, [1, 20, 5, 0, 0, 0]),
                       _FakeRegs(1, 5, [0] * 6)]),
        }
        monkeypatch.setattr(
            tracer, "_getregs", lambda tid: next(regs[tid], None))
        events = tracer.run(max_events=2)
        assert {e.tid for e in events} == {100, 101}
        assert all(e.name == "write" for e in events)

    def test_main_thread_exit_ends_loop(self, monkeypatch):
        stub = StubPtrace(tids=[100], stops={100: [_exit_status()]})
        stub.exit_status[100] = _exit_status()
        tracer = make_tracer(monkeypatch, stub)
        events = tracer.run(max_events=None)
        assert events == []
        assert 100 not in tracer.tids

    def test_max_events_stops_loop(self, monkeypatch):
        stub = StubPtrace(
            tids=[100],
            stops={100: [_syscall_entry_status()] * 100},
        )
        tracer = make_tracer(monkeypatch, stub)
        # each entry immediately followed by exit via alternating regs
        entries = iter([_FakeRegs(0, 0, [0] * 6),
                        _FakeRegs(0, 0, [0] * 6), _FakeRegs(0, 0, [0] * 6),
                        _FakeRegs(0, 0, [0] * 6), _FakeRegs(0, 0, [0] * 6),
                        _FakeRegs(0, 0, [0] * 6)])
        monkeypatch.setattr(tracer, "_getregs",
                            lambda tid: next(entries, None))
        events = tracer.run(max_events=3)
        assert len(events) == 3


class TestSignalForwarding:
    def test_real_signal_forwarded(self, monkeypatch):
        stub = StubPtrace(
            tids=[100],
            stops={100: [_stop_status(10) ] * 1 + [_exit_status()]},
        )
        stub.exit_status[100] = _exit_status()
        tracer = make_tracer(monkeypatch, stub)
        monkeypatch.setattr(tracer, "_getregs", lambda tid: None)
        tracer.run()
        forwards = [c for c in stub.calls
                    if c[0] == st_mod.PTRACE_SYSCALL and c[3] == 10]
        assert forwards, "SIGUSR1 should be forwarded via PTRACE_SYSCALL data"

    def test_sigstop_new_thread_swallowed(self, monkeypatch):
        # main thread keeps tracing; unknown tid 200 hits a SIGSTOP
        # delivery-stop (fresh TRACECLONE child) which must be swallowed
        # and the thread registered.  No exit follows, so the loop ends
        # via ECHILD once both scripts are consumed.
        stub = StubPtrace(
            tids=[100, 200],
            stops={100: [_syscall_entry_status()],
                   200: [_stop_status(19)]},
        )
        tracer = make_tracer(monkeypatch, stub)
        tracer.tids = {100}
        regs = iter([_FakeRegs(-1, 0, [0] * 6)])  # aborted syscall (no nr)
        monkeypatch.setattr(tracer, "_getregs", lambda tid: next(regs, None))
        tracer.run()
        swallows = [c for c in stub.calls
                    if c[0] == st_mod.PTRACE_SYSCALL and c[3] == 0
                    and c[1] == 200]
        assert swallows
        assert 200 in tracer.tids

    def test_group_stop_swallowed(self, monkeypatch):
        # PTRACE_EVENT_STOP group-stop
        group_stop = _event_stop_status(19, st_mod.PTRACE_EVENT_STOP)
        stub = StubPtrace(
            tids=[100], stops={100: [group_stop, _exit_status()]})
        stub.exit_status[100] = _exit_status()
        tracer = make_tracer(monkeypatch, stub)
        monkeypatch.setattr(tracer, "_getregs", lambda tid: None)
        tracer.run()
        forwards = [c for c in stub.calls
                    if c[0] == st_mod.PTRACE_SYSCALL and c[3] == 19]
        assert not forwards, "group-stop SIGSTOP must not be forwarded"


class TestDetach:
    def test_detach_interrupts_and_detaches_all(self, monkeypatch):
        stub = StubPtrace(tids=[100, 101], stops={100: [], 101: []})
        tracer = make_tracer(monkeypatch, stub)
        tracer.detach()
        intr = [c for c in stub.calls if c[0] == st_mod.PTRACE_INTERRUPT]
        det = [c for c in stub.calls if c[0] == st_mod.PTRACE_DETACH]
        assert {c[1] for c in det} == {100, 101}
        assert tracer.tids == set()
        # idempotent
        tracer.detach()
        det2 = [c for c in stub.calls if c[0] == st_mod.PTRACE_DETACH]
        assert len(det2) == 2


class TestCollectSyscalls:
    def test_unsupported_arch(self, monkeypatch):
        monkeypatch.setattr(st_mod.platform, "machine",
                            lambda: "aarch64")
        with pytest.raises(UnsupportedArchitecture):
            st_mod.collect_syscalls(123)
