"""Unit tests for pyprobe.stack_dump — frame walking, idle detection, formatting."""

import os
import struct
import sys

import pytest

from pyprobe import offsets
from pyprobe.stack_dump import (
    collect_frames, collect_thread,
    _is_thread_idle, is_thread_idle_by_stat, _is_thread_idle_by_frames,
    format_process, dump_python, collect_python,
    MAX_FRAMES,
)
from pyprobe.types import FrameInfo, ThreadInfo, ProcessInfo
from pyprobe.errors import ProcessNotFound, SymbolNotFound
from tests.helpers import (
    FakeReader, build_frame, build_code_object, build_pyunicode, build_pybytes,
    linetable_no_line,
)

# Layout constants
CODE_ADAPTIVE = 192  # offsets.get("CodeObject.co_code_adaptive")


def _make_simple_frame(reader, frame_addr, code_addr, name, filename,
                        firstlineno=10, previous=0, lasti=0):
    """Build a complete frame + code + strings in the reader."""
    name_addr = code_addr + 0x1000
    filename_addr = code_addr + 0x2000
    lt_addr = code_addr + 0x3000

    build_pyunicode(reader, name_addr, name)
    build_pyunicode(reader, filename_addr, filename)
    build_pybytes(reader, lt_addr, linetable_no_line(4))
    build_code_object(reader, code_addr, name_addr, filename_addr,
                      firstlineno, lt_addr)

    prev_instr = code_addr + CODE_ADAPTIVE + lasti
    build_frame(reader, frame_addr, code_addr, previous, prev_instr)


class TestCollectFrames:
    def test_null_frame_addr(self):
        reader = FakeReader()
        assert collect_frames(reader, 0, 0) == []

    def test_unreadable_frame(self):
        reader = FakeReader()
        assert collect_frames(reader, 0x1000, 0) == []

    def test_single_frame(self):
        reader = FakeReader()
        _make_simple_frame(reader, 0x1000, 0x2000, "foo", "bar.py",
                           firstlineno=10, previous=0)
        frames = collect_frames(reader, 0x1000, 0)
        assert len(frames) == 1
        assert isinstance(frames[0], FrameInfo)
        assert frames[0].name == "foo"
        assert frames[0].filename == "bar.py"
        assert frames[0].line == 10  # linetable_no_line → firstlineno

    def test_two_frame_chain(self):
        reader = FakeReader()
        _make_simple_frame(reader, 0x1000, 0x2000, "inner", "a.py",
                           firstlineno=5, previous=0x5000)
        _make_simple_frame(reader, 0x5000, 0x6000, "outer", "b.py",
                           firstlineno=20, previous=0)
        frames = collect_frames(reader, 0x1000, 0)
        assert len(frames) == 2
        assert frames[0].name == "inner"
        assert frames[1].name == "outer"

    def test_skip_null_code(self):
        """Frame with f_code=0 is skipped, chain follows previous."""
        reader = FakeReader()
        # Frame 0: null code, previous → frame 1
        build_frame(reader, 0x1000, 0, 0x5000, 0)
        _make_simple_frame(reader, 0x5000, 0x6000, "real", "x.py",
                           firstlineno=1, previous=0)
        frames = collect_frames(reader, 0x1000, 0)
        assert len(frames) == 1
        assert frames[0].name == "real"

    def test_skip_trampoline(self):
        """Frame whose f_code == trampoline_addr is skipped."""
        reader = FakeReader()
        trampoline = 0x9999
        # Frame 0: f_code = trampoline, previous → frame 1
        build_frame(reader, 0x1000, trampoline, 0x5000, 0)
        _make_simple_frame(reader, 0x5000, 0x6000, "real", "x.py",
                           firstlineno=1, previous=0)
        frames = collect_frames(reader, 0x1000, trampoline)
        assert len(frames) == 1
        assert frames[0].name == "real"

    def test_trampoline_zero_does_not_skip(self):
        """trampoline_addr=0 must not cause all frames to be skipped."""
        reader = FakeReader()
        _make_simple_frame(reader, 0x1000, 0x2000, "foo", "bar.py", previous=0)
        frames = collect_frames(reader, 0x1000, 0)
        assert len(frames) == 1

    def test_circular_chain_hits_max(self):
        reader = FakeReader()
        # Self-referencing frame
        _make_simple_frame(reader, 0x1000, 0x2000, "loop", "l.py",
                           firstlineno=1, previous=0x1000)
        frames = collect_frames(reader, 0x1000, 0)
        assert len(frames) == MAX_FRAMES

    def test_none_name_when_code_addr_zero_in_string(self):
        """If co_name pointer is 0, name should be None."""
        reader = FakeReader()
        lt_addr = 0x3000
        build_pybytes(reader, lt_addr, linetable_no_line(4))
        build_pyunicode(reader, 0x4000, "foo.py")
        build_code_object(reader, 0x2000, 0, 0x4000, 10, lt_addr)
        build_frame(reader, 0x1000, 0x2000, 0,
                    0x2000 + CODE_ADAPTIVE)
        frames = collect_frames(reader, 0x1000, 0)
        assert len(frames) == 1
        assert frames[0].name is None
        assert frames[0].filename == "foo.py"

    def test_stale_tail_frame_filtered(self):
        """A frame whose code reads as garbage (name and filename both
        unreadable) terminates the chain instead of being collected.

        Seen on 3.13: the frame chain can end with a stale datastack entry
        whose f_executable points at recycled memory.
        """
        reader = FakeReader()
        lt_addr = 0x3000
        build_pybytes(reader, lt_addr, linetable_no_line(4))
        # name/filename point at addresses that were never written
        build_code_object(reader, 0x2000, 0x4000, 0x5000, 10, lt_addr)
        build_frame(reader, 0x1000, 0x2000, 0, 0x2000 + CODE_ADAPTIVE)
        frames = collect_frames(reader, 0x1000, 0)
        assert frames == []


