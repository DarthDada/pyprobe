"""Unit tests for pyprobe.types — structured data formatting."""

from pyprobe.types import (
    FrameInfo, ThreadInfo, ProcessInfo,
    NativeFrame, NativeThreadInfo,
)


class TestFrameInfo:
    def test_format(self):
        f = FrameInfo(name="foo", filename="bar.py", line=10)
        assert f.format(0) == "  #0 foo (bar.py:10)"

    def test_format_high_index(self):
        f = FrameInfo(name="baz", filename="x.py", line=3)
        assert f.format(42) == "  #42 baz (x.py:3)"

    def test_format_none_name(self):
        f = FrameInfo(name=None, filename="bar.py", line=1)
        assert f.format(0) == "  #0 ? (bar.py:1)"

    def test_format_none_filename(self):
        f = FrameInfo(name="foo", filename=None, line=1)
        assert f.format(0) == "  #0 foo (?:1)"


class TestThreadInfo:
    def test_format_with_frames(self):
        t = ThreadInfo(
            native_tid=100,
            name="worker",
            frames=[FrameInfo("f1", "a.py", 1), FrameInfo("f2", "b.py", 2)],
        )
        out = t.format()
        assert 'Thread 100: "worker"' in out
        assert "  #0 f1 (a.py:1)" in out
        assert "  #1 f2 (b.py:2)" in out

    def test_format_idle(self):
        t = ThreadInfo(native_tid=5, idle=True)
        assert "Thread 5 (idle)" in t.format()

    def test_format_no_name(self):
        t = ThreadInfo(native_tid=7)
        out = t.format()
        assert out.startswith("Thread 7")

    def test_format_no_frames(self):
        t = ThreadInfo(native_tid=7)
        out = t.format()
        assert "no Python frame" in out

    def test_format_name_with_quotes(self):
        t = ThreadInfo(native_tid=1, name="my-thread")
        assert 'Thread 1: "my-thread"' in t.format()


class TestProcessInfo:
    def test_format_header(self):
        p = ProcessInfo(pid=123, cmdline="python app.py", exe_path="/usr/bin/python3.12",
                        python_version="3.12.13")
        out = p.format_header()
        assert "Process 123: python app.py" in out
        assert "Python v3.12.13 (/usr/bin/python3.12)" in out


class TestNativeFrame:
    def test_thread_format_with_module(self):
        t = NativeThreadInfo(
            tid=100, comm="python3",
            frames=[NativeFrame(pc=0x1000, symbol="foo", module="/lib/libc.so"),
                     NativeFrame(pc=0x2000, symbol="bar", module=None)],
        )
        out = t.format(1)
        assert 'Thread 1 (LWP 100) "python3"' in out
        assert "#0  0x0000000000001000 in foo () from /lib/libc.so" in out
        assert "#1  0x0000000000002000 in bar ()" in out

    def test_thread_format_unwind_failed(self):
        t = NativeThreadInfo(tid=5, comm="x", frames=[], unwind_failed=True)
        out = t.format(1)
        assert "Backtrace stopped: Cannot access memory" in out

    def test_thread_format_no_frames(self):
        t = NativeThreadInfo(tid=5, comm="x", frames=[], unwind_failed=False)
        out = t.format(1)
        assert 'Thread 1 (LWP 5) "x"' in out
