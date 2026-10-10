"""pyprobe: CPython out-of-process inspection toolset (stack, memory, CPU).

Library API surface (§10.5-2, 契约 API1/API2): the ``__all__`` list
replicates the old tree's exactly — every name is bound to its new-
architecture implementation (compat mappings documented per section) —
plus the explicit ADR A1 addition :func:`resolve_layout`.

Internal architecture (kernel → target → cpython → observe → present →
cli, ADR A8) is documented in next/docs/contracts.md.
"""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("pyprobe")
except Exception:
    # Package metadata unavailable (e.g. running from source tree without
    # install). Fall back to a marker; the canonical source is pyproject.toml.
    __version__ = "0.0.0+unknown"

from . import colors, offsets
from .cpython.frames import walk_frames as collect_frames
from .cpython.runtime import read_thread_chain
from .dto import (
    FrameInfo,
    NativeFrame,
    NativeThreadInfo,
    ProcessInfo,
    ProfileData,
    SyscallEvent,
    ThreadInfo,
)
from .errors import (
    AttachFailed,
    NoInterpreterState,
    NoThreadState,
    PermissionDenied,
    ProcessExited,
    ProcessNotFound,
    PyProbeError,
    SymbolNotFound,
    UnsupportedArchitecture,
    VersionNotSupported,
)
from .observe.native import collect_native
from .observe.profile import collect_profile
from .observe.sampling import Sampler
from .observe.session import Session as ProcessSession
from .observe.session import open_session
from .observe.session import open_session as resolve_process
from .observe.snapshot import build_thread as collect_thread
from .observe.snapshot import collect_snapshot, is_thread_idle_by_stat
from .observe.syscalls import TraceFilter, collect_syscalls
from .observe.topstats import TopStats
from .present.dumps import (
    dump_native,
    dump_python,
    dump_record,
    dump_syscalls,
    dump_top,
)
from .present.jsonout import (
    format_native_json,
    format_process_json,
    format_syscalls_json,
)
from .present.text import (
    SyscallStat,
    format_folded,
    format_native,
    format_process,
    format_summary,
)
from .target.layout import DEFAULT_VERSION, resolve_layout


def collect_python(pid):
    """Collect Python stack data from a running process (API2 组合).

    Returns ``(ProcessInfo, list[ThreadInfo])``; raises a ``PyProbeError``
    subclass on failure. Composition of :func:`open_session` +
    :func:`collect_snapshot` — callers needing the session (e.g. its
    ``version_warning``) use :func:`open_session` directly.
    """
    session = open_session(pid)
    return session.proc_info, collect_snapshot(session)


__all__ = [  # noqa: RUF022 — semantic section grouping, not alphabetical
    # data types (dto.py, ADR A5)
    "FrameInfo", "ThreadInfo", "ProcessInfo",
    "NativeFrame", "NativeThreadInfo",
    "SyscallEvent", "ProfileData",
    # exceptions (errors.py)
    "PyProbeError", "ProcessNotFound", "PermissionDenied", "SymbolNotFound",
    "NoInterpreterState", "NoThreadState", "VersionNotSupported", "AttachFailed",
    "UnsupportedArchitecture", "ProcessExited",
    # python stack API
    "collect_python", "format_process", "dump_python",
    "collect_frames", "collect_thread", "format_process_json",
    "read_thread_chain", "is_thread_idle_by_stat",
    # process session API (open_session/Session in observe/session.py)
    "ProcessSession", "resolve_process",
    # layout constants + ADR A1 addition (§10.5-2 显式例外)
    "DEFAULT_VERSION", "resolve_layout",
    # native stack API
    "collect_native", "format_native", "dump_native",
    "format_native_json",
    # syscall tracing API
    "collect_syscalls", "dump_syscalls", "format_summary",
    "format_syscalls_json",
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
