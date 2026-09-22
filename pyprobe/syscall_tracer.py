"""Syscall tracing engine via ptrace (PTRACE_SEIZE + PTRACE_SYSCALL).

Layered design (mirrors stack_dump / native_dump):

* ``collect_syscalls(pid, ...)`` — attach, trace, detach; returns
  ``list[SyscallEvent]``.  Raises ``AttachFailed`` / ``ProcessNotFound`` /
  ``UnsupportedArchitecture``.  No printing.
* ``dump_syscalls(pid, ...)`` — CLI wrapper: collect + stream events
  (or print a ``-c`` summary).

Pure rendering helpers (string escaping/truncation, argument decoding,
``TraceFilter`` parsing, ``format_summary``) live in ``syscall_render.py``
— split out of this module (TODO §8.3) so the engine module imports only
what it needs at the top of the file, with no mid-file import block left
over from the prior two-files-stitched shape.
"""

import ctypes
import os
import platform
import sys
import time

from .colors import red, should_color
from .errors import (
    AttachFailed,
    ProcessNotFound,
    UnsupportedArchitecture,
)
from .syscall_render import (
    TraceFilter,
    _decode_args,
    _fill_out_args,
    format_summary,
)
from .syscall_table import SYSCALL_NAMES, SYSCALL_NRS
from .types import SyscallEvent

PTRACE_GETREGS = 12
PTRACE_SYSCALL = 24
PTRACE_DETACH = 17
PTRACE_SEIZE = 0x4206
PTRACE_INTERRUPT = 0x4207

PTRACE_O_TRACESYSGOOD = 0x1
PTRACE_O_TRACECLONE = 0x8
PTRACE_O_TRACEEXEC = 0x10

PTRACE_EVENT_CLONE = 3
PTRACE_EVENT_EXEC = 4
PTRACE_EVENT_STOP = 128

SIGTRAP = 5
SIGSTOP = 19
SYSCALL_STOP_SIG = SIGTRAP | 0x80  # with TRACESYSGOOD

_WALL = 0x40000000
WNOHANG = 1


class _UserRegs(ctypes.Structure):
    """x86-64 ``struct user_regs_struct`` (sys/user.h)."""

    _fields_ = [
        (name, ctypes.c_ulonglong) for name in (
            "r15", "r14", "r13", "r12", "rbp", "rbx", "r11", "r10", "r9",
            "r8", "rax", "rcx", "rdx", "rsi", "rdi", "orig_rax", "rip",
            "cs", "eflags", "rsp", "ss", "fs_base", "gs_base", "ds", "es",
            "fs", "gs",
        )
    ]


_ARG_REGS = ("rdi", "rsi", "rdx", "r10", "r8", "r9")

_libc = None


def _init_libc():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    return _libc


def _ptrace(request, tid, addr=0, data=0):
    """Thin libc.ptrace wrapper. Returns -1 on failure (see ctypes errno)."""
    libc = _init_libc()
    ctypes.set_errno(0)
    ret = libc.ptrace(ctypes.c_long(request), ctypes.c_int(tid),
                      ctypes.c_void_p(addr), ctypes.c_void_p(data))
    if ret == -1:
        return -ctypes.get_errno()
    return 0


def _waitpid(pid, flags):
    """os.waitpid wrapper (patched in unit tests)."""
    return os.waitpid(pid, flags)


def _list_tids(pid):
    try:
        return sorted(int(x) for x in os.listdir(f"/proc/{pid}/task"))
    except (FileNotFoundError, ProcessLookupError):
        raise ProcessNotFound(pid) from None


class _UncachedReader:
    """Adapter: every read goes straight to process_vm_readv (no cache).

    Syscall tracing is long-running and the target mutates memory between
    observations (e.g. a timespec struct reused by successive sleeps), so
    the RemoteReader snapshot page cache must not be used here.
    """

    def __init__(self, base):
        self._base = base

    def read(self, addr, length):
        return self._base.read_uncached(addr, length)


