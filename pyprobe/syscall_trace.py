"""Syscall tracing via ptrace (PTRACE_SEIZE + PTRACE_SYSCALL), strace-style.

Layered design (mirrors stack_dump / native_dump):

* ``collect_syscalls(pid, ...)`` — attach, trace, detach; returns
  ``list[SyscallEvent]``.  Raises ``AttachFailed`` / ``ProcessNotFound``.
* ``format_summary(events)`` — strace -c style statistics table.
* ``dump_syscalls(pid, ...)`` — CLI wrapper: collect + print/summary.

Pure helpers (no ptrace): string escaping/truncation, argument decoding
(``_decode_args`` / ``_fill_out_args``), ``TraceFilter`` parsing.
"""

import re
import struct

from .syscall_table import (
    SYSCALL_NAMES, SYSCALL_NRS, DECODE, TRACE_GROUPS,
    OPEN_FLAGS, MAP_FLAGS, PROT_FLAGS,
)
from .types import SyscallEvent

# ---------------------------------------------------------------------------
# String rendering helpers (strace style)
# ---------------------------------------------------------------------------

# strace default: print 32 chars of a string
STR_MAX = 32
_PRINTABLE = re.compile(rb"^[\x20-\x7e]*$")

_AT_FDCWD = 0xFFFFFFFFFFFFFF10  # (int) AT_FDCWD on x86-64


def escape_bytes(data: bytes) -> str:
    """Render bytes strace-style: printable ASCII kept, rest as \\NNN octal."""
    out = []
    for b in data:
        if 0x20 <= b <= 0x7E:
            ch = chr(b)
            if ch in ('"', "\\"):
                out.append("\\" + ch)
            else:
                out.append(ch)
        else:
            out.append("\\%03o" % b)
    return "".join(out)


def truncate_escaped(escaped: str, max_chars: int = STR_MAX) -> str:
    """Truncate an escaped string to ~``max_chars`` visible chars + ``...``.

    Operates on the escaped form so output length is bounded; strace shows
    ``"very long stri..."``.
    """
    if len(escaped) <= max_chars:
        return escaped
    return escaped[:max_chars] + '...'


def render_str_arg(data: bytes, verbose: bool = False) -> str:
    """Bytes -> quoted strace string, truncated unless verbose."""
    escaped = escape_bytes(data)
    if not verbose:
        escaped = truncate_escaped(escaped)
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# Argument decoding (pure; reader injected — FakeReader in unit tests)
# ---------------------------------------------------------------------------

def _read_cstr(reader, addr: int, max_len: int = 4096) -> str:
    """Read a NUL-terminated string from the target at *addr*.

    Returns the raw hex address when nothing at all is readable (strace
    prints raw addresses for unreadable memory).
    """
    if not addr:
        return "NULL"
    data = b""
    chunk = 256
    while len(data) < max_len:
        part = reader.read(addr + len(data), chunk)
        if part is None or part == b"":
            if not data:
                # probe how much is actually readable (short regions)
                part = _read_available(reader, addr + len(data), chunk)
                if not part:
                    return f"0x{addr:x}"
            else:
                break
        data += part
        nul = data.find(b"\x00")
        if nul != -1:
            return render_str_arg(data[:nul])
        if len(part) < chunk:
            break
    return render_str_arg(data[:max_len])


def _read_available(reader, addr: int, want: int) -> bytes:
    """Read up to *want* bytes at *addr*, tolerating short/edge mappings.

    Binary-searches the largest request length the reader can fully
    satisfy, so a mapping that cannot satisfy the full length still
    yields whatever it has.
    """
    data = reader.read(addr, want)
    if data and len(data) == want:
        return data
    best = b""
    lo, hi = 1, want
    while lo <= hi:
        mid = (lo + hi) // 2
        part = reader.read(addr, mid)
        if part and len(part) == mid:
            best = part
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _read_timespec(reader, addr: int) -> str:
    """Read struct timespec {time_t tv_sec; long tv_nsec;} at *addr*."""
    if not addr:
        return "NULL"
    data = reader.read(addr, 16)
    if data is None or len(data) < 16:
        return f"0x{addr:x}"
    sec, nsec = struct.unpack("<qq", data)
    return f"{{{sec}, {nsec}}}"


def _decode_flags(value: int, table: dict) -> str:
    """OR-decode *value* against a name table (e.g. OPEN_FLAGS)."""
    if value == 0 and 0 in table:
        return table[0]
    names = []
    rest = value
    for bit, name in sorted(table.items()):
        if bit == 0:
            continue
        if bit & value == bit:
            names.append(name)
            rest &= ~bit
    if rest:
        names.append(f"0x{rest:x}")
    return "|".join(names) if names else "0"


def _render_scalar(kind: str, value: int) -> str:
    if kind == "fd" or kind == "dirfd":
        return "AT_FDCWD" if value == _AT_FDCWD else str(value)
    if kind == "mode":
        return oct(value & 0o7777)
    if kind == "signal":
        return f"SIG{value}"
    if kind == "prot":
        return _decode_flags(value, PROT_FLAGS)
    if kind == "map_flags":
        return _decode_flags(value, MAP_FLAGS)
    if kind == "open_flags":
        return _decode_flags(value, OPEN_FLAGS)
    # int-like kinds and unknown kinds
    if -(2 ** 31) <= value <= 2 ** 31 - 1:
        return str(value)
    return hex(value)