class TestIsThreadIdleByFrames:
    def test_empty_frames(self):
        assert _is_thread_idle_by_frames([]) is False

    def test_wait_threading(self):
        frames = [FrameInfo("wait", "/path/to/threading.py", 100)]
        assert _is_thread_idle_by_frames(frames) is True

    def test_wait_wrong_file(self):
        frames = [FrameInfo("wait", "/path/to/other.py", 100)]
        assert _is_thread_idle_by_frames(frames) is False

    def test_select_selectors(self):
        frames = [FrameInfo("select", "/path/to/selectors.py", 50)]
        assert _is_thread_idle_by_frames(frames) is True

    def test_poll_asyncore(self):
        frames = [FrameInfo("poll", "/path/to/asyncore.py", 30)]
        assert _is_thread_idle_by_frames(frames) is True

    def test_poll_zmq(self):
        frames = [FrameInfo("poll", "/x/zmq/socket.py", 1)]
        assert _is_thread_idle_by_frames(frames) is True

    def test_poll_gevent(self):
        frames = [FrameInfo("poll", "/x/gevent/core.py", 1)]
        assert _is_thread_idle_by_frames(frames) is True

    def test_poll_tornado(self):
        frames = [FrameInfo("poll", "/x/tornado/ioloop.py", 1)]
        assert _is_thread_idle_by_frames(frames) is True

    def test_poll_wrong_name(self):
        frames = [FrameInfo("recv", "/path/to/socket.py", 1)]
        assert _is_thread_idle_by_frames(frames) is False

    def test_none_filename(self):
        frames = [FrameInfo("wait", None, 1)]
        assert _is_thread_idle_by_frames(frames) is False

    def test_busy_function(self):
        frames = [FrameInfo("compute", "/app/worker.py", 42)]
        assert _is_thread_idle_by_frames(frames) is False

    def test_second_frame_not_checked(self):
        """Only the top (first) frame is used for idle detection."""
        frames = [FrameInfo("compute", "/app/worker.py", 1),
                  FrameInfo("wait", "/threading.py", 100)]
        assert _is_thread_idle_by_frames(frames) is False


class TestIsThreadIdleByStat:
    def test_self_running(self):
        """The current thread should be in 'R' state → not idle."""
        assert is_thread_idle_by_stat(os.getpid(), os.getpid()) is False

    def test_nonexistent_tid(self):
        """A nonexistent TID → OSError → returns False (conservative)."""
        assert is_thread_idle_by_stat(os.getpid(), 0xFFFFFFF) is False


