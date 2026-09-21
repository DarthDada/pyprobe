"""Process-lifetime-invariant resolution — shared by ``collect_python`` and ``Sampler``.

``stack_dump.collect_python`` and ``sampler.Sampler.__init__`` used to each
re-do the same ~40 lines of process resolution (TODO §8.1): exe readlink,
``_PyRuntime`` symbol lookup, ``Py_Version`` read + decode, ``offsets.configure``,
interpreter-address resolution (main → head fallback), trampoline address
(3.12 only), cmdline metadata, and the thread-name map (``threading._active``).
The two copies had already drifted: the ``stack`` path warned on stderr for
unknown CPython versions while the ``record``/``top`` path silently fell back.

This module factors that resolution into :func:`resolve_process`, returning a
:class:`ProcessSession` that carries both the resolved state and the optional
``version_warning`` string.  No printing happens here — the *dump* layer
(``dump_python`` / ``dump_record`` / ``dump_top``) surfaces the warning, per
the collect/format/dump contract (TODO §8.5).
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional

from .elf import find_symbol, read_const
from .errors import (
    NoInterpreterState, ProcessNotFound, SymbolNotFound, VersionNotSupported,
)
from . import offsets
from .procmeta import decode_py_version, read_cmdline
from .thread_names import get_thread_names
from .types import ProcessInfo


@dataclass
class ProcessSession:
    """Everything that cannot change while the target process lives.

    Constructed once by :func:`resolve_process`; reused by ``Sampler.sample()``
    on every iteration and by ``collect_python`` for one-shot dumps.  Carries
    the optional ``version_warning`` so the dump layer can surface it without
    the collect layer having to print (TODO §8.1, §8.5).
    """

    pid: int
    exe_path: str
    runtime_addr: int
    interp_addr: int
    trampoline_addr: int
    proc_info: ProcessInfo
    names: Dict[int, str] = field(default_factory=dict)
    version_warning: Optional[str] = None


def resolve_process(pid: int, *, reader_factory=None) -> ProcessSession:
    """Resolve all process-lifetime-invariant data for ``pid``.

    Walks: ``/proc/<pid>/exe`` → ``find_symbol(_PyRuntime)`` →
    ``read_const(Py_Version)`` → ``offsets.configure`` (catches
    :class:`VersionNotSupported` and converts it to a warning string) →
    interpreter-address resolution (main → head fallback) → trampoline
    address (3.12 only, ``get_or``-guarded) → :func:`get_thread_names`.

    ``reader_factory`` defaults to :class:`pyprobe.memory.RemoteReader`;
    tests inject a ``FakeReader``-based factory.  The reader built here is
    used only for the static resolution fields; ``Sampler.sample()`` and
    ``collect_python`` build a fresh reader for thread-chain reads (page
    cache = single-snapshot semantics).
    """
    if reader_factory is None:
        from .memory import RemoteReader
        reader_factory = RemoteReader

    try:
        exe_path = os.readlink(f"/proc/{pid}/exe")
    except OSError as e:
        raise ProcessNotFound(pid) from e

    runtime_addr = find_symbol(exe_path, "_PyRuntime", pid)
    if runtime_addr == 0:
        raise SymbolNotFound("_PyRuntime", exe_path)

    version_str = "?"
    py_version = read_const(exe_path, "Py_Version", 8)
    if py_version is not None:
        version_str = decode_py_version(int.from_bytes(py_version, "little"))

    warning = _configure_offsets(version_str)

    cmdline = read_cmdline(pid) or exe_path
    proc_info = ProcessInfo(
        pid=pid, cmdline=cmdline, exe_path=exe_path,
        python_version=version_str,
    )

    reader = reader_factory(pid)
    interp_addr = reader.read_ptr(
        runtime_addr + offsets.get("RuntimeState.interpreters")
        + offsets.get("pyinterpreters.main"))
    if interp_addr is None or interp_addr == 0:
        interp_addr = reader.read_ptr(
            runtime_addr + offsets.get("RuntimeState.interpreters")
            + offsets.get("pyinterpreters.head"))
    if interp_addr is None or interp_addr == 0:
        raise NoInterpreterState()

    # The interpreter trampoline only exists in 3.12 (introduced there,
    # removed in 3.13); when absent there are no trampoline frames to skip.
    trampoline_addr = 0
    tramp_off = offsets.get_or("InterpreterState.interpreter_trampoline")
    if tramp_off is not None:
        addr = reader.read_ptr(interp_addr + tramp_off)
        if addr is not None:
            trampoline_addr = addr

    names = get_thread_names(reader, interp_addr)

    return ProcessSession(
        pid=pid, exe_path=exe_path, runtime_addr=runtime_addr,
        interp_addr=interp_addr, trampoline_addr=trampoline_addr,
        proc_info=proc_info, names=names, version_warning=warning,
    )


def _configure_offsets(version_str: str) -> Optional[str]:
    """Run ``offsets.configure`` and translate the unverified-version case
    into a warning string (no printing, no exception escapes).

    Returns ``None`` for verified versions.  For an unreadable ``Py_Version``
    (``version_str == "?"``) the default offsets are configured directly and
    a distinct "cannot determine" warning is returned.  For a readable but
    unverified version, ``offsets.configure`` raises
    :class:`VersionNotSupported` *after* populating ``_active`` with the
    fallback — we catch it and stringify.
    """
    if version_str == "?":
        offsets.configure(offsets.DEFAULT_VERSION)
        return ("[!] Warning: cannot determine target CPython version, "
                "using default offsets — output may be incorrect.")
    try:
        offsets.configure(version_str)
        return None
    except VersionNotSupported as exc:
        return f"[!] {exc}"
