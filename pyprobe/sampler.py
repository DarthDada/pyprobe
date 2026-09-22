"""Sampling engine: one-time resolution + periodic lightweight sampling.

``stack_dump.collect_python`` and ``Sampler.__init__`` used to each redo
the same expensive, process-lifetime-invariant work (ELF symbol scan ×2,
``offsets.configure``, warning handling).  That resolution now lives in
:mod:`pyprobe.process` (``resolve_process`` → :class:`ProcessSession`),
and the ``Sampler`` simply stores the session.  The split is:

* ``__init__`` — call :func:`resolve_process` once; everything that
  cannot change while the target lives (exe path, ``_PyRuntime`` address,
  CPython version + offsets table, main interpreter address, trampoline
  address (3.12 only), cmdline metadata, thread-name map) is on the
  session.
* ``sample()`` — the hot path: a fresh ``RemoteReader`` (page cache is a
  single-dump snapshot; reuse across samples would serve stale data),
  the thread chain, idle pruning via ``/proc`` stat state, and the frame
  walk for active threads only.

``record`` and ``top`` both build on this engine.  ``session.version_warning``
is surfaced by ``dump_record`` / ``dump_top`` (the dump layer), per the
collect/format/dump contract (TODO §8.1, §8.5).
"""


from .errors import NoThreadState, ProcessExited
from .memory import RemoteReader
from .process import ProcessSession, resolve_process
from .stack_dump import (
    collect_thread,
    is_thread_idle_by_stat,
    read_thread_chain,
)
from .thread_names import get_thread_names
from .types import ThreadInfo


class Sampler:
    """Periodic Python-stack sampler for a running CPython process.

    Constructing a ``Sampler`` performs all process-lifetime-invariant
    resolution once via :func:`resolve_process`; ``sample()`` is then
    cheap enough to call tens of times per second.

    ``reader_factory`` is an injection point for tests (any callable
    returning an object with ``read``/``read_ptr`` semantics, e.g.
    ``FakeReader``); production code uses :class:`RemoteReader`.
    """

    def __init__(self, pid: int, *, reader_factory=RemoteReader):
        self.pid = pid
        self._reader_factory = reader_factory
        # Shared resolution path with ``collect_python`` (TODO §8.1) — no
        # more duplicated ELF / offsets / interp-addr logic here.
        self.session: ProcessSession = resolve_process(
            pid, reader_factory=reader_factory)

        # Backward-compat attributes: existing tests and callers read
        # interp_addr / trampoline_addr / proc_info / _names directly.
        self.proc_info = self.session.proc_info
        self.interp_addr = self.session.interp_addr
        self.trampoline_addr = self.session.trampoline_addr
        self._names = self.session.names

    def sample(self) -> list[ThreadInfo]:
        """Take one snapshot of all threads. Raises ``ProcessExited``.

        Each call uses a fresh reader: the page-level cache implements a
        single-invocation snapshot semantics, and reusing it across
        samples would return stale memory.
        """
        reader = self._reader_factory(self.pid)
        try:
            raw = read_thread_chain(reader, self.interp_addr)
        except NoThreadState as e:
            raise ProcessExited(self.pid) from e

        threads = []
        for t in raw:
            name = self._names.get(t["thread_id"], "")
            idle = is_thread_idle_by_stat(self.pid, t["native_tid"])
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
        self.session.names = self._names