class TestCollectThread:
    def test_build_threadinfo(self):
        reader = FakeReader()
        tstate_addr = 0x10000
        cframe_addr = 0x20000
        frame_addr = 0x30000
        code_addr = 0x40000

        # tstate.cframe → cframe_addr
        reader.add_ptr(tstate_addr + offsets.get("ThreadState.cframe"), cframe_addr)
        # cframe.current_frame → frame_addr
        reader.add_ptr(cframe_addr, frame_addr)
        _make_simple_frame(reader, frame_addr, code_addr, "worker", "w.py",
                           firstlineno=5, previous=0)

        ti = collect_thread(reader, 0, tstate_addr, 999, "worker", 0)
        assert isinstance(ti, ThreadInfo)
        assert ti.native_tid == 999
        assert ti.name == "worker"
        assert len(ti.frames) == 1
        assert ti.frames[0].name == "worker"

    def test_null_cframe(self):
        """If cframe is 0, no frames are collected."""
        reader = FakeReader()
        tstate_addr = 0x10000
        reader.add_ptr(tstate_addr + offsets.get("ThreadState.cframe"), 0)
        ti = collect_thread(reader, 0, tstate_addr, 100, "", 0)
        assert ti.frames == []

    def test_idle_hint_skips_frame_walk(self):
        """idle_hint=True: no frame traversal, empty frames + idle=True.

        The reader has no frame memory at all — a full walk would return
        no frames anyway, but the point is the tstate/cframe fields are
        never read (pruning happens before any address dereference).
        """
        reader = FakeReader()
        tstate_addr = 0x10000
        ti = collect_thread(reader, 0, tstate_addr, 100, "w", 0,
                            idle_hint=True)
        assert isinstance(ti, ThreadInfo)
        assert ti.native_tid == 100
        assert ti.name == "w"
        assert ti.frames == []
        assert ti.idle is True

    def test_idle_hint_false_walks_frames(self):
        """idle_hint=False (default): full walk — original behavior."""
        reader = FakeReader()
        tstate_addr = 0x10000
        cframe_addr = 0x20000
        frame_addr = 0x30000
        code_addr = 0x40000
        reader.add_ptr(tstate_addr + offsets.get("ThreadState.cframe"), cframe_addr)
        reader.add_ptr(cframe_addr, frame_addr)
        _make_simple_frame(reader, frame_addr, code_addr, "worker", "w.py",
                           firstlineno=5, previous=0)
        ti = collect_thread(reader, 0, tstate_addr, 100, "worker", 0,
                            idle_hint=False)
        assert len(ti.frames) == 1


class TestFormatProcess:
    def test_basic(self):
        proc = ProcessInfo(pid=123, cmdline="python app.py",
                           exe_path="/usr/bin/python3", python_version="3.12.1")
        threads = [
            ThreadInfo(native_tid=123, name="MainThread", idle=True,
                       frames=[FrameInfo("main", "app.py", 10)]),
            ThreadInfo(native_tid=124, name="worker", idle=False,
                       frames=[FrameInfo("run", "t.py", 5)]),
        ]
        out = format_process(proc, threads)
        assert "Process 123: python app.py" in out
        assert "Python v3.12.1 (/usr/bin/python3)" in out
        assert 'Thread 123 (idle): "MainThread"' in out
        assert 'Thread 124: "worker"' in out
        assert "#0 main (app.py:10)" in out
        assert "#0 run (t.py:5)" in out

    def test_no_frames(self):
        proc = ProcessInfo(pid=1, cmdline="x", exe_path="/x")
        threads = [ThreadInfo(native_tid=1)]
        out = format_process(proc, threads)
        assert "no Python frame" in out

    def test_empty_threads(self):
        proc = ProcessInfo(pid=1, cmdline="x", exe_path="/x")
        out = format_process(proc, [])
        assert "Process 1" in out

    def test_color(self):
        proc = ProcessInfo(pid=123, cmdline="python app.py",
                           exe_path="/usr/bin/python3", python_version="3.12.1")
        threads = [ThreadInfo(native_tid=123, name="MainThread",
                              frames=[FrameInfo("main", "app.py", 10)])]
        out = format_process(proc, threads, color=True)
        assert "Process \x1b[1m\x1b[33m123\x1b[0m: python app.py" in out
        assert "Thread \x1b[1m\x1b[33m123\x1b[0m" in out
        assert "#0 \x1b[32mmain\x1b[0m (\x1b[36mapp.py\x1b[0m:\x1b[2m10\x1b[0m)" in out

    def test_verbose_keeps_full_paths(self):
        proc = ProcessInfo(pid=123, cmdline="python app.py",
                           exe_path="/usr/bin/python3", python_version="3.12.1")
        threads = [ThreadInfo(native_tid=123, name="MainThread",
                              frames=[FrameInfo("run", "/a/b/c/server.py", 86)])]
        out = format_process(proc, threads, verbose=True)
        assert "#0 run (/a/b/c/server.py:86)" in out

    def test_default_shortens_long_paths(self):
        proc = ProcessInfo(pid=123, cmdline="python app.py",
                           exe_path="/usr/bin/python3", python_version="3.12.1")
        threads = [ThreadInfo(native_tid=123, name="MainThread",
                              frames=[FrameInfo("run", "/a/b/c/server.py", 86)])]
        out = format_process(proc, threads)
        assert "#0 run (c/server.py:86)" in out
        assert "/a/b/c/server.py" not in out


