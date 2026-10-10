"""Contract tests for present/dumps.py (DP1–DP5).

Collect functions are monkeypatched at the dumps module's import seams —
dump 层的职责是编排（collect + 颜色决策 + 打印 + 退出码），collect/format
行为由各自批次的测试守护。
"""

import json

import pyprobe.present.dumps as dumps
from pyprobe import dto
from pyprobe.errors import (
    AttachFailed,
    NoThreadState,
    ProcessExited,
    ProcessNotFound,
)

PID = 4242


class StubSession:
    def __init__(self, warning=None):
        self.pid = PID
        self.proc_info = dto.ProcessInfo(
            pid=PID, cmdline="python3 app.py", exe_path="/x/python",
            python_version="3.12.13")
        self.version_warning = warning


def _threads():
    return [dto.ThreadInfo(native_tid=101, name="main", frames=[
        dto.FrameInfo("run", "app/main.py", 10)])]


class TestDumpPython:
    def test_success_text(self, monkeypatch, capsys):
        """DP1：成功路径——文本输出含进程头与线程块，rc=0。"""
        monkeypatch.setattr(dumps, "open_session", lambda pid: StubSession())
        monkeypatch.setattr(dumps, "collect_snapshot",
                            lambda s: _threads())
        assert dumps.dump_python(PID, color=False) == 0
        out = capsys.readouterr().out
        assert "Process 4242: python3 app.py" in out
        assert 'Thread 101: "main"' in out
        assert "  #0 run (app/main.py:10)" in out

    def test_error_goes_to_stderr_rc1(self, monkeypatch, capsys):
        """DP1：目标错误 → `[!] <err>` 到 stderr，rc=1，无 stdout。"""
        def boom(pid):
            raise ProcessNotFound(pid)

        monkeypatch.setattr(dumps, "open_session", boom)
        assert dumps.dump_python(999, color=False) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "[!] Process 999 not found" in captured.err

    def test_error_color_forced(self, monkeypatch, capsys):
        """DP1：--color always 时错误行带 ANSI（非 tty 也生效）。"""
        def boom(pid):
            raise ProcessNotFound(pid)

        monkeypatch.setattr(dumps, "open_session", boom)
        dumps.dump_python(999, color=True)
        assert "\x1b[31m[!]" in capsys.readouterr().err

    def test_version_warning_before_collection_failure(self, monkeypatch,
                                                       capsys):
        """DP2：版本警告先于线程收集失败打印（警告是后续失败的根因，
        不得被提前 return 吞掉——旧 stack_dump L338-342 语义）。"""
        monkeypatch.setattr(dumps, "open_session",
                            lambda pid: StubSession(warning="[!] fake warn"))
        def boom(session):
            raise NoThreadState()

        monkeypatch.setattr(dumps, "collect_snapshot", boom)
        assert dumps.dump_python(PID, color=False) == 1
        err = capsys.readouterr().err
        assert "[!] fake warn" in err
        assert "failed to read threads.head" in err
        assert err.index("fake warn") < err.index("threads.head")

    def test_json_output_never_colored(self, monkeypatch, capsys):
        """DP1/DP2：--json 输出 JSON 文档，color=True 也无 ANSI。"""
        monkeypatch.setattr(dumps, "open_session", lambda pid: StubSession())
        monkeypatch.setattr(dumps, "collect_snapshot",
                            lambda s: _threads())
        assert dumps.dump_python(PID, color=True, json_output=True) == 0
        out = capsys.readouterr().out
        assert "\x1b[" not in out
        doc = json.loads(out)
        assert doc["process"]["pid"] == PID


