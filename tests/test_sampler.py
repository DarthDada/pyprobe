"""Unit tests for pyprobe.sampler — one-time resolution + per-sample loop.

Static resolution (ELF symbols, version, offsets, interpreter/trampoline
addresses, thread names) happens in ``__init__`` and must NOT be repeated
inside ``sample()`` — several tests below guard exactly that (no
offsets.configure, no name refresh, fresh reader per call).
"""

import os

import pytest

from pyprobe import offsets
from pyprobe.sampler import Sampler
from pyprobe.errors import (
    ProcessNotFound, SymbolNotFound, NoInterpreterState, ProcessExited,
)
from tests.helpers import (
    FakeReader, build_frame, build_code_object, build_pyunicode,
    build_pybytes, linetable_no_line,
)

# Static addresses used by the fake process layout.
RUNTIME = 0x100000
INTERP = 0x200000
TRAMPOLINE = 0x7777

# Layout constants (3.12 table; conftest configures it for every test)
CODE_ADAPTIVE = offsets.get("CodeObject.co_code_adaptive")


def _add_thread(reader, addr, next_addr, thread_id, native_tid,
                frame_spec=None):
    """Write a tstate (chain fields) and optionally a frame chain.

    ``frame_spec`` is ``(func_name, filename, firstlineno)`` or None for a
    thread with no Python frames.
    """
    # _read_thread_chain reads [next, native_thread_id] in one contiguous
    # span — the tstate chain fields must live in one FakeReader region.
    ts_lo = min(offsets.get("ThreadState.next"),
                offsets.get("ThreadState.native_thread_id"))
    ts_hi = max(offsets.get("ThreadState.next"),
                offsets.get("ThreadState.native_thread_id")) + 8
    blob = bytearray(ts_hi)
    import struct as _s
    _s.pack_into("<Q", blob, offsets.get("ThreadState.next"), next_addr)
    _s.pack_into("<Q", blob, offsets.get("ThreadState.thread_id"), thread_id)
    _s.pack_into("<Q", blob, offsets.get("ThreadState.native_thread_id"),
                 native_tid)
    if frame_spec is not None:
        # cframe sits inside the chain span; set it in the same blob so the
        # later add_ptr does not create an overlapping (shadowed) region.
        _s.pack_into("<Q", blob, offsets.get("ThreadState.cframe"),
                     addr + 0x800)
    reader.add(addr + ts_lo, bytes(blob[ts_lo:]))
    if frame_spec is None:
        return
    name, filename, firstlineno = frame_spec
    cframe = addr + 0x800
    frame = addr + 0x1000
    code = addr + 0x2000
    name_addr = addr + 0x3000
    filename_addr = addr + 0x4000
    lt_addr = addr + 0x5000

    reader.add_ptr(cframe, frame)  # CFrame.current_frame
    build_pyunicode(reader, name_addr, name)
    build_pyunicode(reader, filename_addr, filename)
    build_pybytes(reader, lt_addr, linetable_no_line(4))
    build_code_object(reader, code, name_addr, filename_addr,
                      firstlineno, lt_addr)
    build_frame(reader, frame, code, 0, code + CODE_ADAPTIVE)


def _build_layout(reader, threads):
    """Write runtime/interp/trampoline + thread chain into ``reader``.

    ``threads`` is a list of dicts with keys addr/thread_id/native_tid/
    frame_spec (see ``_add_thread``).  The chain is linked in list order.
    """
    reader.add_ptr(
        RUNTIME + offsets.get("RuntimeState.interpreters")
        + offsets.get("pyinterpreters.main"), INTERP)
    tramp_off = offsets.get_or("InterpreterState.interpreter_trampoline")
    if tramp_off is not None:
        reader.add_ptr(INTERP + tramp_off, TRAMPOLINE)
    head = threads[0]["addr"] if threads else 0
    reader.add_ptr(
        INTERP + offsets.get("InterpreterState.threads")
        + offsets.get("pythreads.head"), head)
    for i, t in enumerate(threads):
        nxt = threads[i + 1]["addr"] if i + 1 < len(threads) else 0
        _add_thread(reader, t["addr"], nxt, t["thread_id"],
                    t["native_tid"], t.get("frame_spec"))


def _default_threads():
    return [
        {"addr": 0x30000, "thread_id": 1001, "native_tid": 2001,
         "frame_spec": ("burn", "spin.py", 8)},
        {"addr": 0x40000, "thread_id": 1002, "native_tid": 2002,
         "frame_spec": ("main", "app.py", 20)},
    ]


@pytest.fixture
def static_env(monkeypatch):
    """Stub all out-of-process resolution used by Sampler.__init__.

    ``resolve_process`` now lives in ``pyprobe.process`` (TODO §8.1) —
    patches target that module's namespace, not ``pyprobe.sampler``.
    """
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/bin/python3.12")
    monkeypatch.setattr("pyprobe.process.find_symbol",
                        lambda exe, sym, pid: RUNTIME)
    # Py_Version 3.12.13 → 0x030C0D00
    monkeypatch.setattr(
        "pyprobe.process.read_const",
        lambda exe, sym, length: (0x030C0D00).to_bytes(8, "little"))
    monkeypatch.setattr("pyprobe.process.read_cmdline",
                        lambda pid: "python3 app.py")
    monkeypatch.setattr("pyprobe.process.get_thread_names",
                        lambda reader, interp: {})


def _make_factory(threads=None):
    """Reader factory serving a fresh FakeReader per call."""
    threads = _default_threads() if threads is None else threads

    def factory(pid):
        reader = FakeReader()
        _build_layout(reader, threads)
        return reader

    return factory