class TestCollectPythonErrors:
    def test_process_not_found(self):
        with pytest.raises(ProcessNotFound):
            collect_python(0xFFFFFFF)

    def test_symbol_not_found(self, monkeypatch):
        """Mock find_symbol to return 0 → SymbolNotFound.

        ``resolve_process`` lives in ``pyprobe.process`` (TODO §8.1) —
        patches target that module's namespace.
        """
        monkeypatch.setattr("pyprobe.process.find_symbol", lambda *a, **k: 0)
        monkeypatch.setattr("os.readlink", lambda p: "/fake/python3.12")
        with pytest.raises(SymbolNotFound):
            collect_python(12345)


class TestDumpPythonCli:
    def test_process_not_found_returns_1(self, capsys):
        rc = dump_python(0xFFFFFFF)
        assert rc == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower() or "not found" in captured.out.lower()

    def _fake_collect(self, monkeypatch):
        """Stub resolve_process + _collect_threads_from_session.

        ``dump_python`` no longer routes through ``collect_python`` (it
        needs the ``ProcessSession.version_warning``); the two helpers it
        actually calls are patched instead (TODO §8.1).
        """
        from pyprobe.process import ProcessSession
        proc = ProcessInfo(pid=123, cmdline="python app.py",
                           exe_path="/usr/bin/python3", python_version="3.12.1")
        threads = [ThreadInfo(native_tid=123, name="MainThread",
                              frames=[FrameInfo("main", "app.py", 10)])]
        session = ProcessSession(
            pid=123, exe_path=proc.exe_path, runtime_addr=0,
            interp_addr=0, trampoline_addr=0, proc_info=proc, names={})
        monkeypatch.setattr("pyprobe.stack_dump.resolve_process",
                            lambda pid, **kw: session)
        monkeypatch.setattr("pyprobe.stack_dump._collect_threads_from_session",
                            lambda session: threads)

    def test_no_color_when_not_a_tty(self, monkeypatch, capsys):
        """I3: default (color=None) must emit plain text when stdout is not a tty."""
        self._fake_collect(monkeypatch)
        rc = dump_python(123)
        assert rc == 0
        captured = capsys.readouterr()
        assert "\x1b" not in captured.out

    def test_color_true_prints_ansi(self, monkeypatch, capsys):
        self._fake_collect(monkeypatch)
        rc = dump_python(123, color=True)
        assert rc == 0
        captured = capsys.readouterr()
        assert "\x1b[32m" in captured.out
        assert "\x1b[1m\x1b[33m123\x1b[0m" in captured.out

    def test_color_false_no_ansi_even_on_error(self, capsys):
        rc = dump_python(0xFFFFFFF, color=False)
        assert rc == 1
        captured = capsys.readouterr()
        assert "\x1b" not in captured.err

    def test_color_true_error_is_red(self, capsys):
        rc = dump_python(0xFFFFFFF, color=True)
        assert rc == 1
        captured = capsys.readouterr()
        assert "\x1b[31m[!]" in captured.err


class TestDumpNativeCli:
    def _stub_native(self, monkeypatch, exc):
        monkeypatch.setattr("pyprobe.native_dump._init_libs", lambda: None)
        monkeypatch.setattr("pyprobe.native_dump.read_cmdline", lambda pid: "python3 x.py")
        def fake_collect(pid):
            raise exc
        monkeypatch.setattr("pyprobe.native_dump.collect_native", fake_collect)

    def test_error_goes_to_stderr_with_red(self, monkeypatch, capsys):
        from pyprobe.errors import AttachFailed
        self._stub_native(monkeypatch, AttachFailed("attach denied"))
        from pyprobe.native_dump import dump_native
        rc = dump_native(999, color=True)
        assert rc == 1
        captured = capsys.readouterr()
        assert "\x1b[31m[!]" in captured.err
        assert captured.out == (
            "Process \x1b[1m\x1b[33m999\x1b[0m: python3 x.py\n\n")

    def test_error_plain_when_not_a_tty(self, monkeypatch, capsys):
        from pyprobe.errors import AttachFailed
        self._stub_native(monkeypatch, AttachFailed("attach denied"))
        from pyprobe.native_dump import dump_native
        rc = dump_native(999)
        assert rc == 1
        captured = capsys.readouterr()
        assert "\x1b" not in captured.out
        assert "\x1b" not in captured.err