class TestDumpNative:
    def _native_threads(self):
        return [dto.NativeThreadInfo(tid=101, comm="python3", frames=[
            dto.NativeFrame(pc=0x1000, symbol="main",
                            module="/usr/bin/python3.12")])]

    def test_success_prints_single_header(self, monkeypatch, capsys):
        """DP1：成功——进程头一次（旧树曾重复打印，2026-10 修复语义）+
        线程块，rc=0。"""
        monkeypatch.setattr(dumps, "collect_native",
                            lambda pid: self._native_threads())
        monkeypatch.setattr(dumps, "read_cmdline",
                            lambda pid: "python3 app.py")
        assert dumps.dump_native(PID, color=False) == 0
        out = capsys.readouterr().out
        assert out.count("Process 4242: python3 app.py") == 1
        assert 'Thread 1 (LWP 101) "python3"' in out

    def test_error_rc1(self, monkeypatch, capsys):
        """DP1：attach 失败 → stderr + rc=1。"""
        def boom(pid):
            raise AttachFailed(pid, "Operation not permitted")

        monkeypatch.setattr(dumps, "collect_native", boom)
        monkeypatch.setattr(dumps, "read_cmdline", lambda pid: "x")
        assert dumps.dump_native(PID, color=False) == 1
        assert "[!]" in capsys.readouterr().err

    def test_json(self, monkeypatch, capsys):
        """DP1：--json 走 format_native_json（process 仅 pid/cmdline）。"""
        monkeypatch.setattr(dumps, "collect_native",
                            lambda pid: self._native_threads())
        monkeypatch.setattr(dumps, "read_cmdline",
                            lambda pid: "python3 app.py")
        assert dumps.dump_native(PID, color=False, json_output=True) == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["process"] == {"pid": PID, "cmdline": "python3 app.py"}


class TestDumpSyscalls:
    def _event(self, tid=1234):
        return dto.SyscallEvent(tid=tid, nr=257, name="openat",
                                rendered='AT_FDCWD, "/tmp/x"', ret=3,
                                elapsed=0.000123)

    def _stub_collect(self, monkeypatch, events, error=None):
        def fake_collect(pid, *, trace=None, max_events=None, verbose=False,
                         on_event=None):
            for ev in events:
                if on_event is not None:
                    on_event(ev)
            if error is not None:
                raise error
            return list(events)

        monkeypatch.setattr(dumps, "collect_syscalls", fake_collect)

    def test_streams_events(self, monkeypatch, capsys):
        """DP3：流式模式逐事件打印（含 elapsed 尾），rc=0。"""
        self._stub_collect(monkeypatch, [self._event()])
        assert dumps.dump_syscalls(PID, color=False) == 0
        out = capsys.readouterr().out
        assert '1234  openat(AT_FDCWD, "/tmp/x") = 3' in out
        assert "<0.000123>" in out

    def test_summary_instead_of_stream(self, monkeypatch, capsys):
        """DP3：--summary 出汇总表不流式（total/s 列在）。"""
        self._stub_collect(monkeypatch, [self._event()])
        assert dumps.dump_syscalls(PID, color=False, summary=True) == 0
        out = capsys.readouterr().out
        assert "total/s" in out
        assert "= 3" not in out  # 无逐事件行

    def test_json_precedence_never_colored(self, monkeypatch, capsys):
        """DP3：--json 为单个 JSON 文档且永不着色。"""
        self._stub_collect(monkeypatch, [self._event()])
        assert dumps.dump_syscalls(PID, color=True, json_output=True) == 0
        out = capsys.readouterr().out
        assert "\x1b[" not in out
        assert json.loads(out)["events"][0]["name"] == "openat"

    def test_attach_error_rc1(self, monkeypatch, capsys):
        """DP3：attach 失败（arch/权限/进程）→ `[!]` + rc=1（A7 P0-2 的
        CLI 面——UnsupportedArchitecture 同样走此径）。"""
        self._stub_collect(monkeypatch, [], error=AttachFailed(PID, "x"))
        assert dumps.dump_syscalls(PID, color=False) == 1
        assert "[!]" in capsys.readouterr().err

    def test_keyboardinterrupt_still_summarizes(self, monkeypatch, capsys):
        """DP3：Ctrl-C 干净退出——已捕获事件仍出 summary，rc=0
        （SY14 on_event 是部分结果的通道）。"""
        events = [self._event(tid=1234), self._event(tid=1235)]
        self._stub_collect(monkeypatch, events,
                           error=KeyboardInterrupt())
        assert dumps.dump_syscalls(PID, color=False, summary=True) == 0
        out = capsys.readouterr().out
        assert "openat" in out
        assert "total" in out.splitlines()[-1]


