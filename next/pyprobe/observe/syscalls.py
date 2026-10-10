"""Syscall tracing — consumes the unified ptrace engine (ADR A2).

Pairs syscall entry/exit stops into ``dto.SyscallEvent`` objects, decoding
arguments from target memory while the tracee is stopped (the ``rendered``
field must be built at collection time — the memory it describes may be
gone by presentation). Argument reads go through a ``LiveView`` (ADR A4:
tracing is long-running and the target mutates memory between stops).

format_summary / format_syscalls_json live in present/ (batch 6, ADR A5).

契约（contracts.md 批 5 表 SY1–SY13，含 A7 修复 SY5/SY8/SY10）。
当前为骨架：常量/签名已锁定，函数返回哨兵，批次 5 实现填实。
"""

import struct
import time

from .. import dto
from ..kernel.mem import Transport
from ..kernel.ptrace import (
    ARG_REGS,
    PTRACE_O_TRACECLONE,
    PTRACE_O_TRACEEXEC,
    PTRACE_O_TRACESYSGOOD,
    ExecEvent,
    Exited,
    PtraceEngine,
    SyscallStop,
)
from ..kernel.views import LiveView
from .syscall_abi import (
    DECODE,
    MAP_FLAGS,
    OPEN_FLAGS,
    PROT_FLAGS,
    SYSCALL_NAMES,
    SYSCALL_NRS,
    TRACE_GROUPS,
)

#: strace default: print 32 chars of a string (复刻约束).
STR_MAX = 32

#: (int) AT_FDCWD (-100) as an unsigned x86-64 register value.
_AT_FDCWD = 0xFFFFFFFFFFFFFF9C

PTRACE_OPTIONS = (PTRACE_O_TRACESYSGOOD | PTRACE_O_TRACECLONE
                  | PTRACE_O_TRACEEXEC)

#: Kinds rendered as an opaque pointer: hex address or NULL (SY9).
_POINTER_KINDS = frozenset({
    "buf_in", "buf_out", "sockaddr", "msghdr", "rem",
})


# ---------------------------------------------------------------------------
# String rendering helpers (strace style) — SY9
# ---------------------------------------------------------------------------

def escape_bytes(data: bytes) -> str:
    """Render bytes strace-style: printable ASCII kept, ``"``/``\\`` escaped,
    the rest as \\NNN octal (SY9)."""
    out = []
    for b in data:
        if 0x20 <= b <= 0x7E:
            ch = chr(b)
            if ch in ('"', "\\"):
                out.append("\\" + ch)
            else:
                out.append(ch)
        else:
            out.append(f"\\{b:03o}")
    return "".join(out)


def truncate_escaped(escaped: str, max_chars: int = STR_MAX) -> str:
    """Truncate an escaped string to ~``max_chars`` visible chars + ``...``
    (SY9: strace shows ``"very long stri..."``)."""
    if len(escaped) <= max_chars:
        return escaped
    return escaped[:max_chars] + "..."


def render_str_arg(data: bytes, verbose: bool = False) -> str:
    """Bytes → quoted strace string, truncated unless verbose (SY9)."""
    escaped = escape_bytes(data)
    if not verbose:
        escaped = truncate_escaped(escaped)
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# Argument decoding (SY9/SY10; view injected — FakeMemory in unit tests)
# ---------------------------------------------------------------------------

def read_cstr(view, addr: int, max_len: int = 4096) -> str:
    """Read a NUL-terminated string at *addr* (chunk 256; unreadable → raw
    hex address like strace; NULL addr → "NULL")."""
    if not addr:
        return "NULL"
    data = b""
    chunk = 256
    while len(data) < max_len:
        part = view.read(addr + len(data), chunk)
        if part is None or part == b"":
            if not data:
                # edge mapping: probe the largest satisfiable length (SY9)
                part = read_available(view, addr + len(data), chunk)
                if not part:
                    return f"0x{addr:x}"
            else:
                break  # partial content already collected — keep it
        data += part
        nul = data.find(b"\x00")
        if nul != -1:
            return render_str_arg(data[:nul])
        if len(part) < chunk:
            break
    return render_str_arg(data[:max_len])


def read_available(view, addr: int, want: int) -> bytes:
    """Read up to *want* bytes at *addr*, binary-searching the largest
    satisfiable length for edge mappings (SY9)."""
    data = view.read(addr, want)
    if data and len(data) == want:
        return data
    best = b""
    lo, hi = 1, want
    while lo <= hi:
        mid = (lo + hi) // 2
        part = view.read(addr, mid)
        if part and len(part) == mid:
            best = part
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def read_timespec(view, addr: int) -> str:
    """Read struct timespec {tv_sec; tv_nsec} → ``{sec, nsec}`` (SY9)."""
    if not addr:
        return "NULL"
    data = view.read(addr, 16)
    if data is None or len(data) < 16:
        return f"0x{addr:x}"
    sec, nsec = struct.unpack("<qq", data)
    return f"{{{sec}, {nsec}}}"


