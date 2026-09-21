"""Unit tests for pyprobe/syscall_tracer.py — ptrace engine (no real ptrace).

Monkeypatch stub tests: ``_ptrace`` / ``_waitpid`` / ``_list_tids`` are
patched with scripted stubs.  Split out of ``test_syscall_trace.py``
(TODO §8.3) alongside the source split — the engine now lives in
``syscall_tracer.py`` and the pure helpers in ``syscall_render.py``
(covered by ``test_syscall_render.py``).
"""

import pytest

import pyprobe.syscall_tracer as st_mod
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
