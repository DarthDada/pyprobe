"""Contract tests for kernel/ptrace.py — unified ptrace engine (T1–T17, A2).

No real ptrace: the module's kernel seams (``_ptrace`` / ``_waitpid`` /
``list_tids``) are monkeypatched with scripted stubs, covering the full
state-machine path matrix (contracts.md §10.2-3). Status-word construction
follows wait(2): stop = (sig << 8) | 0x7f, ptrace events in bits 16+,
exit = code << 8, signaled = signal number in low 7 bits.
"""

import os

import pytest

import pyprobe.kernel.ptrace as pt
from pyprobe.errors import (
    AttachFailed,
    ProcessNotFound,
    UnsupportedArchitecture,
)
from pyprobe.kernel.ptrace import (
    ExecEvent,
    Exited,
    PtraceEngine,
    SyscallStop,
    UserRegs,
)


def _syscall_stop_status():
    # syscall stops under PTRACE_O_TRACESYSGOOD report SIGTRAP|0x80 = 133
    return (133 << 8) | 0x7f


def _stop_status(sig):
    return (sig << 8) | 0x7f


def _event_stop_status(sig, event):
    return (event << 16) | (sig << 8) | 0x7f


def _exit_status(code=1):
    return code << 8


def _signal_status(sig):
    return sig  # WIFSIGNALED: nonzero low 7 bits


class StubKernel:
    """Scripted ptrace/waitpid pair; records every ptrace call.

    ``stops``: {tid: [status, ...]} delivered round-robin so all threads
    make progress, matching a real multithreaded tracee. ``errors`` is a
    queue of exceptions raised by waitpid before any stop is delivered.
    ``seize_rc``: {tid: negative errno} to fail specific SEIZE calls.
    """

    def __init__(self, stops=None, errors=(), seize_rc=None):
        self.calls = []  # (request, tid, addr, data)
        self.stops = {t: list(s) for t, s in (stops or {}).items()}
        self.errors = list(errors)
        self.seize_rc = seize_rc or {}
        self._rr = []

    def ptrace(self, request, tid, addr=0, data=0):
        self.calls.append((request, tid, addr, data))
        if request == pt.PTRACE_SEIZE:
            return self.seize_rc.get(tid, 0)
        return 0

    def _next_tid(self):
        if not self._rr:
            self._rr = [t for t in sorted(self.stops) if self.stops[t]]
        while self._rr:
            tid = self._rr.pop(0)
            if self.stops[tid]:
                return tid
        return None

    def waitpid(self, pid, flags):
        if self.errors:
            raise self.errors.pop(0)
        tid = self._next_tid()
        if tid is not None and (pid == -1 or pid == tid):
            status = self.stops[tid][0]
            if not (flags & pt.WNOHANG):  # blocking wait consumes
                self.stops[tid].pop(0)
            return tid, status
        if pid == -1:
            raise ChildProcessError
        raise OSError(10, "No child processes")  # ECHILD

    def requests(self, request):
        return [c for c in self.calls if c[0] == request]


def make_engine(monkeypatch, stub, pid=100, *, tids=(), tid_scans=None,
                **kwargs):
    """Wire a stubbed PtraceEngine; ``tid_scans`` scripts successive
    list_tids results (an entry may also be an exception instance)."""
    monkeypatch.setattr(pt, "_ptrace", stub.ptrace)
    monkeypatch.setattr(pt, "_waitpid", stub.waitpid)
    scans = list(tid_scans) if tid_scans is not None else [list(tids)]

    def list_tids(p):
        r = scans.pop(0) if scans else []
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(pt, "list_tids", list_tids)
    engine = PtraceEngine(pid, **kwargs)
    engine.tids = set(tids)
    return engine


