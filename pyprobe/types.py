"""Structured data types returned by the pyprobe collection API.

The library API is split into two layers:

* ``collect_*`` functions return plain data objects (this module) so callers
  can inspect, serialize, or post-process the results programmatically.
* ``format_*`` functions turn those data objects into the human-readable
  strings used by the CLI.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from .colors import cyan, dim, green, red, yellow_bold


@dataclass
class FrameInfo:
    """A single Python stack frame."""

    name: Optional[str]
    filename: Optional[str]
    line: int

    def format(self, index: int, *, color: bool = False) -> str:
        return (
            f"  #{index} {green(self.name or '?', color)} "
            f"({cyan(self.filename or '?', color)}:{dim(str(self.line), color)})"
        )


@dataclass
class ThreadInfo:
    """A thread observed in the target process."""

    native_tid: int
    thread_id: int = 0
    name: str = ""
    frames: List[FrameInfo] = field(default_factory=list)
    idle: bool = False

    def format(self, *, color: bool = False) -> str:
        status = f" {dim('(idle)', color)}" if self.idle else ""
        header = f"Thread {yellow_bold(str(self.native_tid), color)}{status}"
        if self.name:
            header += f': "{self.name}"'

        if not self.frames:
            body = "  (no Python frame — thread may be in C code or idle)"
        else:
            body = "\n".join(f.format(i, color=color) for i, f in enumerate(self.frames))

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

    def format(self, index: int, *, color: bool = False) -> str:
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
                    lines.append(
                        f"  #{j}  {pc} in {symbol} () "
                        f"from {cyan(f.module, color)}")
                else:
                    lines.append(f"  #{j}  {pc} in {symbol} ()")
            body = "\n".join(lines)
        return header + "\n" + body if body else header
