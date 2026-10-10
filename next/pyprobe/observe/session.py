"""Observation session — everything invariant while the target lives.

``open_session`` resolves once: exe path, ``_PyRuntime`` address, CPython
version + layout (A1: explicit Layout, no global), interpreter address,
trampoline (3.12 only), cmdline metadata, and the thread-name map. The
version warning for unverified targets is composed here (SE2, 逐字复刻)
and surfaced by the present/CLI layer — never printed at this layer.

The session holds a ``view_factory``, not a view (ADR A4): every
observation builds its own view and thereby declares its consistency
need. ``view_factory`` is the test injection point; production default is
``SnapshotView(Transport(pid))``.

契约（contracts.md 批 4 表 SE1–SE6）。当前为骨架：dataclass 已锁定，
函数返回哨兵，批次 4 实现填实。
"""

from dataclasses import dataclass, field

from .. import dto
from ..cpython.names import get_thread_names
from ..cpython.runtime import resolve_interpreter, resolve_trampoline
from ..errors import SymbolNotFound, VersionNotSupported
from ..kernel.mem import Transport
from ..kernel.procfs import read_cmdline
from ..kernel.views import SnapshotView
from ..target.identity import detect_version, exe_path_of
from ..target.layout import Layout, resolve_layout
from ..target.symbols import find_symbol


@dataclass
class Session:
    """Process-lifetime-invariant state + the means to observe (SE4)."""

    pid: int
    exe_path: str
    runtime_addr: int
    interp_addr: int
    trampoline_addr: int
    layout: Layout
    proc_info: dto.ProcessInfo
    view_factory: object  # callable(pid) -> kernel view (SE5)
    names: dict[int, str] = field(default_factory=dict)
    version_warning: str | None = None


def open_session(pid: int, *, view_factory=None) -> Session:
    """Resolve all process-lifetime-invariant data for ``pid`` (SE1–SE6).

    Raises ``ProcessNotFound`` / ``SymbolNotFound`` /
    ``NoInterpreterState``; never prints.
    """
    if view_factory is None:
        # SE5 — production default declares single-snapshot consistency (A4).
        def view_factory(pid):
            return SnapshotView(Transport(pid))

    # SE1 — exe identity, runtime anchor, version, layout (A1: explicit).
    exe_path = exe_path_of(pid)  # raises ProcessNotFound
    runtime_addr = find_symbol(exe_path, "_PyRuntime", pid)
    if runtime_addr == 0:
        raise SymbolNotFound("_PyRuntime", exe_path)
    version_str = detect_version(exe_path)
    layout = resolve_layout(version_str)
    version_warning = _version_warning(version_str, layout)  # SE2

    # SE3 — cmdline unreadable (zombie window) falls back to the exe path.
    cmdline = read_cmdline(pid) or exe_path
    proc_info = dto.ProcessInfo(
        pid=pid, cmdline=cmdline, exe_path=exe_path,
        python_version=version_str,
    )

    # SE6 — interpreter/trampoline/names graph resolution (R5/R6/N-series).
    view = view_factory(pid)
    interp_addr = resolve_interpreter(view, layout, runtime_addr)
    trampoline_addr = resolve_trampoline(view, layout, interp_addr)
    names = get_thread_names(view, layout, interp_addr)

    return Session(
        pid=pid, exe_path=exe_path, runtime_addr=runtime_addr,
        interp_addr=interp_addr, trampoline_addr=trampoline_addr,
        layout=layout, proc_info=proc_info, view_factory=view_factory,
        names=names, version_warning=version_warning,
    )


def _version_warning(version_str: str, layout: Layout) -> str | None:
    """Compose the version warning string (SE2, 逐字复刻).

    "?" (Py_Version unreadable) gets a dedicated "cannot determine" text;
    a readable but unverified version gets "[!] " + the VersionNotSupported
    message (message text pinned by errors.py E2); verified → None.
    """
    if version_str == "?":
        return ("[!] Warning: cannot determine target CPython version, "
                "using default offsets — output may be incorrect.")
    if not layout.verified:
        return f"[!] {VersionNotSupported(version_str, layout.key)}"
    return None