class TestSeize:
    OPTS = pt.PTRACE_O_TRACESYSGOOD | pt.PTRACE_O_TRACECLONE | pt.PTRACE_O_TRACEEXEC

    def test_seize_all_threads_with_options(self, monkeypatch):
        """T1: every thread seized, options passed as the SEIZE data arg."""
        stub = StubKernel()
        engine = make_engine(monkeypatch, stub, tids=[100, 101, 102])
        engine.seize(self.OPTS)
        seizes = stub.requests(pt.PTRACE_SEIZE)
        assert [c[1] for c in seizes] == [100, 101, 102]
        assert all(c[3] == self.OPTS for c in seizes)
        assert engine.tids == {100, 101, 102}

    def test_rescan_picks_up_racer_threads(self, monkeypatch):
        """T1: threads appearing mid-scan are seized by the re-scan loop."""
        stub = StubKernel()
        engine = make_engine(
            monkeypatch, stub, tid_scans=[[100], [100, 101], [100, 101]])
        engine.seize(0)
        seizes = stub.requests(pt.PTRACE_SEIZE)
        assert [c[1] for c in seizes] == [100, 101]
        assert engine.tids == {100, 101}

    def test_main_thread_failure_raises_attach_failed(self, monkeypatch):
        """T3: failing to seize the main thread is a hard AttachFailed."""
        stub = StubKernel(seize_rc={100: -1})  # EPERM
        engine = make_engine(monkeypatch, stub, tids=[100])
        with pytest.raises(AttachFailed) as ei:
            engine.seize(0)
        assert ei.value.pid == 100
        assert "Operation not permitted" in str(ei.value)

    def test_racer_thread_death_skipped(self, monkeypatch):
        """T3: a non-main thread dying mid-scan is skipped, not fatal."""
        stub = StubKernel(seize_rc={101: -3})  # ESRCH
        engine = make_engine(monkeypatch, stub, tids=[100, 101])
        engine.seize(0)
        assert engine.tids == {100}

    def test_rollback_on_attach_failed(self, monkeypatch):
        """T2: AttachFailed after partial progress detaches seized threads."""
        stub = StubKernel(seize_rc={101: -13})
        engine = make_engine(monkeypatch, stub, pid=101, tids=[100, 101])
        with pytest.raises(AttachFailed):
            engine.seize(0)
        detaches = stub.requests(pt.PTRACE_DETACH)
        assert [c[1] for c in detaches] == [100]
        assert engine.tids == set()

    def test_rollback_on_any_exception(self, monkeypatch):
        """T2 (A7 P1-4): rollback covers *any* exception — here the re-scan
        raising ProcessNotFound (target exits mid-attach). The old tree only
        caught AttachFailed and leaked the seized thread."""
        stub = StubKernel()
        engine = make_engine(
            monkeypatch, stub, tid_scans=[[100], ProcessNotFound(100)])
        with pytest.raises(ProcessNotFound):
            engine.seize(0)
        detaches = stub.requests(pt.PTRACE_DETACH)
        assert [c[1] for c in detaches] == [100]
        assert engine.tids == set()

    def test_missing_process_propagates_without_ptrace(self, monkeypatch):
        stub = StubKernel()
        engine = make_engine(
            monkeypatch, stub, pid=9999, tid_scans=[ProcessNotFound(9999)])
        with pytest.raises(ProcessNotFound):
            engine.seize(0)
        assert stub.calls == []


class TestStopAndRestart:
    def test_stop_all_interrupts_and_collects(self, monkeypatch):
        """T4: INTERRUPT per thread + blocking wait to collect the stop."""
        stops = {t: [_stop_status(pt.SIGTRAP)] for t in (100, 101, 102)}
        stub = StubKernel(stops=stops)
        engine = make_engine(monkeypatch, stub, tids=[100, 101, 102])
        engine.stop_all()
        interrupts = stub.requests(pt.PTRACE_INTERRUPT)
        assert {c[1] for c in interrupts} == {100, 101, 102}
        assert all(not stub.stops[t] for t in (100, 101, 102))

    def test_stop_all_tolerates_dead_thread(self, monkeypatch):
        """T4: a thread dying between seize and interrupt is skipped."""
        stub = StubKernel(stops={}, errors=[OSError(3, "No such process")])
        engine = make_engine(monkeypatch, stub, tids=[100])
        engine.stop_all()  # must not raise

    def test_restart_all_uses_restart_op(self, monkeypatch):
        stub = StubKernel()
        engine = make_engine(monkeypatch, stub, tids=[100, 101])
        engine.restart_all()
        restarts = stub.requests(pt.PTRACE_SYSCALL)
        assert {c[1] for c in restarts} == {100, 101}
        assert all(c[3] == 0 for c in restarts)

    def test_resume_forwards_signal(self, monkeypatch):
        stub = StubKernel()
        engine = make_engine(monkeypatch, stub, tids=[100])
        engine.resume(100, 9)
        assert (pt.PTRACE_SYSCALL, 100, 0, 9) in stub.calls


