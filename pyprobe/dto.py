"""Structured data types returned by the pyprobe collection API (ADR A5).

Pure data, no presentation: DTOs carry no ``format()`` methods and import no
color helpers (reversing the old §8.9 compromise). All rendering lives in
``present/text.py`` (batch 6); errno name tables and path shortening move
with it. Field names, order, and defaults replicate the old ``types.py``
exactly — ``dataclasses.asdict`` key sets are the JSON output schema
(复刻约束, contracts.md §3).
"""

from dataclasses import dataclass, field


@dataclass
class FrameInfo:
    """A single Python stack frame."""

    name: str | None
    filename: str | None
    line: int


@dataclass
class ThreadInfo:
    """A thread observed in the target process."""

    native_tid: int
    thread_id: int = 0
    name: str = ""
    frames: list[FrameInfo] = field(default_factory=list)
    idle: bool = False


@dataclass
class ProcessInfo:
    """Metadata about the target process."""

    pid: int
    cmdline: str
    exe_path: str
    python_version: str = "?"


@dataclass
class NativeFrame:
    """A single native (C) stack frame."""

    pc: int
    symbol: str = "??"
    module: str | None = None


@dataclass
class NativeThreadInfo:
    """A native thread observed in the target process."""

    tid: int
    comm: str = ""
    frames: list[NativeFrame] = field(default_factory=list)
    unwind_failed: bool = False


@dataclass
class SyscallEvent:
    """A single completed syscall observation (entry + exit paired).

    ``rendered`` holds the argument string decoded at collection time (the
    target memory it describes may be gone by presentation time), so the
    present layer stays a pure data -> string step.
    """

    tid: int
    nr: int
    name: str
    args: list[int] = field(default_factory=list)
    rendered: str = ""
    ret: int = 0
    error: int | None = None
    elapsed: float = 0.0


@dataclass
class ProfileData:
    """Aggregated sampling result of a ``record`` run.

    ``counts`` maps a folded-stack key (``"<thread>";root;...;leaf``) to the
    number of samples that observed exactly that stack. ``samples`` counts
    active (non-idle) thread observations; ``idle_samples`` the ones excluded
    by idle detection. ``version_warning`` carries the soft warning (if any)
    captured by the session so the CLI can surface it once at the start.
    """

    proc_info: ProcessInfo
    counts: dict[str, int] = field(default_factory=dict)
    samples: int = 0
    idle_samples: int = 0
    elapsed: float = 0.0
    version_warning: str | None = None
