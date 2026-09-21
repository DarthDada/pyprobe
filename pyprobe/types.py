"""Structured data types returned by the pyprobe collection API.

The library API is split into two layers:

* ``collect_*`` functions return plain data objects (this module) so callers
  can inspect, serialize, or post-process the results programmatically.
* ``format_*`` functions turn those data objects into the human-readable
  strings used by the CLI.

Note (TODO §8.9): each dataclass also carries a ``format()`` method that
renders the CLI-style string for that single object.  Those methods import
ANSI helpers from :mod:`pyprobe.colors` so the CLI can colorize output
without the caller having to pass color decisions through the format layer.
The JSON / ``dataclasses.asdict`` serialization paths bypass ``format()``
entirely and stay free of presentation concerns.  The module-level docstring
used to claim "plain data objects" — narrowed here to accept the reality
that ``format()`` lives alongside the data by design (low impact, single
small presentation dependency).
"""

import os

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .colors import cyan, dim, green, red, yellow_bold

_ERRNO_NAMES = {
    1: "EPERM", 2: "ENOENT", 3: "ESRCH", 4: "EINTR", 5: "EIO", 9: "EBADF",
    11: "EAGAIN", 12: "ENOMEM", 13: "EACCES", 16: "EBUSY", 17: "EEXIST",
    20: "ENOTDIR", 21: "EISDIR", 22: "EINVAL", 24: "EMFILE",
    28: "ENOSPC", 32: "EPIPE", 36: "ENAMETOOLONG", 39: "ENOTEMPTY",
    40: "ELOOP", 61: "ENODATA", 75: "EOVERFLOW", 84: "EILSEQ",
    98: "EADDRINUSE", 99: "EADDRNOTAVAIL", 104: "ECONNRESET",
    110: "ETIMEDOUT", 111: "ECONNREFUSED", 115: "EINPROGRESS",
}

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
    """Return the last ``depth`` components of ``path``.

    Paths with ``depth`` components or fewer are returned unchanged.
    """
    parts = path.split(os.sep)
    if len(parts) <= depth:
        return path
    return os.sep.join(parts[-depth:])


@dataclass
class FrameInfo:
    """A single Python stack frame."""

    name: Optional[str]
    filename: Optional[str]
    line: int

    def format(self, index: int, *, color: bool = False,
               verbose: bool = False) -> str:
        filename = self.filename
        if filename is not None and not verbose:
            filename = _shorten_path(filename)
        return (
            f"  #{index} {green(self.name or '?', color)} "
            f"({cyan(filename or '?', color)}:{dim(str(self.line), color)})"
        )


@dataclass
class ThreadInfo:
    """A thread observed in the target process."""

    native_tid: int
    thread_id: int = 0
    name: str = ""
    frames: List[FrameInfo] = field(default_factory=list)
    idle: bool = False

    def format(self, *, color: bool = False, verbose: bool = False) -> str:
        status = f" {dim('(idle)', color)}" if self.idle else ""
        header = f"Thread {yellow_bold(str(self.native_tid), color)}{status}"
        if self.name:
            header += f': "{self.name}"'

        if not self.frames:
            body = "  (no Python frame — thread may be in C code or idle)"
        else:
            body = "\n".join(
                f.format(i, color=color, verbose=verbose)
                for i, f in enumerate(self.frames))

        return header + "\n" + body if body else header


@dataclass
class ProcessInfo:
    """Metadata about the target process."""

    pid: int
    cmdline: str
    exe_path: str
    python_version: str = "?"

    def format_header(self, *, color: bool = False) -> str:
        return (
            f"Process {yellow_bold(str(self.pid), color)}: {self.cmdline}\n"
            f"Python v{self.python_version} ({self.exe_path})\n"
        )


@dataclass
class NativeFrame:
    """A single native (C) stack frame."""

    pc: int
    symbol: str = "??"
    module: Optional[str] = None


@dataclass
class NativeThreadInfo:
    """A native thread observed in the target process."""

    tid: int
    comm: str = ""
    frames: List[NativeFrame] = field(default_factory=list)
    unwind_failed: bool = False

    def format(self, index: int, *, color: bool = False,
               verbose: bool = False) -> str:
        header = (
            f'Thread {index} (LWP {yellow_bold(str(self.tid), color)}) '
            f'"{self.comm}":'
        )
        if not self.frames and self.unwind_failed:
            body = red(
                "  Backtrace stopped: Cannot access memory at address 0x0",
                color)
        elif not self.frames:
            body = ""
        else:
            lines = []
            for j, f in enumerate(self.frames):
                pc = dim(f"0x{f.pc:016x}", color)
                symbol = green(f.symbol, color)
                if f.module:
                    module = f.module if verbose else os.path.basename(f.module)
                    lines.append(
                        f"  #{j}  {pc} in {symbol} () "
                        f"from {cyan(module, color)}")
                else:
                    lines.append(f"  #{j}  {pc} in {symbol} ()")
            body = "\n".join(lines)
        return header + "\n" + body if body else header


@dataclass
class SyscallEvent:
    """A single completed syscall observation (entry + exit paired).

    ``rendered`` holds the pre-rendered argument string (built by
    ``syscall_trace._decode_args`` / ``_fill_out_args``) so ``format()``
    stays a pure data -> string step.
    """

    tid: int
    nr: int
    name: str
    args: List[int] = field(default_factory=list)
    rendered: str = ""
    ret: int = 0
    error: Optional[int] = None    # errno when the syscall failed (-1 return)
    elapsed: float = 0.0           # seconds between entry and exit stop

    def format(self, *, color: bool = False) -> str:
        """Render one strace-style event line.

        Success:  ``1234  openat(AT_FDCWD, "/tmp/x") = 3``
        Failure:  ``1234  openat(...) = -1 ENOENT (No such file or directory)``
        Slow syscalls get a trailing ``<0.000123>`` like strace -T.
        """
        tid = yellow_bold(str(self.tid), color)
        name = green(self.name, color)
        if self.error is not None:
            ename = _ERRNO_NAMES.get(self.error, f"ERRNO_{self.error}")
            edesc = _ERRNO_DESCS.get(self.error, "Unknown error")
            ret = red(f"= -1 {ename} ({edesc})", color)
        else:
            ret = f"= {self.ret}"
        line = f"{tid}  {name}({self.rendered}) {ret}"
        if self.elapsed:
            line += f" {dim(f'<{self.elapsed:.6f}>', color)}"
        return line


@dataclass
class ProfileData:
    """Aggregated sampling result of a ``record`` run.

    ``counts`` maps a folded-stack key (``"<thread>";root;...;leaf``) to
    the number of samples that observed exactly that stack.  ``samples``
    counts active (non-idle) thread observations; ``idle_samples`` the
    ones excluded by idle detection.  ``version_warning`` carries the
    soft warning (if any) captured by :class:`pyprobe.process.ProcessSession`
    so ``dump_record`` can surface it once at the start (TODO §8.5).
    """

    proc_info: ProcessInfo
    counts: Dict[str, int] = field(default_factory=dict)
    samples: int = 0
    idle_samples: int = 0
    elapsed: float = 0.0
    version_warning: Optional[str] = None