class SyscallTracer:
    """ptrace syscall tracer for one process (all threads).

    Uses PTRACE_SEIZE (not ATTACH): options are passed with the seize and
    inherited by new threads, and an interrupted tracee can always be
    DETACHed cleanly (ATTACH would leave it stopped if we exit early).
    """

    def __init__(self, pid, *, verbose=False):
        self.pid = pid
        self.verbose = verbose
        self.reader = None  # RemoteReader, created after attach
        self.tids = set()
        self.stash = {}  # tid -> (nr, args, t_entry) pending syscall entry
        self.events = []

    # -- attach / detach ---------------------------------------------------

    def attach(self):
        """SEIZE all threads (+ re-scan for racers), interrupt, restart."""
        from .memory import RemoteReader

        options = (PTRACE_O_TRACESYSGOOD | PTRACE_O_TRACECLONE |
                   PTRACE_O_TRACEEXEC)
        seen = set()
        try:
            pending = _list_tids(self.pid)
        except ProcessNotFound:
            raise
        seized = []
        try:
            while pending:
                for tid in pending:
                    rc = _ptrace(PTRACE_SEIZE, tid, 0, options)
                    if rc != 0:
                        if tid == self.pid or not seized:
                            raise AttachFailed(
                                self.pid, os.strerror(-rc))
                        continue  # thread died mid-scan
                    seen.add(tid)
                    seized.append(tid)
                pending = [t for t in _list_tids(self.pid) if t not in seen]
        except AttachFailed:
            for tid in seized:
                _ptrace(PTRACE_DETACH, tid)
            raise

        # Bring every thread to a known stop, then enter syscall tracing.
        for tid in seen:
            _ptrace(PTRACE_INTERRUPT, tid)
        for tid in seen:
            try:
                _waitpid(tid, 0)
            except OSError:
                pass  # thread died between seize and interrupt
        for tid in seen:
            _ptrace(PTRACE_SYSCALL, tid)
        self.tids = set(seized)
        # wrap the reader: tracing is long-running, so argument reads
        # (paths, timespecs, buffers) must bypass the snapshot page cache
        self.reader = _UncachedReader(RemoteReader(self.pid))

    def detach(self):
        """Idempotent detach: leave every tracee running normally."""
        for tid in list(self.tids):
            _ptrace(PTRACE_INTERRUPT, tid)
            # collect the interrupt stop (bounded retries)
            for _ in range(20):
                try:
                    wpid, status = _waitpid(tid, WNOHANG | _WALL)
                except OSError:
                    break
                if wpid == tid:
                    break
                time.sleep(0.002)
            _ptrace(PTRACE_DETACH, tid)
        self.tids.clear()
        self.stash.clear()

    # -- register access ----------------------------------------------------

    def _getregs(self, tid):
        regs = _UserRegs()
        rc = _ptrace(PTRACE_GETREGS, tid, 0, ctypes.addressof(regs))
        if rc != 0:
            return None
        return regs

    # -- main loop -----------------------------------------------------------

    def run(self, *, trace=None, max_events=None, on_event=None):
        """Trace syscalls until max_events, process exit, or ECHILD.

        Returns the list of captured events (also in ``self.events``).
        ``on_event`` (optional) is called with each SyscallEvent as it is
        captured (streaming CLI output).
        """
        flt = TraceFilter(trace or "")
        while max_events is None or len(self.events) < max_events:
            try:
                wpid, status = _waitpid(-1, _WALL)
            except ChildProcessError:
                break  # all tracees gone
            except InterruptedError:
                continue
            if wpid == 0:
                continue
            if os.WIFSTOPPED(status):
                self._handle_stop(wpid, status, flt, on_event)
                if max_events is not None and len(self.events) >= max_events:
                    break
            elif os.WIFEXITED(status) or os.WIFSIGNALED(status):
                self.tids.discard(wpid)
                if wpid == self.pid:
                    break  # main thread gone: process over
        return self.events

    def _handle_stop(self, tid, status, flt, on_event):
        sig = os.WSTOPSIG(status)
        event = status >> 16

        if sig == SYSCALL_STOP_SIG:
            self._handle_syscall_stop(tid, flt, on_event)
            return

        if event == PTRACE_EVENT_STOP:
            # group-stop (any signal): swallow and keep tracing
            _ptrace(PTRACE_SYSCALL, tid)
            return

        if sig == SIGTRAP:
            if event == PTRACE_EVENT_CLONE:
                # new thread will report a SIGSTOP delivery-stop shortly
                _ptrace(PTRACE_SYSCALL, tid)
            elif event == PTRACE_EVENT_EXEC:
                self._handle_exec(tid, on_event)
                _ptrace(PTRACE_SYSCALL, tid)
            else:
                # other trap (breakpoint etc.): forward
                _ptrace(PTRACE_SYSCALL, tid, 0, sig)
            return

        if tid not in self.tids:
            # SIGSTOP delivery-stop of a fresh TRACECLONE child
            self.tids.add(tid)
            _ptrace(PTRACE_SYSCALL, tid)  # swallow the SIGSTOP
            return

        # signal-delivery-stop: forward the signal
        _ptrace(PTRACE_SYSCALL, tid, 0, sig)

    def _handle_syscall_stop(self, tid, flt, on_event):
        regs = self._getregs(tid)
        if regs is None:
            return
        stash = self.stash.get(tid)
        if stash is None:
            # syscall entry: park nr/args/timestamp
            args = [getattr(regs, r) for r in _ARG_REGS]
            nr = regs.orig_rax & 0xFFFFFFFF  # may be sign-extended -1
            self.stash[tid] = (nr, args, time.monotonic())
        else:
            nr, args, t0 = stash
            del self.stash[tid]
            self._emit(tid, nr, args, regs.rax, time.monotonic() - t0,
                       flt, on_event)
        _ptrace(PTRACE_SYSCALL, tid)

    def _handle_exec(self, tid, on_event):
        """PTRACE_EVENT_EXEC: execve succeeded — emit its exit event."""
        stash = self.stash.pop(tid, None)
        if stash is not None:
            nr, args, t0 = stash
            if nr == SYSCALL_NRS.get("execve"):
                self._emit(tid, nr, args, 0, time.monotonic() - t0,
                           TraceFilter(""), on_event)

    def _emit(self, tid, nr, args, ret, elapsed, flt, on_event):
        name = SYSCALL_NAMES.get(nr, f"sys_{nr}")
        if not flt.matches(name):
            return
        error = None
        if ret > 0xFFFFFFFF00000000:  # -errno range as unsigned
            ret -= 1 << 64
        if -4096 < ret < 0:
            error = -ret
            ret = -1
        rendered = _decode_args(self.reader, name, args)
        if error is None:
            rendered = _fill_out_args(self.reader, name, args, ret,
                                      rendered, verbose=self.verbose)
        ev = SyscallEvent(tid=tid, nr=nr, name=name,
                          args=[a & 0xFFFFFFFFFFFFFFFF for a in args],
                          rendered=rendered, ret=ret, error=error,
                          elapsed=elapsed)
        self.events.append(ev)
        if on_event is not None:
            on_event(ev)


