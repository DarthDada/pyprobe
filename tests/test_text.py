"""Contract tests for present/text.py (TX1–TX10, A5 落地 + A7 P1-8 修复).

Format expectations replicate the old types.py/stack_dump/record/top/
syscall_render outputs byte-for-byte (复刻约束) — these strings are the
CLI's user-visible surface.
"""

from pyprobe import dto
from pyprobe.observe.topstats import TopStats
from pyprobe.present.text import (
    CLEAR_SCREEN,
    HIDE_CURSOR,
    SHOW_CURSOR,
    SyscallStat,
    _shorten_path,
    format_folded,
    format_frame,
    format_native,
    format_native_thread,
    format_process,
    format_process_header,
    format_summary,
    format_syscall_event,
    format_thread,
    render_top,
)


def _frame(name, filename="bar.py", line=10):
    return dto.FrameInfo(name=name, filename=filename, line=line)


class TestFormatFrame:
    def test_basic(self):
        """TX1：帧行基本形态（索引/名称/文件：行号）。"""
        assert format_frame(_frame("foo"), 0) == "  #0 foo (bar.py:10)"

    def test_high_index(self):
        """TX1：索引原样透传（不补零）。"""
        assert format_frame(_frame("baz", "x.py", 3), 42) == "  #42 baz (x.py:3)"

    def test_none_name_and_filename(self):
        """TX1：None 名称/文件 → "?" 占位（两处独立）。"""
        assert format_frame(_frame(None), 0) == "  #0 ? (bar.py:10)"
        assert format_frame(_frame("foo", None, 1), 0) == "  #0 foo (?:1)"

    def test_color(self):
        """TX1：颜色点位（名绿/文件青/行号暗）。"""
        assert format_frame(_frame("foo"), 0, color=True) == (
            "  #0 \x1b[32mfoo\x1b[0m "
            "(\x1b[36mbar.py\x1b[0m:\x1b[2m10\x1b[0m)")

    def test_path_shortened_to_two_components(self):
        """TX1（复刻约束 Python 帧末 2 级）：长路径默认缩短。"""
        f = _frame("run", "/a/b/c/d/runners.py", 118)
        assert format_frame(f, 0) == "  #0 run (d/runners.py:118)"

    def test_verbose_keeps_full_path(self):
        """TX1：-v 保留全路径。"""
        f = _frame("run", "/a/b/c/d/runners.py", 118)
        assert format_frame(f, 0, verbose=True) == (
            "  #0 run (/a/b/c/d/runners.py:118)")

    def test_two_components_unchanged(self):
        """TX1 边界：≤2 级路径原样。"""
        assert format_frame(_frame("run", "c/runners.py", 1), 0) == \
            "  #0 run (c/runners.py:1)"


class TestShortenPath:
    def test_depth_semantics(self):
        """TX1 配套：_shorten_path 深度语义（>depth 才截）。"""
        assert _shorten_path("/a/b/c/d.py") == "c/d.py"
        assert _shorten_path("b/c.py") == "b/c.py"
        assert _shorten_path("c.py") == "c.py"


class TestFormatThread:
    def test_with_frames(self):
        """TX2：线程头 + 编号帧行。"""
        t = dto.ThreadInfo(native_tid=100, name="worker",
                           frames=[_frame("f1", "a.py", 1),
                                   _frame("f2", "b.py", 2)])
        out = format_thread(t)
        assert 'Thread 100: "worker"' in out
        assert "  #0 f1 (a.py:1)" in out
        assert "  #1 f2 (b.py:2)" in out

    def test_idle_marker(self):
        """TX2：idle 线程头带 (idle) 标记。"""
        assert "Thread 5 (idle)" in format_thread(
            dto.ThreadInfo(native_tid=5, idle=True))

    def test_no_name(self):
        """TX2：无名线程头无引号段。"""
        assert format_thread(dto.ThreadInfo(native_tid=7)).startswith(
            "Thread 7")

    def test_no_frames(self):
        """TX2：无帧 → 提示行（C 代码/空闲说明，逐字复刻）。"""
        out = format_thread(dto.ThreadInfo(native_tid=7))
        assert "  (no Python frame — thread may be in C code or idle)" in out

    def test_color(self):
        """TX2：颜色点位（tid 黄粗、idle 暗）。"""
        t = dto.ThreadInfo(native_tid=100, name="worker",
                           frames=[_frame("f1", "a.py", 1)])
        out = format_thread(t, color=True)
        assert 'Thread \x1b[1m\x1b[33m100\x1b[0m: "worker"' in out
        assert "  #0 \x1b[32mf1\x1b[0m (\x1b[36ma.py\x1b[0m:\x1b[2m1\x1b[0m)" \
            in out
        idle = dto.ThreadInfo(native_tid=5, idle=True)
        assert "Thread \x1b[1m\x1b[33m5\x1b[0m \x1b[2m(idle)\x1b[0m" in \
            format_thread(idle, color=True)


