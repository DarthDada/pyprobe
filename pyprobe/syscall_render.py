"""Syscall rendering helpers — strace-style string + argument decoding.

Pure functions only (no ptrace, no I/O except reader-injected memory
reads).  The reader is passed in so unit tests can substitute a
``FakeReader`` (``tests/helpers.py``).  Split out of ``syscall_trace.py``
(TODO §8.3) so the engine module imports only what it needs at top, with
no mid-file imports left over from the prior two-files-stitched shape.

Public surface used by ``pyprobe.__init__`` and the CLI:

* :class:`TraceFilter`   — ``-e trace=`` expression compiler.
* :class:`SyscallStat`   — per-syscall accumulator for ``format_summary``.
* :func:`format_summary` — strace ``-c`` style summary table.

Internal helpers (``escape_bytes`` / ``truncate_escaped`` / ``render_str_arg``
/ ``_read_cstr`` / ``_read_available`` / ``_read_timespec`` / ``_decode_flags``
/ ``_render_scalar`` / ``_decode_args`` / ``_fill_out_args``) are exposed for
unit tests but not part of ``__all__``.
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