class TestWaitEvent:
    def test_syscall_stop_returned_without_resume(self, monkeypatch):
        """T5: a syscall-stop is handed to the consumer, which owns resume."""
        stub = StubKernel(stops={100: [_syscall_stop_status()]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        ev = engine.wait_event()
        assert ev == SyscallStop(100)
        assert stub.requests(pt.PTRACE_SYSCALL) == []

    def test_group_stop_swallowed(self, monkeypatch):
        """T6: PTRACE_EVENT_STOP group-stop restarts without forwarding."""
        stub = StubKernel(stops={
            100: [_event_stop_status(pt.SIGSTOP, pt.PTRACE_EVENT_STOP),
                  _exit_status(0)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        ev = engine.wait_event()
        assert isinstance(ev, Exited)
        restarts = stub.requests(pt.PTRACE_SYSCALL)
        assert [c[3] for c in restarts] == [0]  # restarted, signal NOT forwarded

    def test_real_signal_forwarded(self, monkeypatch):
        """T7: signal-delivery-stop forwards the signal via restart data."""
        stub = StubKernel(stops={100: [_stop_status(10), _exit_status(0)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        ev = engine.wait_event()
        assert isinstance(ev, Exited)
        assert (pt.PTRACE_SYSCALL, 100, 0, 10) in stub.calls

    def test_fresh_clone_child_sigstop_swallowed(self, monkeypatch):
        """T8: SIGSTOP delivery-stop of a TRACECLONE child is swallowed
        and the new tid registered. Events are returned one stop at a time
        (immediate semantics, T12), so the sequence spans two calls."""
        stub = StubKernel(stops={100: [_syscall_stop_status(), _exit_status(0)],
                                 200: [_stop_status(pt.SIGSTOP)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        assert engine.wait_event() == SyscallStop(100)
        engine.resume(100)  # consumer restarts the syscall-stopped thread
        ev = engine.wait_event()
        assert isinstance(ev, Exited)
        assert 200 in engine.tids
        assert (pt.PTRACE_SYSCALL, 200, 0, 0) in stub.calls

    def test_non_event_sigtrap_forwarded(self, monkeypatch):
        """T9: a plain SIGTRAP (breakpoint etc.) is forwarded, not swallowed."""
        stub = StubKernel(stops={100: [_stop_status(pt.SIGTRAP),
                                       _exit_status(0)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        ev = engine.wait_event()
        assert isinstance(ev, Exited)
        assert (pt.PTRACE_SYSCALL, 100, 0, pt.SIGTRAP) in stub.calls

    def test_exec_event_returned_without_resume(self, monkeypatch):
        """T10: PTRACE_EVENT_EXEC is handed to the consumer (owns resume)."""
        stub = StubKernel(stops={
            100: [_event_stop_status(pt.SIGTRAP, pt.PTRACE_EVENT_EXEC)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        ev = engine.wait_event()
        assert ev == ExecEvent(100)
        assert stub.requests(pt.PTRACE_SYSCALL) == []

    def test_clone_restarts_parent_without_event(self, monkeypatch):
        """T11: PTRACE_EVENT_CLONE restarts the parent; the child's own
        SIGSTOP arrives separately (covered by T8)."""
        stub = StubKernel(stops={
            100: [_event_stop_status(pt.SIGTRAP, pt.PTRACE_EVENT_CLONE),
                  _exit_status(0)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        ev = engine.wait_event()
        assert isinstance(ev, Exited)
        assert (pt.PTRACE_SYSCALL, 100, 0, 0) in stub.calls

    def test_echild_returns_none(self, monkeypatch):
        """T12: no tracees left -> None (consumer ends its loop)."""
        stub = StubKernel(stops={})
        engine = make_engine(monkeypatch, stub, tids=[100])
        assert engine.wait_event() is None

    def test_interrupted_wait_retried(self, monkeypatch):
        """T12: InterruptedError (EINTR waitpid) is retried transparently."""
        stub = StubKernel(stops={100: [_syscall_stop_status()]},
                          errors=[InterruptedError])
        engine = make_engine(monkeypatch, stub, tids=[100])
        assert engine.wait_event() == SyscallStop(100)

    def test_exited_reports_code_and_deregisters(self, monkeypatch):
        """T12: exit status decoded; tid removed from the engine set."""
        stub = StubKernel(stops={100: [_exit_status(3)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        ev = engine.wait_event()
        assert ev == Exited(100, 3, False)
        assert 100 not in engine.tids

    def test_signaled_reports_signal(self, monkeypatch):
        stub = StubKernel(stops={100: [_signal_status(9)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        ev = engine.wait_event()
        assert ev == Exited(100, 9, True)


class TestGetregs:
    def test_getregs_returns_user_regs(self, monkeypatch):
        """T15/T17: x86-64 layout fields are addressable by name."""
        stub = StubKernel()
        engine = make_engine(monkeypatch, stub, tids=[100])
        regs = engine.getregs(100)
        assert isinstance(regs, UserRegs)
        for field in ("rdi", "rsi", "rdx", "r10", "r8", "r9", "rax",
                      "orig_rax", "rip", "rsp"):
            getattr(regs, field)  # AttributeError if the layout is wrong
        getregs = stub.requests(pt.PTRACE_GETREGS)
        assert len(getregs) == 1 and getregs[0][1] == 100

    def test_getregs_failure_returns_none(self, monkeypatch):
        """T15: GETREGS failure (thread died) is None, not an exception."""
        stub = StubKernel()

        def failing(request, tid, addr=0, data=0):
            stub.calls.append((request, tid, addr, data))
            return -3  # ESRCH

        monkeypatch.setattr(pt, "_ptrace", failing)
        monkeypatch.setattr(pt, "_waitpid", stub.waitpid)
        engine = PtraceEngine(100)
        engine.tids = {100}
        assert engine.getregs(100) is None


class TestDetach:
    def test_detach_interrupts_and_detaches_all(self, monkeypatch):
        """T14: INTERRUPT + bounded stop-collect + DETACH per thread."""
        stops = {t: [_stop_status(pt.SIGTRAP)] for t in (100, 101)}
        stub = StubKernel(stops=stops)
        engine = make_engine(monkeypatch, stub, tids=[100, 101])
        engine.detach()
        interrupts = stub.requests(pt.PTRACE_INTERRUPT)
        assert {c[1] for c in interrupts} == {100, 101}
        detaches = stub.requests(pt.PTRACE_DETACH)
        assert {c[1] for c in detaches} == {100, 101}
        assert engine.tids == set()

    def test_detach_idempotent(self, monkeypatch):
        """T14: a second detach is a no-op."""
        stub = StubKernel(stops={100: [_stop_status(pt.SIGTRAP)]})
        engine = make_engine(monkeypatch, stub, tids=[100])
        engine.detach()
        engine.detach()
        assert len(stub.requests(pt.PTRACE_DETACH)) == 1

    def test_detach_empty_is_noop(self, monkeypatch):
        stub = StubKernel()
        engine = make_engine(monkeypatch, stub, tids=[])
        engine.detach()
        assert stub.calls == []


class TestArchCheck:
    def test_unsupported_arch_raises_on_construction(self, monkeypatch):
        """T16 (A7 P0-2): the check lives in the engine, so *every* entry
        path (library or CLI) fails with UnsupportedArchitecture instead of
        reading registers with the wrong layout."""
        monkeypatch.setattr(pt.platform, "machine", lambda: "aarch64")
        with pytest.raises(UnsupportedArchitecture) as ei:
            PtraceEngine(100, feature="syscall tracing")
        assert ei.value.feature == "syscall tracing"
        assert ei.value.arch == "aarch64"

    @pytest.mark.parametrize("machine", ["x86_64", "AMD64"])
    def test_supported_arches(self, monkeypatch, machine):
        monkeypatch.setattr(pt.platform, "machine", lambda: machine)
        PtraceEngine(100)  # must not raise

    def test_real_arch_is_supported(self):
        # spec-oracle: this test suite only runs meaningfully on x86-64;
        # guard the development machine assumption explicitly.
        assert os.uname().machine == "x86_64"
        PtraceEngine(100)