class TestFormatProcessHeader:
    def test_header(self):
        """TX3：进程头两行（pid/cmdline + 版本/exe）。"""
        p = dto.ProcessInfo(pid=123, cmdline="python app.py",
                            exe_path="/usr/bin/python3.12",
                            python_version="3.12.13")
        out = format_process_header(p)
        assert "Process 123: python app.py" in out
        assert "Python v3.12.13 (/usr/bin/python3.12)" in out

    def test_header_color(self):
        """TX3：pid 黄粗。"""
        p = dto.ProcessInfo(pid=123, cmdline="python app.py",
                            exe_path="/usr/bin/python3.12",
                            python_version="3.12.13")
        assert "Process \x1b[1m\x1b[33m123\x1b[0m: python app.py" in \
            format_process_header(p, color=True)


class TestFormatProcess:
    def test_full_output(self):
        """TX4：进程头 + 逐线程块 + 空行分隔。"""
        p = dto.ProcessInfo(pid=1, cmdline="python x", exe_path="/x",
                            python_version="3.12.13")
        t = dto.ThreadInfo(native_tid=100, frames=[_frame("f", "a.py", 1)])
        out = format_process(p, [t])
        assert "Process 1: python x" in out
        assert "Thread 100" in out
        assert "  #0 f (a.py:1)" in out

    def test_empty_threads(self):
        """TX4 边界：无线程仍出进程头。"""
        p = dto.ProcessInfo(pid=1, cmdline="python x", exe_path="/x")
        assert "Process 1" in format_process(p, [])


class TestFormatNativeThread:
    def test_with_module(self):
        """TX5：gdb 风格线程头 + 帧行（模块非 verbose 取 basename——
        复刻约束 native basename）。"""
        t = dto.NativeThreadInfo(
            tid=100, comm="python3",
            frames=[dto.NativeFrame(pc=0x1000, symbol="foo",
                                    module="/lib/libc.so"),
                    dto.NativeFrame(pc=0x2000, symbol="bar", module=None)])
        out = format_native_thread(t, 1)
        assert 'Thread 1 (LWP 100) "python3"' in out
        assert "#0  0x0000000000001000 in foo () from libc.so" in out
        assert "#1  0x0000000000002000 in bar ()" in out

    def test_verbose_keeps_full_module(self):
        """TX5：-v 保留模块全路径。"""
        t = dto.NativeThreadInfo(
            tid=100, comm="python3",
            frames=[dto.NativeFrame(pc=0x1000, symbol="foo",
                                    module="/lib/libc.so")])
        assert "from /lib/libc.so" in format_native_thread(t, 1, verbose=True)

    def test_unwind_failed(self):
        """TX5：回溯失败且无帧 → Backtrace stopped 提示（逐字复刻）。"""
        t = dto.NativeThreadInfo(tid=5, comm="x", unwind_failed=True)
        assert "Backtrace stopped: Cannot access memory" in \
            format_native_thread(t, 1)

    def test_color(self):
        """TX5：颜色点位（pc 暗/符号绿/模块青/失败红）。"""
        t = dto.NativeThreadInfo(
            tid=100, comm="python3",
            frames=[dto.NativeFrame(pc=0x1000, symbol="foo",
                                    module="/lib/libc.so")])
        out = format_native_thread(t, 1, color=True)
        assert 'Thread 1 (LWP \x1b[1m\x1b[33m100\x1b[0m) "python3"' in out
        assert ("#0  \x1b[2m0x0000000000001000\x1b[0m in \x1b[32mfoo\x1b[0m "
                "() from \x1b[36mlibc.so\x1b[0m") in out
        fail = dto.NativeThreadInfo(tid=5, comm="x", unwind_failed=True)
        assert ("\x1b[31m  Backtrace stopped: Cannot access memory at "
                "address 0x0\x1b[0m") in format_native_thread(fail, 1,
                                                              color=True)