def _decode_args(reader, name: str, args) -> str:
    """Render the six syscall args at entry time (no exit data yet).

    ``buf_in``/``buf_out`` args are rendered as their address; exit-time
    content is filled by :func:`_fill_out_args`.
    """
    kinds = DECODE.get(name)
    parts = []
    for i in range(6):
        if i >= len(args):
            break
        value = args[i]
        kind = kinds[i] if kinds and i < len(kinds) else None
        if kind == "path" and reader is not None:
            parts.append(_read_cstr(reader, value))
        elif kind == "path":
            parts.append(f"0x{value:x}" if value else "NULL")
        elif kind in ("buf_in", "buf_out"):
            parts.append(f"0x{value:x}" if value else "NULL")
        elif kind in ("timespec", "timespec_out") and reader is not None:
            parts.append(_read_timespec(reader, value))
        elif kind == "timespec" or kind == "timespec_out":
            parts.append(f"0x{value:x}" if value else "NULL")
        elif kind == "sockaddr" or kind == "msghdr":
            parts.append(f"0x{value:x}" if value else "NULL")
        elif kind is None:
            parts.append(hex(value))
        else:
            parts.append(_render_scalar(kind, value))
    return ", ".join(parts)


def _fill_out_args(reader, name: str, args, ret: int,
                   rendered: str, verbose: bool = False) -> str:
    """Post-exit pass: splice buffer contents into the rendered args.

    For ``read(2)``-style calls the buffer is filled by the kernel on
    success, so strace renders ``0x7f../"content..."``; the length is the
    return value.  ``write(2)``-style ``buf_in`` buffers are readable at
    entry but we read them at exit (registers are clobbered anyway).
    """
    kinds = DECODE.get(name)
    if not kinds:
        return rendered
    if reader is None or not any(
            k in ("buf_in", "buf_out") for k in kinds):
        return rendered

    parts = rendered.split(", ")
    # read(fd, buf, count) -> show min(ret, 32) bytes; on error nothing
    # was written.  For write-like calls ret also equals bytes moved.
    length = ret if ret > 0 else 32
    for i, kind in enumerate(kinds):
        if i >= len(args) or i >= len(parts):
            break
        if kind not in ("buf_in", "buf_out"):
            continue
        addr = args[i]
        if not addr:
            continue
        if ret < 0 and kind == "buf_out":
            continue  # kernel wrote nothing
        data = reader.read(addr, min(length, 4096))
        if data is None or not data:
            continue
        parts[i] = f"0x{addr:x}/{render_str_arg(data, verbose)}"
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# TraceFilter: -e trace=<spec> parsing
# ---------------------------------------------------------------------------

class TraceFilter:
    """Compiled ``-e trace=`` expression.

    ``None`` spec -> trace everything.  Otherwise the spec is a comma
    list whose items are either group names (``file``, ``network``, ...)
    or syscall names; ``!`` prefix excludes (keeps only listed names).
    """

    def __init__(self, spec: str = ""):
        self.spec = spec
        self.names = None  # None = no filtering
        if not spec:
            return
        include = set()
        exclude = set()
        for item in spec.split(","):
            item = item.strip()
            if not item:
                continue
            if item.startswith("!"):
                target = exclude
                item = item[1:].strip()
            else:
                target = include
            if item in TRACE_GROUPS:
                target |= TRACE_GROUPS[item]
            else:
                target.add(item)
        if exclude and not include:
            # "!a,b" — everything except the excluded
            self.names = None if not exclude else exclude
            self._exclude_mode = True
        else:
            self.names = include | exclude if exclude else include
            self._exclude_mode = False

    def matches(self, name: str) -> bool:
        if self.names is None:
            return True
        if self._exclude_mode:
            return name not in self.names
        return name in self.names


# ---------------------------------------------------------------------------
# Summary (strace -c style)
# ---------------------------------------------------------------------------

class SyscallStat:
    """Accumulated stats for one syscall name."""

    def __init__(self, name: str):
        self.name = name
        self.calls = 0
        self.errors = 0
        self.total_time = 0.0  # seconds


def format_summary(events, *, color: bool = False) -> str:
    """Render a strace -c style summary table from collected events."""
    stats = {}
    total_calls = 0
    total_errors = 0
    total_time = 0.0
    for ev in events:
        st = stats.setdefault(ev.name, SyscallStat(ev.name))
        st.calls += 1
        total_calls += 1
        if ev.error is not None:
            st.errors += 1
            total_errors += 1
        st.total_time += ev.elapsed
        total_time += ev.elapsed

    rows = sorted(stats.values(), key=lambda s: -s.total_time)
    lines = [
        "%-20s %10s %10s %11s %11s %16s" % (
            "syscall", "calls", "errors", "total", "total/s", "per-call"),
        "-" * 82,
    ]
    for st in rows:
        per_call = st.total_time / st.calls if st.calls else 0.0
        lines.append("%-20s %10d %10d %10.6f %10.6f %13.6f" % (
            st.name, st.calls, st.errors,
            st.total_time, st.total_time, per_call))
    lines.append("-" * 82)
    lines.append("%-20s %10d %10d %10.6f %10.6f" % (
        "total", total_calls, total_errors, total_time, total_time))
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# ptrace engine (x86-64 only)
# ---------------------------------------------------------------------------

import ctypes
import os
import platform
import sys
import time

from .errors import AttachFailed, ProcessNotFound, UnsupportedArchitecture
from .colors import red, should_color

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
        return sorted(int(x) for x in os.listdir("/proc/%d/task" % pid))
    except (FileNotFoundError, ProcessLookupError):
        raise ProcessNotFound(pid)


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
        self.tids = set(seen)
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
        name = SYSCALL_NAMES.get(nr, "sys_%d" % nr)
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
        print(red("[!] %s" % e, err_color), file=sys.stderr)
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