# ---------------------------------------------------------------------------
# Public API (layered like collect_python / dump_python)
# ---------------------------------------------------------------------------

def collect_syscalls(pid, *, trace=None, max_events=None, verbose=False):
    """Attach to ``pid`` and collect syscall events (all threads).

    Returns ``list[SyscallEvent]``.  Raises ``AttachFailed`` /
    ``ProcessNotFound`` / ``UnsupportedArchitecture``.  No printing.
    """
    machine = platform.machine()
    if machine not in ("x86_64", "AMD64"):
        raise UnsupportedArchitecture("syscall tracing", machine)
    tracer = SyscallTracer(pid, verbose=verbose)
    tracer.attach()
    try:
        return tracer.run(trace=trace, max_events=max_events)
    finally:
        tracer.detach()


def dump_syscalls(pid, *, color=None, verbose=False, trace="",
                  max_events=None, summary=False):
    """CLI entry point: collect + stream events (or print a -c summary).

    Returns the exit code.  KeyboardInterrupt detaches cleanly and still
    prints the summary of what was collected so far.
    """
    use_color = should_color(sys.stdout) if color is None else color
    tracer = SyscallTracer(pid, verbose=verbose)
    try:
        tracer.attach()
    except (AttachFailed, ProcessNotFound, UnsupportedArchitecture) as e:
        err_color = should_color(sys.stderr) if color is None else color
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    try:
        events = tracer.run(
            trace=trace, max_events=max_events,
            on_event=(None if summary else
                      lambda ev: print(ev.format(color=use_color))))
    except KeyboardInterrupt:
        events = tracer.events
        print()
    finally:
        tracer.detach()

    if summary:
        print(format_summary(events, color=use_color))
    return 0