class TestFormatNative:
    def test_multiple_threads(self):
        """TX5 配套：多线程块顺序拼接（线程顺序由 collect 保证升序）。"""
        threads = [
            dto.NativeThreadInfo(tid=101, comm="a",
                                 frames=[dto.NativeFrame(pc=0x1, symbol="f")]),
            dto.NativeThreadInfo(tid=102, comm="b",
                                 frames=[dto.NativeFrame(pc=0x2, symbol="g")]),
        ]
        out = format_native(threads)
        assert 'Thread 1 (LWP 101) "a"' in out
        assert 'Thread 2 (LWP 102) "b"' in out


class TestFormatSyscallEvent:
    def _event(self, **kw):
        defaults = {"tid": 1234, "nr": 257, "name": "openat",
                    "rendered": 'AT_FDCWD, "/tmp/x", O_RDONLY', "ret": 3}
        defaults.update(kw)
        return dto.SyscallEvent(**defaults)

    def test_success(self):
        """TX6：成功事件行（tid 双空格分隔，= ret）。"""
        assert format_syscall_event(self._event()) == \
            '1234  openat(AT_FDCWD, "/tmp/x", O_RDONLY) = 3'

    def test_error(self):
        """TX6（复刻约束 errno 名解码）：= -1 ENAME (desc)。"""
        out = format_syscall_event(self._event(error=2, ret=-1))
        assert "= -1 ENOENT (No such file or directory)" in out

    def test_unknown_errno(self):
        """TX6：表外 errno → ERRNO_<n> / Unknown error。"""
        out = format_syscall_event(self._event(error=999, ret=-1))
        assert "ERRNO_999" in out
        assert "Unknown error" in out

    def test_elapsed(self):
        """TX6（复刻约束 elapsed 格式）：尾部 <0.000123>；0 不显示。"""
        assert format_syscall_event(
            self._event(elapsed=0.000123)).endswith("<0.000123>")
        assert "<" not in format_syscall_event(self._event())

    def test_error_and_elapsed(self):
        """TX6：错误与计时并存。"""
        out = format_syscall_event(
            self._event(error=13, ret=-1, elapsed=0.5))
        assert "= -1 EACCES (Permission denied) " in out
        assert out.endswith("<0.500000>")

    def test_color(self):
        """TX6：tid 黄粗、name 绿、错误红。"""
        out = format_syscall_event(self._event(), color=True)
        assert "\x1b[1m\x1b[33m1234\x1b[0m" in out
        assert "\x1b[32mopenat\x1b[0m" in out
        err = format_syscall_event(self._event(error=2, ret=-1), color=True)
        assert "\x1b[31m= -1 ENOENT" in err


class TestFormatSummary:
    def _events(self):
        return [
            dto.SyscallEvent(tid=1, nr=0, name="read", ret=3, elapsed=0.001),
            dto.SyscallEvent(tid=1, nr=0, name="read", ret=3, elapsed=0.002),
            dto.SyscallEvent(tid=1, nr=1, name="write", ret=-1, error=2,
                             elapsed=0.010),
        ]

    def test_header_row(self):
        """TX7（复刻约束 total/s 列）：表头六列齐备。"""
        out = format_summary(self._events())
        header = out.splitlines()[0]
        for col in ("syscall", "calls", "errors", "total", "total/s",
                    "per-call"):
            assert col in header

    def test_counts_errors_and_totals(self):
        """TX7：按名聚合 calls/errors/total_time 与总计行。"""
        out = format_summary(self._events())
        assert "read" in out and "write" in out
        total_line = out.splitlines()[-1]
        assert "total" in total_line
        assert "3" in total_line  # 总调用数
        assert "1" in total_line  # 总错误数

    def test_sorted_by_total_time(self):
        """TX7：按 total_time 降序（write 0.010 在 read 0.003 前）。"""
        lines = format_summary(self._events()).splitlines()
        write_idx = lines.index(next(x for x in lines
                                     if x.startswith("write")))
        read_idx = lines.index(next(x for x in lines
                                    if x.startswith("read")))
        assert write_idx < read_idx

    def test_separator_rows(self):
        """TX7：82 连字符分隔行（复刻约束）。"""
        out = format_summary(self._events())
        assert "-" * 82 in out.splitlines()

    def test_syscall_stat_object(self):
        """TX7 配套：SyscallStat 累加器形态（库用户可取）。"""
        st = SyscallStat("read")
        st.calls += 1
        st.errors += 1
        st.total_time += 0.5
        assert (st.name, st.calls, st.errors, st.total_time) == \
            ("read", 1, 1, 0.5)


