"""Sampling engine: one-time resolution + periodic lightweight sampling.

``stack_dump.collect_python`` re-does expensive, process-lifetime-invariant
work on every call (ELF symbol scan ×2, ``offsets.configure``, warning
output for unverified versions).  The ``Sampler`` splits that into:

* ``__init__`` — resolve everything that cannot change while the target
  lives: exe path, ``_PyRuntime`` address, CPython version + offsets
  table, main interpreter address, trampoline address (3.12 only),
  cmdline metadata, and the thread-name map (``threading._active``).
* ``sample()`` — the hot path: a fresh ``RemoteReader`` (page cache is a
  single-dump snapshot; reuse across samples would serve stale data),
  the thread chain, idle pruning via ``/proc`` stat state, and the frame
  walk for active threads only.

``record`` and ``top`` both build on this engine.
"""

import os
from typing import Callable, List

from .memory import RemoteReader
from . import offsets
from .elf import find_symbol, read_const, read_cmdline, decode_py_version
from .thread_names import get_thread_names
from .stack_dump import (
    collect_thread, _read_thread_chain, _is_thread_idle_by_stat,
)
from .types import ThreadInfo, ProcessInfo
from .errors import (
    ProcessNotFound, SymbolNotFound, NoInterpreterState, NoThreadState,
    ProcessExited,
)


class Sampler:
    """Periodic Python-stack sampler for a running CPython process.

    Constructing a ``Sampler`` performs all process-lifetime-invariant
    resolution once (see module docstring); ``sample()`` is then cheap
    enough to call tens of times per second.

    ``reader_factory`` is an injection point for tests (any callable
    returning an object with ``read``/``read_ptr`` semantics, e.g.
    ``FakeReader``); production code uses :class:`RemoteReader`.
    """

    def __init__(self, pid: int, *, reader_factory=RemoteReader):
        self.pid = pid
        self._reader_factory = reader_factory

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
            version_str = decode_py_version(
                int.from_bytes(py_version, "little"))
        if version_str != "?":
            offsets.configure(version_str)
        else:
            offsets.configure(offsets._DEFAULT_VERSION)

        cmdline = read_cmdline(pid) or exe_path
        self.proc_info = ProcessInfo(
            pid=pid, cmdline=cmdline, exe_path=exe_path,
            python_version=version_str,
        )

        bootstrap = reader_factory(pid)
        self.interp_addr = bootstrap.read_ptr(
            runtime_addr + offsets.get("RuntimeState.interpreters")
            + offsets.get("pyinterpreters.main"))
        if self.interp_addr is None or self.interp_addr == 0:
            self.interp_addr = bootstrap.read_ptr(
                runtime_addr + offsets.get("RuntimeState.interpreters")
                + offsets.get("pyinterpreters.head"))
        if self.interp_addr is None or self.interp_addr == 0:
            raise NoInterpreterState()

        # The interpreter trampoline only exists in 3.12 (introduced there,
        # removed in 3.13); when absent there are no trampoline frames to skip.
        self.trampoline_addr = 0
        tramp_off = offsets.get_or("InterpreterState.interpreter_trampoline")
        if tramp_off is not None:
            addr = bootstrap.read_ptr(self.interp_addr + tramp_off)
            if addr is not None:
                self.trampoline_addr = addr

        self._names = get_thread_names(bootstrap, self.interp_addr)

    def sample(self) -> List[ThreadInfo]:
        """Take one snapshot of all threads. Raises ``ProcessExited``.

        Each call uses a fresh reader: the page-level cache implements a
        single-invocation snapshot semantics, and reusing it across
        samples would return stale memory.
        """
        reader = self._reader_factory(self.pid)
        try:
            raw = _read_thread_chain(reader, self.interp_addr)
        except NoThreadState as e:
            raise ProcessExited(self.pid) from e

        threads = []
        for t in raw:
            name = self._names.get(t["thread_id"], "")
            idle = _is_thread_idle_by_stat(self.pid, t["native_tid"])
            threads.append(collect_thread(
                reader, self.pid, t["tstate_addr"], t["native_tid"],
                name, self.trampoline_addr, idle_hint=idle))
        threads.sort(key=lambda t: t.native_tid)
        return threads

    def refresh_names(self) -> None:
        """Re-read the thread-name map from the target.

        Cheap enough for display cadence (once per ``top`` refresh), too
        expensive for the sampling hot loop.  ``record`` never calls it:
        names are part of the folded aggregation key and must stay stable
        for the whole recording.
        """
        reader = self._reader_factory(self.pid)
        self._names = get_thread_names(reader, self.interp_addr)
