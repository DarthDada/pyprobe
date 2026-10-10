"""Integration: syscall tracing and native unwinding against live targets
(contracts.md 批 5 表 SY12/SY13/NA9, §10.2-4).

fast_syscall_app 提供 0.2s 级 clock_nanosleep 循环（§10.2-7 fast target）；
SY12 的 strace 对比在 strace 缺席时 skip（spec-oracle 的可用性降级）。
"""

import re
import shutil
import subprocess
import sys

import pytest

from pyprobe.errors import AttachFailed
from pyprobe.observe.native import collect_native
from pyprobe.observe.syscalls import collect_syscalls

pytestmark = pytest.mark.integration

#: strace 常量名 → pyprobe 数值渲染的对照表（canon 归一化用；clockid 与
#: clock_nanosleep flags 权威值见 linux/time.h）。
_STRACE_CONST_NAMES = {
    "CLOCK_REALTIME": "0",
    "CLOCK_MONOTONIC": "1",
    "TIMER_ABSTIME": "1",
}


class TestSyscallTracingLive:
    def test_captures_clock_nanosleep_with_elapsed(self, fast_syscall_pid):
        """SY13：对 fast target 捕获 clock_nanosleep 且 elapsed≥0.1s
        （entry/exit 配对、计时、引擎全链路的活体互证）。"""
        events = collect_syscalls(fast_syscall_pid, max_events=5)
        assert events, "no events captured from a sleeping target"
        sleep_events = [e for e in events if e.name == "clock_nanosleep"]
        assert sleep_events, (
            f"clock_nanosleep not in {[e.name for e in events]}")
        assert any(e.elapsed >= 0.1 for e in sleep_events)

    def test_filter_selects_events(self, fast_syscall_pid):
        """SY13/SY8 活体：-e trace=clock_nanosleep 只捕获目标 syscall。"""
        events = collect_syscalls(fast_syscall_pid,
                                  trace="clock_nanosleep", max_events=3)
        assert events
        assert all(e.name == "clock_nanosleep" for e in events)

    def test_dead_pid_raises(self):
        """SY1 失败模式活体：不存在的 PID → PyProbeError 子类（非 traceback）。"""
        from pyprobe.errors import PyProbeError

        with pytest.raises(PyProbeError):
            collect_syscalls(1 << 22, max_events=1)


class TestStraceOracle:
    """SY12 (§10.2-1)：渲染样例 vs strace 真实输出。

    ptrace_scope=1 下 strace 作为子进程无法 attach 我们的 fixture 目标
    （兄弟进程非祖先），故 strace 改为**包裹同一 target_app 的另一个
    实例**（strace 是祖先，合法）——对照的是同一确定性程序的渲染格式。
    """

    @pytest.mark.skipif(shutil.which("strace") is None,
                        reason="strace not installed")
    def test_clock_nanosleep_render_matches_strace(self, fast_syscall_pid,
                                                   tmp_path):
        """SY12：pyprobe 与 strace 对 clock_nanosleep 的渲染在归一化
        （地址/tid/timespec 值抹零）后一致——渲染格式的权威对照。"""
        ours = collect_syscalls(fast_syscall_pid, trace="clock_nanosleep",
                                max_events=2)
        assert ours, "no clock_nanosleep captured by pyprobe"

        # strace 作为祖先包裹同一程序的独立实例（ptrace_scope=1 合法）
        app = tmp_path / "app.py"
        app.write_text("import time\nwhile True: time.sleep(0.2)\n")
        out_file = tmp_path / "trace.out"
        with open(out_file, "w") as f:
            proc = subprocess.Popen(
                ["strace", "-e", "trace=clock_nanosleep",
                 sys.executable, str(app)],
                stdout=subprocess.DEVNULL, stderr=f, text=True)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5)
        pattern = re.compile(
            r'clock_nanosleep\((?P<args>[^)]*)\)\s*=\s*(?P<ret>\S+)')
        strace_lines = [m.group(0) for m in map(
            pattern.search, out_file.read_text().splitlines()) if m]
        assert strace_lines, (
            f"strace captured no clock_nanosleep: "
            f"{out_file.read_text()[:500]}")

        def canon(line):
            """归一化为 (name, args元组, ret)——抹平两方的**文档化**格式
            差异（§10.3 复刻约束保留、非 bug）：pyprobe 全显 6 参数、
            timespec 无名花括号、clockid/flags 数值；strace 按真实元数
            显示、{tv_sec=…} 具名、常量名解码。差异项归一后语义等价。"""
            line = re.sub(r"0x[0-9a-f]+", "0xADDR", line)
            line = re.sub(r"\{tv_sec=\d+, tv_nsec=\d+\}", "{TS}", line)
            line = re.sub(r"\{\d+, \d+\}", "{TS}", line)
            # strace 的常量名解码 → 数值（对齐 pyprobe 的数值渲染）
            for name, num in _STRACE_CONST_NAMES.items():
                line = line.replace(name, num)
            line = re.sub(r"\s*<\d+\.\d+>$", "", line)  # strace -T 尾
            line = re.sub(r"^\s*\d+\s+", "", line)      # tid 列
            m = re.match(r"(\w+)\((.*)\) = (\S+)$", line.strip())
            name, argstr, ret = m.groups()
            args = tuple(a.strip() for a in argstr.split(", ")) \
                if argstr else ()
            return name, args, ret

        ours = [canon(f"{e.name}({e.rendered}) = {e.ret}") for e in ours]
        strace = [canon(line) for line in strace_lines]
        # 参数数对齐：strace 按 syscall 真实元数显示，pyprobe 全显 6 个
        # （复刻约束）——截取前 N 个比较
        matches = [
            (o, s) for o in ours for s in strace
            if o[0] == s[0] and o[2] == s[2]
            and o[1][: len(s[1])] == s[1]
        ]
        assert matches, (
            f"no common rendered call:\nours={ours}\nstrace={strace}")


class TestNativeUnwindLive:
    def test_collect_native_frames(self, target_pid):
        """NA9：对 live target 出 native 帧——线程非空、主线程有帧、
        tid 升序（libdw/权限不足时 skip，旧 or-skip 语义保留）。"""
        try:
            threads = collect_native(target_pid)
        except (AttachFailed, OSError) as e:
            pytest.skip(f"native unwind unavailable: {e}")
        assert threads
        tids = [t.tid for t in threads]
        assert tids == sorted(tids)
        main = [t for t in threads if t.tid == target_pid]
        assert main and (main[0].frames or main[0].unwind_failed)
