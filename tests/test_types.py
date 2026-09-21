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

    def test_format_color(self):
        f = FrameInfo(name="foo", filename="bar.py", line=10)
        assert f.format(0, color=True) == (
            "  #0 \x1b[32mfoo\x1b[0m "
            "(\x1b[36mbar.py\x1b[0m:\x1b[2m10\x1b[0m)"
        )

    def test_format_long_path_shortened(self):
        f = FrameInfo(name="run", filename="/a/b/c/d/runners.py", line=118)
        assert f.format(0) == "  #0 run (d/runners.py:118)"

    def test_format_long_path_verbose_keeps_full(self):
        f = FrameInfo(name="run", filename="/a/b/c/d/runners.py", line=118)
        assert f.format(0, verbose=True) == (
            "  #0 run (/a/b/c/d/runners.py:118)")

    def test_format_two_components_unchanged(self):
        """Paths of two components or fewer are returned unchanged."""
        f = FrameInfo(name="run", filename="c/runners.py", line=1)
        assert f.format(0) == "  #0 run (c/runners.py:1)"

    def test_format_verbose_short_path_unchanged(self):
        f = FrameInfo(name="run", filename="bar.py", line=1)
        assert f.format(0, verbose=True) == "  #0 run (bar.py:1)"


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

    def test_format_color(self):
        t = ThreadInfo(native_tid=100, name="worker",
                       frames=[FrameInfo("f1", "a.py", 1)])
        out = t.format(color=True)
        assert 'Thread \x1b[1m\x1b[33m100\x1b[0m: "worker"' in out
        assert "  #0 \x1b[32mf1\x1b[0m (\x1b[36ma.py\x1b[0m:\x1b[2m1\x1b[0m)" in out

    def test_format_verbose_passes_through(self):
        """verbose=True keeps full frame filenames."""
        t = ThreadInfo(native_tid=100, name="worker",
                       frames=[FrameInfo("f1", "/a/b/c/f1.py", 1)])
        out = t.format(verbose=True)
        assert "  #0 f1 (/a/b/c/f1.py:1)" in out

    def test_format_default_shortens_frame_paths(self):
        t = ThreadInfo(native_tid=100, name="worker",
                       frames=[FrameInfo("f1", "/a/b/c/f1.py", 1)])
        out = t.format()
        assert "  #0 f1 (c/f1.py:1)" in out

    def test_format_color_idle(self):
        t = ThreadInfo(native_tid=5, idle=True)
        assert "Thread \x1b[1m\x1b[33m5\x1b[0m \x1b[2m(idle)\x1b[0m" in t.format(color=True)


class TestProcessInfo:
    def test_format_header(self):
        p = ProcessInfo(pid=123, cmdline="python app.py", exe_path="/usr/bin/python3.12",
                        python_version="3.12.13")
        out = p.format_header()
        assert "Process 123: python app.py" in out
        assert "Python v3.12.13 (/usr/bin/python3.12)" in out

    def test_format_header_color(self):
        p = ProcessInfo(pid=123, cmdline="python app.py", exe_path="/usr/bin/python3.12",
                        python_version="3.12.13")
        out = p.format_header(color=True)
        assert "Process \x1b[1m\x1b[33m123\x1b[0m: python app.py" in out
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
        assert "#0  0x0000000000001000 in foo () from libc.so" in out
        assert "#1  0x0000000000002000 in bar ()" in out

    def test_thread_format_verbose_keeps_full_module(self):
        t = NativeThreadInfo(
            tid=100, comm="python3",
            frames=[NativeFrame(pc=0x1000, symbol="foo", module="/lib/libc.so")],
        )
        out = t.format(1, verbose=True)
        assert "#0  0x0000000000001000 in foo () from /lib/libc.so" in out

    def test_thread_format_unwind_failed(self):
        t = NativeThreadInfo(tid=5, comm="x", frames=[], unwind_failed=True)
        out = t.format(1)
        assert "Backtrace stopped: Cannot access memory" in out

    def test_thread_format_no_frames(self):
        t = NativeThreadInfo(tid=5, comm="x", frames=[], unwind_failed=False)
        out = t.format(1)
        assert 'Thread 1 (LWP 5) "x"' in out

    def test_thread_format_color(self):
        t = NativeThreadInfo(
            tid=100, comm="python3",
            frames=[NativeFrame(pc=0x1000, symbol="foo", module="/lib/libc.so"),
                     NativeFrame(pc=0x2000, symbol="bar", module=None)],
        )
        out = t.format(1, color=True)
        assert 'Thread 1 (LWP \x1b[1m\x1b[33m100\x1b[0m) "python3"' in out
        assert ("#0  \x1b[2m0x0000000000001000\x1b[0m in \x1b[32mfoo\x1b[0m () "
                "from \x1b[36mlibc.so\x1b[0m") in out
        assert "#1  \x1b[2m0x0000000000002000\x1b[0m in \x1b[32mbar\x1b[0m ()" in out

    def test_thread_format_color_unwind_failed(self):
        t = NativeThreadInfo(tid=5, comm="x", frames=[], unwind_failed=True)
        out = t.format(1, color=True)
        assert ("\x1b[31m  Backtrace stopped: Cannot access memory at address 0x0"
                "\x1b[0m") in out


class TestSyscallEvent:
    def _event(self, **kw):
        from pyprobe.types import SyscallEvent
        defaults = dict(tid=1234, nr=257, name="openat",
                        rendered='AT_FDCWD, "/tmp/x", O_RDONLY', ret=3)
        defaults.update(kw)
        return SyscallEvent(**defaults)

    def test_format_success(self):
        ev = self._event()
        assert ev.format() == \
            '1234  openat(AT_FDCWD, "/tmp/x", O_RDONLY) = 3'

    def test_format_error(self):
        ev = self._event(error=2, ret=-1)
        out = ev.format()
        assert "= -1 ENOENT (No such file or directory)" in out

    def test_format_unknown_errno(self):
        ev = self._event(error=999, ret=-1)
        out = ev.format()
        assert "ERRNO_999" in out
        assert "Unknown error" in out

    def test_format_elapsed(self):
        ev = self._event(elapsed=0.000123)
        assert ev.format().endswith("<0.000123>")

    def test_format_no_elapsed(self):
        assert "<" not in self._event().format()

    def test_format_error_and_elapsed(self):
        ev = self._event(error=13, ret=-1, elapsed=0.5)
        out = ev.format()
        assert "= -1 EACCES (Permission denied) " in out
        assert out.endswith("<0.500000>")

    def test_format_color(self):
        ev = self._event()
        out = ev.format(color=True)
        assert "\x1b[1m\x1b[33m1234\x1b[0m" in out   # tid
        assert "\x1b[32mopenat\x1b[0m" in out   # syscall name

    def test_format_error_color(self):
        ev = self._event(error=2, ret=-1)
        out = ev.format(color=True)
        assert "\x1b[31m= -1 ENOENT" in out     # red error

    def test_defaults(self):
        from pyprobe.types import SyscallEvent
        ev = SyscallEvent(tid=1, nr=0, name="read")
        assert ev.args == [] and ev.rendered == "" and ev.ret == 0
        assert ev.error is None and ev.elapsed == 0.0
