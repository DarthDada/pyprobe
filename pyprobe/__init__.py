"""pyprobe: CPython out-of-process inspection tool (pure Python port).

Public API
----------

Data collection (returns structured data, raises ``PyProbeError`` on failure):

* :func:`collect_python`  → ``(ProcessInfo, list[ThreadInfo])``
* :func:`collect_native`  → ``list[NativeThreadInfo]``

Formatting (turns structured data into the CLI-style string; pass
``color=True`` for ANSI-colored output):

* :func:`format_process`  — Python stacks
* :func:`format_native`   — native stacks

CLI wrappers (collect + format + print, return an exit code):

* :func:`dump_python`
* :func:`dump_native`

Structured data types live in :mod:`pyprobe.types`; exceptions in
:mod:`pyprobe.errors`.

Example
-------

::

    from pyprobe import collect_python, format_process, PyProbeError

    try:
        proc_info, threads = collect_python(pid)
    except PyProbeError as exc:
        ...
    else:
        print(format_process(proc_info, threads))
"""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("pyprobe")
except Exception:
    # Package metadata unavailable (e.g. running from source tree without
    # install). Fall back to a marker; the canonical source is pyproject.toml.
    __version__ = "0.0.0+unknown"

from .types import (
    FrameInfo, ThreadInfo, ProcessInfo,
    NativeFrame, NativeThreadInfo,
    SyscallEvent, ProfileData,
)
from .errors import (
    PyProbeError, ProcessNotFound, PermissionDenied, SymbolNotFound,
    NoInterpreterState, NoThreadState, VersionNotSupported, AttachFailed,
    UnsupportedArchitecture, ProcessExited,
)
from .stack_dump import (
    collect_python, format_process, dump_python,
    collect_frames, collect_thread, format_process_json,
    read_thread_chain, is_thread_idle_by_stat,
)
from .process import ProcessSession, resolve_process
from .native_dump import (
    collect_native, format_native, dump_native, format_native_json,
)
from .syscall_render import (
    TraceFilter, SyscallStat, format_summary,
)
from .syscall_tracer import (
    collect_syscalls, dump_syscalls,
)
from .sampler import Sampler
from .record import collect_profile, format_folded, dump_record
from .top import dump_top, TopStats
from . import offsets
from .offsets import DEFAULT_VERSION
from . import colors

__all__ = [
    # data types
    "FrameInfo", "ThreadInfo", "ProcessInfo",
    "NativeFrame", "NativeThreadInfo",
    "SyscallEvent", "ProfileData",
    # exceptions
    "PyProbeError", "ProcessNotFound", "PermissionDenied", "SymbolNotFound",
    "NoInterpreterState", "NoThreadState", "VersionNotSupported", "AttachFailed",
    "UnsupportedArchitecture", "ProcessExited",
    # python stack API
    "collect_python", "format_process", "dump_python",
    "collect_frames", "collect_thread", "format_process_json",
    "read_thread_chain", "is_thread_idle_by_stat",
    # process session API (shared resolution; TODO §8.1)
    "ProcessSession", "resolve_process",
    # offsets constants
    "DEFAULT_VERSION",
    # native stack API
    "collect_native", "format_native", "dump_native",
    "format_native_json",
    # syscall tracing API
    "collect_syscalls", "dump_syscalls", "format_summary",
    "TraceFilter", "SyscallStat",
    # sampling engine
    "Sampler",
    # profiling API (record)
    "collect_profile", "format_folded", "dump_record",
    # live view API (top)
    "dump_top", "TopStats",
    # submodules
    "offsets",
    "colors",
]