class TestInit:
    def test_resolves_static_addrs(self, static_env):
        s = Sampler(1234, reader_factory=_make_factory())
        assert s.pid == 1234
        assert s.interp_addr == INTERP
        assert s.trampoline_addr == TRAMPOLINE
        assert s.proc_info.pid == 1234
        assert s.proc_info.cmdline == "python3 app.py"
        assert s.proc_info.exe_path == "/usr/bin/python3.12"
        assert s.proc_info.python_version == "3.12.13"

    def test_process_not_found(self, monkeypatch):
        def boom(path):
            raise OSError("no such process")
        monkeypatch.setattr(os, "readlink", boom)
        with pytest.raises(ProcessNotFound):
            Sampler(999, reader_factory=_make_factory())

    def test_symbol_not_found(self, monkeypatch):
        monkeypatch.setattr(os, "readlink", lambda path: "/usr/bin/python3")
        monkeypatch.setattr("pyprobe.process.find_symbol",
                            lambda exe, sym, pid: 0)
        with pytest.raises(SymbolNotFound):
            Sampler(999, reader_factory=_make_factory())

    def test_no_interpreter_state(self, static_env):
        with pytest.raises(NoInterpreterState):
            Sampler(999, reader_factory=lambda pid: FakeReader())

    def test_unverified_version_warns_exactly_once(
            self, static_env, monkeypatch, capsys):
        # ``resolve_process`` captures the unverified-version warning on the
        # ProcessSession and does NOT print at the collect layer (TODO §8.5).
        # ``sample()`` must not re-warn either — the warning is static.
        monkeypatch.setattr(
            "pyprobe.process.read_const",
            lambda exe, sym, length: (0x09090000).to_bytes(8, "little"))
        s = Sampler(1, reader_factory=_make_factory())
        assert s.session.version_warning is not None
        assert "9.9" in s.session.version_warning
        first = capsys.readouterr()
        assert first.err == ""  # collect/init layer must not print

        for _ in range(3):
            s.sample()
        again = capsys.readouterr()
        assert again.err == ""  # sample() must not re-warn


class TestSample:
    def test_returns_thread_infos(self, static_env, monkeypatch):
        # ``resolve_process`` (in pyprobe.process) reads names at init time.
        monkeypatch.setattr("pyprobe.process.get_thread_names",
                            lambda reader, interp: {1001: "spin-worker"})
        s = Sampler(1, reader_factory=_make_factory())
        threads = s.sample()
        assert len(threads) == 2
        # sorted by native_tid
        assert [t.native_tid for t in threads] == [2001, 2002]
        assert threads[0].name == "spin-worker"
        assert threads[0].frames[0].name == "burn"
        assert threads[1].name == ""
        assert threads[1].frames[0].name == "main"

    def test_prunes_idle_threads(self, static_env, monkeypatch):
        monkeypatch.setattr(
            "pyprobe.sampler.is_thread_idle_by_stat",
            lambda pid, tid: tid == 2001)
        s = Sampler(1, reader_factory=_make_factory())
        threads = s.sample()
        pruned, active = threads[0], threads[1]
        assert pruned.native_tid == 2001
        assert pruned.frames == []
        assert pruned.idle is True
        assert active.native_tid == 2002
        assert len(active.frames) == 1
        assert active.idle is False

    def test_new_reader_per_call(self, static_env):
        calls = []
        inner = _make_factory()

        def factory(pid):
            calls.append(pid)
            return inner(pid)

        s = Sampler(1, reader_factory=factory)
        assert len(calls) == 1  # bootstrap only
        s.sample()
        s.sample()
        assert len(calls) == 3  # one fresh reader per sample

    def test_process_exited(self, static_env):
        s = Sampler(1, reader_factory=_make_factory())
        s._reader_factory = lambda pid: FakeReader()  # memory gone
        with pytest.raises(ProcessExited):
            s.sample()

    def test_names_not_refreshed_in_sample(self, static_env, monkeypatch):
        counter = {"n": 0}

        def counting(reader, interp):
            counter["n"] += 1
            return {}

        # Counts the call from resolve_process at init; sample() must not
        # re-invoke get_thread_names (the hot loop uses self._names only).
        monkeypatch.setattr("pyprobe.process.get_thread_names", counting)
        s = Sampler(1, reader_factory=_make_factory())
        assert counter["n"] == 1
        for _ in range(3):
            s.sample()
        assert counter["n"] == 1  # hot loop must not read names

    def test_refresh_names(self, static_env, monkeypatch):
        state = {"names": {1001: "old"}}

        def fake_names(reader, interp):
            return state["names"]

        # Patch both namespaces: resolve_process (init) and Sampler's
        # own refresh_names (which imports get_thread_names directly).
        monkeypatch.setattr("pyprobe.process.get_thread_names", fake_names)
        monkeypatch.setattr("pyprobe.sampler.get_thread_names", fake_names)
        s = Sampler(1, reader_factory=_make_factory())
        assert s.sample()[0].name == "old"

        state["names"] = {1001: "renamed"}
        s.refresh_names()
        assert s.sample()[0].name == "renamed"

    def test_offsets_not_reconfigured_in_sample(
            self, static_env, monkeypatch):
        from pyprobe import offsets as offsets_mod
        calls = []
        real = offsets_mod.configure

        def counting(version_str):
            calls.append(version_str)
            real(version_str)

        monkeypatch.setattr(offsets_mod, "configure", counting)
        s = Sampler(1, reader_factory=_make_factory())
        n_init = len(calls)
        assert n_init >= 1
        for _ in range(3):
            s.sample()
        assert len(calls) == n_init  # sample() never reconfigures

    def test_empty_thread_chain_returns_empty(self, static_env):
        s = Sampler(1, reader_factory=_make_factory(threads=[]))
        assert s.sample() == []