class TestFormatFolded:
    def test_sorted_count_desc_then_lexicographic(self):
        """TX8：计数降序、同计数键升序（确定性输出，复刻约束）。"""
        p = dto.ProfileData(
            proc_info=dto.ProcessInfo(pid=1, cmdline="c", exe_path="/x"),
            counts={"b;f": 1, "a;f": 3, "c;f": 3})
        lines = format_folded(p).splitlines()
        assert lines == ["a;f 3", "c;f 3", "b;f 1"]

    def test_empty(self):
        """TX8 边界：无计数 → 空串（不写文件时无空行污染）。"""
        p = dto.ProfileData(
            proc_info=dto.ProcessInfo(pid=1, cmdline="c", exe_path="/x"))
        assert format_folded(p) == ""

    def test_trailing_newline(self):
        """TX8：非空输出以换行结尾（flamegraph 工具行格式）。"""
        p = dto.ProfileData(
            proc_info=dto.ProcessInfo(pid=1, cmdline="c", exe_path="/x"),
            counts={"a;f": 1})
        assert format_folded(p) == "a;f 1\n"


class TestRenderTop:
    def _stats(self):
        s = TopStats()
        s.update([dto.ThreadInfo(native_tid=101, name="worker", frames=[
            dto.FrameInfo("burn", "app/spin.py", 7),
            dto.FrameInfo("main", "app/main.py", 3)])])
        return s

    def test_layout_headers_and_values(self):
        """TX9：屏幕骨架——进程头/Elapsed 行/Active 表/Top functions 表。"""
        p = dto.ProcessInfo(pid=1, cmdline="python x", exe_path="/x",
                            python_version="3.12.13")
        out = render_top(self._stats(), p, elapsed=2.0)
        assert "Process 1: python x" in out
        assert "Elapsed 2.0s | 1 samples (idle 0)" in out
        assert "Active threads" in out
        assert "101" in out and "burn" in out
        assert "Top functions" in out
        assert "100.0%" in out  # burn own 1/1

    def test_no_ansi_without_color(self):
        """TX9：非着色输出无 ANSI 转义（管道/日志洁净）。"""
        p = dto.ProcessInfo(pid=1, cmdline="c", exe_path="/x")
        assert "\x1b[" not in render_top(self._stats(), p, elapsed=1.0)

    def test_top_n_truncates(self):
        """TX9：Top functions 按 top_n 截断——判据限定在该表尾部
        （Active 表会列出全部线程名，整屏断言必假阳性；与旧
        test_top.py:89 同形的尾部分段）。同计数按名升序取前 5。"""
        s = TopStats()
        for i in range(20):
            s.update([dto.ThreadInfo(native_tid=100 + i, frames=[
                dto.FrameInfo(f"f{i:02d}", "a.py", 1)])])
        p = dto.ProcessInfo(pid=1, cmdline="c", exe_path="/x")
        out = render_top(s, p, elapsed=1.0, top_n=5)
        tail = out.split("Top functions")[-1]
        ranked = [line for line in tail.splitlines()
                  if line.strip() and not line.startswith(" " * 2 + "OWN%")]
        func_rows = [line for line in ranked if "f" in line]
        assert len(func_rows) == 5
        assert "f00" in tail and "f04" in tail
        assert "f05" not in tail

    def test_idle_threads_listed(self):
        """TX9：Idle threads 行（去重升序，A7 P0-1 修复后的展示面）。"""
        s = self._stats()
        s.update([dto.ThreadInfo(native_tid=105, idle=True)])
        p = dto.ProcessInfo(pid=1, cmdline="c", exe_path="/x")
        out = render_top(s, p, elapsed=1.0)
        assert "Idle threads: 105" in out
        assert "105, 105" not in out

    def test_unnamed_top_frame_own_pct_nonzero(self):
        """TX9（A7 P1-8 修复）：无名顶帧的 OWN% 以 "?" 归一查表——旧树
        用 frame.name（None）查询，OWN% 恒 0。"""
        s = TopStats()
        s.update([dto.ThreadInfo(native_tid=101, frames=[
            dto.FrameInfo(None, "a.py", 1)])])
        p = dto.ProcessInfo(pid=1, cmdline="c", exe_path="/x")
        out = render_top(s, p, elapsed=1.0)
        active_line = next(line for line in out.splitlines()
                           if "101" in line)
        assert "100.0%" in active_line  # 不再恒 0


class TestTerminalSequences:
    def test_cursor_and_clear_verbatim(self):
        """TX10：终端控制序列逐字（top 清屏/光标协议，复刻约束）。"""
        assert HIDE_CURSOR == "\x1b[?25l"
        assert SHOW_CURSOR == "\x1b[?25h"
        assert CLEAR_SCREEN == "\x1b[H\x1b[2J"