class TestDumpRecord:
    def _profile(self, warning=None):
        return dto.ProfileData(
            proc_info=dto.ProcessInfo(pid=PID, cmdline="python3 app.py",
                                      exe_path="/x/python"),
            counts={'"main";run': 3}, samples=3, idle_samples=1,
            elapsed=0.5, version_warning=warning)

    def test_stdout_folded_stderr_summary(self, monkeypatch, capsys):
        """DP4：folded 文本只去 stdout，摘要 `[i]` 只去 stderr（管道洁净，
        复刻约束）。"""
        monkeypatch.setattr(dumps, "collect_profile",
                            lambda pid, **kw: self._profile())
        assert dumps.dump_record(PID, color=False) == 0
        captured = capsys.readouterr()
        assert captured.out == '"main";run 3\n'
        assert "[i] pyprobe recorded 3 samples" in captured.err
        assert "[i]" not in captured.out

    def test_output_file(self, monkeypatch, capsys, tmp_path):
        """DP4：-o 写文件——stdout 无 folded 文本。"""
        monkeypatch.setattr(dumps, "collect_profile",
                            lambda pid, **kw: self._profile())
        out_file = tmp_path / "out.folded"
        assert dumps.dump_record(PID, color=False,
                                 output=str(out_file)) == 0
        assert out_file.read_text() == '"main";run 3\n'
        assert capsys.readouterr().out == ""

    def test_version_warning_printed(self, monkeypatch, capsys):
        """DP4：版本警告打到 stderr（dump 层 surfacing 职责，§8.5 语义）。"""
        monkeypatch.setattr(dumps, "collect_profile",
                            lambda pid, **kw: self._profile(warning="[!] w"))
        dumps.dump_record(PID, color=False)
        assert "[!] w" in capsys.readouterr().err

    def test_target_error_rc1(self, monkeypatch, capsys):
        """DP4：目标错误 → rc=1。"""
        def boom(pid, **kw):
            raise ProcessNotFound(pid)

        monkeypatch.setattr(dumps, "collect_profile", boom)
        assert dumps.dump_record(999, color=False) == 1


class StubSampler:
    def __init__(self, pid, rounds):
        self.session = StubSession()
        self.proc_info = self.session.proc_info
        self._rounds = list(rounds)

    def sample(self):
        r = self._rounds.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    def refresh_names(self):
        pass


class TestDumpTop:
    def test_not_a_tty_rc2(self, monkeypatch, capsys):
        """DP5：非 tty → 提示 + rc=2（无降级——README 承诺，复刻约束）。"""
        monkeypatch.setattr(dumps, "_stdout_is_tty", lambda: False)
        assert dumps.dump_top(PID, color=False) == 2
        err = capsys.readouterr().err
        assert "requires a terminal" in err

    def test_target_error_rc1(self, monkeypatch, capsys):
        """DP5：目标错误 → rc=1。"""
        monkeypatch.setattr(dumps, "_stdout_is_tty", lambda: True)
        def boom(pid, **kw):
            raise ProcessNotFound(pid)

        monkeypatch.setattr(dumps, "Sampler", boom)
        assert dumps.dump_top(999, color=False) == 1

    def test_exits_on_process_exited(self, monkeypatch, capsys):
        """DP5：目标退出 → rc=0、恢复光标（finally SHOW_CURSOR）、
        首屏已渲染（不等首个 interval）、摘要到 stderr。"""
        monkeypatch.setattr(dumps, "_stdout_is_tty", lambda: True)
        rounds = [[dto.ThreadInfo(native_tid=101, frames=[
            dto.FrameInfo("burn", "app/spin.py", 7)])],
                  ProcessExited(PID)]
        monkeypatch.setattr(dumps, "Sampler",
                            lambda pid, **kw: StubSampler(pid, rounds))
        assert dumps.dump_top(PID, color=False, rate=1000,
                              interval=60) == 0
        captured = capsys.readouterr()
        assert "\x1b[H\x1b[2J" in captured.out       # 首屏渲染
        assert "burn" in captured.out
        assert captured.out.endswith("\x1b[?25h")     # 光标恢复于末尾
        assert "[i] pyprobe top:" in captured.err