def decode_flags(value: int, table: dict) -> str:
    """OR-decode *value* against a name table; leftover bits as hex (SY9)."""
    if value == 0 and 0 in table:
        return table[0]  # O_RDONLY special case: flags==0 has a name
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


def render_scalar(kind: str, value: int) -> str:
    """Scalar argument kinds: fd/dirfd (AT_FDCWD), mode (octal), signal,
    prot/map_flags/open_flags tables, int-range str vs hex (SY9)."""
    if kind == "fd" or kind == "dirfd":
        # registers carry sign-extended ints; interpret as signed 32-bit so
        # both 0xFFFFFFFF and 0xFFFFFFFFFFFFFFFF render as -1 (SY9)
        s32 = value & 0xFFFFFFFF
        if s32 >= 1 << 31:
            s32 -= 1 << 32
        return "AT_FDCWD" if s32 == -100 else str(s32)
    if kind == "mode":
        return oct(value & 0o7777)
    if kind == "signal":
        return f"SIG{value}"
    if kind == "prot":
        return decode_flags(value, PROT_FLAGS)
    if kind == "map_flags":
        return decode_flags(value, MAP_FLAGS)
    if kind == "open_flags":
        return decode_flags(value, OPEN_FLAGS)
    if kind == "addr":
        return hex(value)
    # int-like kinds and unknown kinds
    if -(2 ** 31) <= value <= 2 ** 31 - 1:
        return str(value)
    return hex(value)


def decode_args(view, name: str, args) -> str:
    """Render the six syscall args at entry time (SY9, 全显复刻约束).

    ``buf_in``/``buf_out`` args render as their address; exit-time content
    is spliced by :func:`fill_out_args`.
    """
    kinds = DECODE.get(name)
    parts = []
    for i in range(6):
        if i >= len(args):
            break
        value = args[i]
        kind = kinds[i] if kinds and i < len(kinds) else None
        if kind == "path":
            parts.append(read_cstr(view, value))
        elif kind in _POINTER_KINDS:
            parts.append(f"0x{value:x}" if value else "NULL")
        elif kind in ("timespec", "timespec_out"):
            parts.append(read_timespec(view, value))
        elif kind is None:
            parts.append(hex(value))
        else:
            parts.append(render_scalar(kind, value))
    return ", ".join(parts)


def fill_out_args(view, name: str, args, ret: int, rendered: str,
                  verbose: bool = False) -> str:
    """Post-exit pass: splice buffer contents into the rendered args (SY10).

    A7 修复：ret==0 且 kind=="buf_in" → 渲染空串 ""（P1-7，旧按 32 字节读
    内核未写的陈旧缓冲区）；timespec_out 于 exit 重读替换该段（P1-6，旧
    entry 读到调用前陈旧值——strace 在 exit 读）。
    """
    kinds = DECODE.get(name)
    if not kinds:
        return rendered
    if not any(k in ("buf_in", "buf_out", "timespec_out") for k in kinds):
        return rendered  # nothing exit-dependent to splice

    parts = rendered.split(", ")
    for i, kind in enumerate(kinds):
        if i >= len(args) or i >= len(parts):
            break
        addr = args[i]
        if kind == "timespec_out":
            # A7 P1-6: strace reads output structs at exit — re-read now
            parts[i] = read_timespec(view, addr)
            continue
        if kind == "buf_in":
            if not addr:
                continue
            if ret == 0:
                # A7 P1-7: EOF — kernel wrote nothing; render ""
                parts[i] = f'0x{addr:x}/""'
                continue
            if ret > 0:
                data = read_available(view, addr, min(ret, 4096))
                if data:
                    parts[i] = f"0x{addr:x}/{render_str_arg(data, verbose)}"
            continue
        if kind == "buf_out":
            if not addr:
                continue
            # ret is a status code here (stat success == 0), not a byte
            # count — still read the struct content on success
            length = ret if ret > 0 else 32
            data = read_available(view, addr, min(length, 4096))
            if data:
                parts[i] = f"0x{addr:x}/{render_str_arg(data, verbose)}"
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# TraceFilter: -e trace=<spec> parsing (SY8, A7 P0-3)
# ---------------------------------------------------------------------------

