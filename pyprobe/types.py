"""Structured data types returned by the pyprobe collection API.

The library API is split into two layers:

* ``collect_*`` functions return plain data objects (this module) so callers
  can inspect, serialize, or post-process the results programmatically.
* ``format_*`` functions turn those data objects into the human-readable
  strings used by the CLI.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FrameInfo:
    """A single Python stack frame."""

    name: Optional[str]
    filename: Optional[str]
    line: int

    def format(self, index: int) -> str:
        return f"  #{index} {self.name or '?'} ({self.filename or '?'}:{self.line})"


@dataclass
class ThreadInfo:
    """A thread observed in the target process."""

    native_tid: int
    thread_id: int = 0
    name: str = ""
    frames: List[FrameInfo] = field(default_factory=list)
    idle: bool = False

    def format(self) -> str:
        status = " (idle)" if self.idle else ""
        header = f"Thread {self.native_tid}{status}"
        if self.name:
            header += f': "{self.name}"'

        if not self.frames:
            body = "  (no Python frame — thread may be in C code or idle)"
        else:
            body = "\n".join(f.format(i) for i, f in enumerate(self.frames))

        return header + "\n" + body if body else header


@dataclass
class ProcessInfo:
    """Metadata about the target process."""

    pid: int
    cmdline: str
    exe_path: str
    python_version: str = "?"

    def format_header(self) -> str:
        return f"Process {self.pid}: {self.cmdline}\nPython v{self.python_version} ({self.exe_path})\n"


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

    def format(self, index: int) -> str:
        header = f'Thread {index} (LWP {self.tid}) "{self.comm}":'
        if not self.frames and self.unwind_failed:
            body = "  Backtrace stopped: Cannot access memory at address 0x0"
        elif not self.frames:
            body = ""
        else:
            lines = []
            for j, f in enumerate(self.frames):
                if f.module:
                    lines.append(
                        f"  #{j}  0x{f.pc:016x} in {f.symbol} () from {f.module}")
                else:
                    lines.append(f"  #{j}  0x{f.pc:016x} in {f.symbol} ()")
            body = "\n".join(lines)
        return header + "\n" + body if body else header
