"""All human-readable formatting (ADR A5 落地).

Everything that turns DTOs into CLI-style text lives here — DTOs carry no
format() methods (reversing the old §8.9 compromise). The errno name/desc
tables and path shortening moved here from the old ``types.py``; the
render-side of TopStats moved here from ``top.py``.

契约（contracts.md 批 6 表 TX1–TX10）。格式字符串自旧树
types.py/stack_dump/native_dump/record/top/syscall_render 逐字节复刻
（复刻约束 §3——这些字符串是 CLI 的用户可见面）。
"""

import os

from .. import dto
from .color import cyan, dim, green, red, yellow_bold

#: Terminal control sequences (契约 TX10, 逐字复刻).
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[H\x1b[2J"

#: errno → name (契约 TX6; 自旧 types.py 逐字，权威来源 errno.h).
_ERRNO_NAMES = {
    1: "EPERM", 2: "ENOENT", 3: "ESRCH", 4: "EINTR", 5: "EIO", 9: "EBADF",
    11: "EAGAIN", 12: "ENOMEM", 13: "EACCES", 16: "EBUSY", 17: "EEXIST",
    20: "ENOTDIR", 21: "EISDIR", 22: "EINVAL", 24: "EMFILE",
    28: "ENOSPC", 32: "EPIPE", 36: "ENAMETOOLONG", 39: "ENOTEMPTY",
    40: "ELOOP", 61: "ENODATA", 75: "EOVERFLOW", 84: "EILSEQ",
    98: "EADDRINUSE", 99: "EADDRNOTAVAIL", 104: "ECONNRESET",
    110: "ETIMEDOUT", 111: "ECONNREFUSED", 115: "EINPROGRESS",
}

#: errno → description (契约 TX6; 自旧 types.py 逐字，strerror(3) 文本).
_ERRNO_DESCS = {
    1: "Operation not permitted", 2: "No such file or directory",
    3: "No such process", 4: "Interrupted system call",
    5: "Input/output error", 9: "Bad file descriptor",
    11: "Resource temporarily unavailable", 12: "Cannot allocate memory",
    13: "Permission denied", 16: "Device or resource busy",
    17: "File exists", 20: "Not a directory", 21: "Is a directory",
    22: "Invalid argument", 24: "Too many open files",
    28: "No space left on device", 32: "Broken pipe",
    36: "File name too long", 39: "Directory not empty",
    40: "Too many levels of symbolic links", 61: "No data available",
    75: "Value too large for defined data type",
    84: "Invalid or incomplete multibyte or wide character",
    98: "Address already in use", 99: "Cannot assign requested address",
    104: "Connection reset by peer", 110: "Connection timed out",
    111: "Connection refused", 115: "Operation now in progress",
}


def _shorten_path(path: str, depth: int = 2) -> str:
    """Return the last ``depth`` components of ``path`` (契约 TX1: Python
    帧末 2 级缩短；≤depth 级原样)。"""
    parts = path.split(os.sep)
    if len(parts) <= depth:
        return path
    return os.sep.join(parts[-depth:])


def format_frame(frame: dto.FrameInfo, index: int, *, color: bool = False,
                 verbose: bool = False) -> str:
    """One Python frame line (TX1): ``  #<i> <name|?> (<file|?>:<line>)``."""
    filename = frame.filename
    if filename is not None and not verbose:
        # TX1 (复刻约束 Python 帧末 2 级): non-verbose shortens to the
        # last two components; -v keeps the full path.
        filename = _shorten_path(filename)
    return (
        f"  #{index} {green(frame.name or '?', color)} "
        f"({cyan(filename or '?', color)}:{dim(str(frame.line), color)})"
    )


def format_thread(thread: dto.ThreadInfo, *, color: bool = False,
                  verbose: bool = False) -> str:
    """One Python thread block (TX2): header + frame lines."""
    status = f" {dim('(idle)', color)}" if thread.idle else ""
    header = f"Thread {yellow_bold(str(thread.native_tid), color)}{status}"
    if thread.name:
        header += f': "{thread.name}"'

    if not thread.frames:
        # TX2 (逐字复刻): em-dash included — the hint line is user-visible.
        body = "  (no Python frame — thread may be in C code or idle)"
    else:
        body = "\n".join(
            format_frame(f, i, color=color, verbose=verbose)
            for i, f in enumerate(thread.frames))

    return header + "\n" + body if body else header


def format_process_header(proc_info: dto.ProcessInfo, *,
                          color: bool = False) -> str:
    """Process header (TX3): ``Process <pid>: <cmdline>\\nPython v<ver> (<exe>)``."""
    return (
        f"Process {yellow_bold(str(proc_info.pid), color)}: "
        f"{proc_info.cmdline}\n"
        f"Python v{proc_info.python_version} ({proc_info.exe_path})\n"
    )


def format_process(proc_info: dto.ProcessInfo,
                   threads: list[dto.ThreadInfo], *, color: bool = False,
                   verbose: bool = False) -> str:
    """Full Python stack dump text (TX4)."""
    parts = [format_process_header(proc_info, color=color)]
    for t in threads:
        parts.append(format_thread(t, color=color, verbose=verbose))
        parts.append("")  # TX4: blank line separates thread blocks
    return "\n".join(parts)


def format_native_thread(thread: dto.NativeThreadInfo, index: int, *,
                         color: bool = False, verbose: bool = False) -> str:
    """One native thread block (TX5): gdb-style header + frames."""
    header = (
        f'Thread {index} (LWP {yellow_bold(str(thread.tid), color)}) '
        f'"{thread.comm}":'
    )
    if not thread.frames and thread.unwind_failed:
        # TX5 (逐字复刻): unwind died on a corrupt stack with nothing read.
        body = red(
            "  Backtrace stopped: Cannot access memory at address 0x0",
            color)
    elif not thread.frames:
        body = ""
    else:
        lines = []
        for j, f in enumerate(thread.frames):
            pc = dim(f"0x{f.pc:016x}", color)
            symbol = green(f.symbol, color)
            if f.module:
                # TX5 (复刻约束 native basename): non-verbose shows the
                # module basename; -v keeps the full path.
                module = f.module if verbose else os.path.basename(f.module)
                lines.append(
                    f"  #{j}  {pc} in {symbol} () "
                    f"from {cyan(module, color)}")
            else:
                lines.append(f"  #{j}  {pc} in {symbol} ()")
        body = "\n".join(lines)
    return header + "\n" + body if body else header


def format_native(threads: list[dto.NativeThreadInfo], *,
                  color: bool = False, verbose: bool = False) -> str:
    """Full native stack dump text (TX5).

    No process header here — ``dump_native`` prints it (with the pid)
    before collecting, so failure paths keep their context.
    """
    parts = []
    for i, t in enumerate(threads):
        parts.append(format_native_thread(t, i + 1, color=color,
                                          verbose=verbose))
        parts.append("")
    return "\n".join(parts)


def format_syscall_event(ev: dto.SyscallEvent, *, color: bool = False) -> str:
    """One strace-style event line (TX6): ``<tid>  <name>(<args>) = <ret>``
    with errno name decode and ``<elapsed>`` tail."""
    tid = yellow_bold(str(ev.tid), color)
    name = green(ev.name, color)
    if ev.error is not None:
        # TX6 (复刻约束 errno 名解码): table lookup, unknown → ERRNO_<n>.
        ename = _ERRNO_NAMES.get(ev.error, f"ERRNO_{ev.error}")
        edesc = _ERRNO_DESCS.get(ev.error, "Unknown error")
        ret = red(f"= -1 {ename} ({edesc})", color)
    else:
        ret = f"= {ev.ret}"
    line = f"{tid}  {name}({ev.rendered}) {ret}"
    if ev.elapsed:
        # TX6 (复刻约束 elapsed 格式): strace -T tail; 0.0 stays hidden.
        line += f" {dim(f'<{ev.elapsed:.6f}>', color)}"
    return line


class SyscallStat:
    """Accumulated stats for one syscall name (TX7)."""

    def __init__(self, name: str):
        self.name = name
        self.calls = 0
        self.errors = 0
        self.total_time = 0.0  # seconds


def format_summary(events: list[dto.SyscallEvent], *,
                   color: bool = False) -> str:
    """strace -c style summary table (TX7: total/s 列复刻约束)."""
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

    # TX7: total_time 降序（复刻约束；列宽/分隔行逐字自旧 syscall_render）。
    rows = sorted(stats.values(), key=lambda s: -s.total_time)
    lines = [
        f"{'syscall':<20} {'calls':>10} {'errors':>10} "
        f"{'total':>11} {'total/s':>11} {'per-call':>16}",
        "-" * 82,
    ]
    for st in rows:
        per_call = st.total_time / st.calls if st.calls else 0.0
        lines.append(
            f"{st.name:<20} {st.calls:>10} {st.errors:>10} "
            f"{st.total_time:>10.6f} {st.total_time:>10.6f} {per_call:>13.6f}"
        )
    lines.append("-" * 82)
    lines.append(
        f"{'total':<20} {total_calls:>10} {total_errors:>10} "
        f"{total_time:>10.6f} {total_time:>10.6f}"
    )
    return "\n".join(lines)


def format_folded(profile: dto.ProfileData) -> str:
    """Folded stacks text, count-desc then key-asc (TX8)."""
    lines = [
        f"{key} {count}"
        for key, count in sorted(profile.counts.items(),
                                 key=lambda kv: (-kv[1], kv[0]))
    ]
    if not lines:
        return ""  # TX8: empty profile → empty string (no stray newline)
    return "\n".join(lines) + "\n"


def render_top(stats, proc_info: dto.ProcessInfo, *, elapsed: float,
               color: bool = False, top_n: int = 15) -> str:
    """Full top screen (TX9; A7 P1-8 修复：无名顶帧 OWN% 以 "?" 归一查表）."""
    total = max(stats.samples, 1)

    lines = [format_process_header(proc_info, color=color).rstrip()]
    lines.append(
        f"Elapsed {elapsed:.1f}s | {stats.samples} samples "
        f"(idle {stats.idle_samples})")
    lines.append("")

    lines.append("Active threads")
    lines.append(f"  {'TID':<7} {'OWN%':>6}  CURRENT")
    for tid, (frame, name) in sorted(stats.current.items()):
        tid_s = yellow_bold(str(tid), color)
        label = f"{name} " if name else ""
        if frame is not None:
            # TX9 (A7 P1-8 修复): the own-count lookup key normalizes an
            # unnamed top frame to "?" — the old tree queried
            # frame.name (None) and the OWN% column was stuck at 0.
            own = stats.own.get(frame.name or "?", 0) / total
            fname = green(frame.name or "?", color)
            loc = f" ({cyan(frame.filename or '?', color)}:{frame.line})"
            current = f"{label}{fname}{dim(loc, color)}"
        else:
            own = 0.0
            current = dim(f"{label}(no Python frame)", color)
        lines.append(f"  {tid_s:<7} {own * 100:>5.1f}%  {current}")
    lines.append("")

    if stats.idle_threads:
        idlers = ", ".join(str(t) for t in sorted(stats.idle_threads))
        lines.append(f"Idle threads: {idlers}")
        lines.append("")

    lines.append("Top functions")
    lines.append(f"  {'OWN%':>6} {'TOTAL%':>7}  {'TIME':>7}  FUNCTION")
    ranked = sorted(stats.own.items(), key=lambda kv: (-kv[1], kv[0]))
    for name, own_n in ranked[:top_n]:
        own_pct = own_n / total * 100
        tot_pct = stats.total.get(name, 0) / total * 100
        time_s = own_n / total * elapsed
        lines.append(
            f"  {dim(f'{own_pct:>5.1f}%', color)} "
            f"{dim(f'{tot_pct:>6.1f}%', color)}  "
            f"{dim(f'{time_s:>6.1f}s', color)}  "
            f"{green(name, color)}")
    return "\n".join(lines) + "\n"