class TraceFilter:
    """Compiled ``-e trace=`` expression (SY8).

    Empty spec → trace everything.  Pure ``!`` exclusions → everything
    except the excluded.  Mixed include/exclude → **include − exclude**
    (A7 P0-3 契约变更：旧并入 include 致语义反转).
    """

    def __init__(self, spec: str = ""):
        self.spec = spec
        self.names = None  # None = no filtering
        self._exclude_only = False
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
            # "!a,!b" — everything except the excluded (SY8)
            self.names = exclude
            self._exclude_only = True
        else:
            # A7 P0-3: mixed expressions subtract, never merge (SY8)
            self.names = include - exclude

    def matches(self, name: str) -> bool:
        if self.names is None:
            return True
        if self._exclude_only:
            return name not in self.names
        return name in self.names


# ---------------------------------------------------------------------------
# Tracing engine consumer (SY1–SY7)
# ---------------------------------------------------------------------------

def _emit(events, view, tid, nr, args, ret, elapsed, flt, verbose):
    """Build one SyscallEvent from a paired entry/exit (SY3)."""
    name = SYSCALL_NAMES.get(nr, f"sys_{nr}")
    if not flt.matches(name):
        return
    error = None
    if ret > 0xFFFFFFFF00000000:  # -errno range as unsigned
        ret -= 1 << 64
    if -4096 < ret < 0:
        error = -ret
        ret = -1
    rendered = decode_args(view, name, args)
    if error is None:
        rendered = fill_out_args(view, name, args, ret, rendered,
                                 verbose=verbose)
    events.append(dto.SyscallEvent(
        tid=tid, nr=nr, name=name,
        args=[a & 0xFFFFFFFFFFFFFFFF for a in args],
        rendered=rendered, ret=ret, error=error, elapsed=elapsed))


def collect_syscalls(pid: int, *, trace=None, max_events=None,
                     verbose: bool = False) -> list[dto.SyscallEvent]:
    """Trace syscalls of ``pid`` (all threads) and return paired events.

    Engine: PtraceEngine(feature="syscall tracing") → seize(PTRACE_OPTIONS)
    → stop_all → restart_all → wait_event loop → finally detach (SY1).
    Raises ``AttachFailed`` / ``ProcessNotFound`` /
    ``UnsupportedArchitecture`` (the last from the engine, SY1/T16).
    No printing.
    """
    flt = TraceFilter(trace or "")
    engine = PtraceEngine(pid, feature="syscall tracing")
    engine.seize(PTRACE_OPTIONS)  # SY1: AttachFailed propagates as-is
    try:
        engine.stop_all()
        engine.restart_all()
        # SY4 (A4): one LiveView per trace — never the snapshot cache
        view = LiveView(Transport(pid))
        events: list[dto.SyscallEvent] = []
        stash: dict[int, tuple[int, list[int], float]] = {}
        while True:
            ev = engine.wait_event()
            if ev is None:
                break  # SY6: ECHILD — no tracees remain
            if isinstance(ev, Exited):
                if ev.tid == pid:
                    break  # SY6: main thread gone — process over
                continue
            if isinstance(ev, ExecEvent):
                # SY5: execve has no syscall-exit stop on success — the
                # EXEC event completes the pair (ret=0)
                pending = stash.pop(ev.tid, None)
                if pending is not None:
                    nr, args, t0 = pending
                    if nr == SYSCALL_NRS.get("execve"):
                        # A7 P1-5: the synthesized exit event goes through
                        # the *user* filter (old tree hardcoded
                        # TraceFilter("") and bypassed it)
                        _emit(events, view, ev.tid, nr, args, 0,
                              time.monotonic() - t0, flt, verbose)
                engine.resume(ev.tid)
            elif isinstance(ev, SyscallStop):
                regs = engine.getregs(ev.tid)
                if regs is None:
                    engine.resume(ev.tid)  # thread died at the stop
                    continue
                pending = stash.get(ev.tid)
                if pending is None:
                    # SY2: syscall entry — park nr/args/timestamp per tid
                    args = [getattr(regs, r) for r in ARG_REGS]
                    nr = regs.orig_rax & 0xFFFFFFFF
                    stash[ev.tid] = (nr, args, time.monotonic())
                else:
                    # SY2: exit — pair with the stashed entry and emit
                    nr, args, t0 = pending
                    del stash[ev.tid]
                    _emit(events, view, ev.tid, nr, args, regs.rax,
                          time.monotonic() - t0, flt, verbose)
                engine.resume(ev.tid)
            if max_events is not None and len(events) >= max_events:
                break  # SY7: quota checked after the event is emitted
        return events
    finally:
        engine.detach()  # SY1: never leave the tracee stopped
