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

from .types import (
    FrameInfo, ThreadInfo, ProcessInfo,
    NativeFrame, NativeThreadInfo,
)
from .errors import (
    PyProbeError, ProcessNotFound, PermissionDenied, SymbolNotFound,
    NoInterpreterState, NoThreadState, VersionNotSupported, AttachFailed,
)
from .stack_dump import (
    collect_python, format_process, dump_python,
    collect_frames, collect_thread,
)
from .native_dump import (
    collect_native, format_native, dump_native,
)
from . import offsets
from . import colors

__all__ = [
    # data types
    "FrameInfo", "ThreadInfo", "ProcessInfo",
    "NativeFrame", "NativeThreadInfo",
    # exceptions
    "PyProbeError", "ProcessNotFound", "PermissionDenied", "SymbolNotFound",
    "NoInterpreterState", "NoThreadState", "VersionNotSupported", "AttachFailed",
    # python stack API
    "collect_python", "format_process", "dump_python",
    "collect_frames", "collect_thread",
    # native stack API
    "collect_native", "format_native", "dump_native",
    # submodules
    "offsets",
    "colors",
]

__version__ = "0.1.0"
