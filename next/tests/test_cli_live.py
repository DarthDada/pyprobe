"""Integration: the next/ CLI end-to-end (contracts.md 批 6；§10.5-1
旧新差异对比的演练——CLI 子进程是唯一的对比通道）。

yama ptrace_scope=1 约束：探测方必须是目标的祖先。CLI 以**包裹进程**
运行——先派生目标（成为其父）、再在进程内执行 cli.main——而非由
pytest 派生兄弟进程 CLI（批次 6 验收实证：兄弟探测在此环境结构性
EPERM，旧树从不以 CLI 子进程探测目标正是此因）。
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

NEXT_ROOT = Path(__file__).resolve().parent.parent

#: Wrapper: spawn the target as a child (ancestor rule), relay its READY
#: handshake, then run the CLI in-process with the target's pid patched
#: into argv. Prints "TARGETPID <pid>" on its first stdout line so the
#: test can correlate output with the spawned target.
_WRAPPER = textwrap.dedent("""
    import subprocess, sys

    script, cli_args = sys.argv[1], sys.argv[2:]
    target = subprocess.Popen([sys.executable, script],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
    try:
        line = target.stdout.readline()
        if not line.startswith("READY"):
            sys.exit(f"target failed to start: {line!r}")
        pid = int(line.split()[1])
        print(f"TARGETPID {pid}", flush=True)
        sys.argv = ["pyprobe"] + [a.replace("__PID__", str(pid))
                                  for a in cli_args]
        from pyprobe.cli import main
        rc = main(sys.argv[1:])
    finally:
        target.terminate()
        try:
            target.wait(timeout=5)
        except subprocess.TimeoutExpired:
            target.kill()
            target.wait()
    sys.exit(rc)
""")


def run_cli_with_target(script_name, *argv, timeout=25):
    """Run the CLI in a wrapper that parents the target itself.

    Returns (proc, target_pid): proc.stdout 的首行是 TARGETPID 标记，
    其余为 CLI 输出。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(NEXT_ROOT)
    env.pop("CLICOLOR_FORCE", None)
    script = NEXT_ROOT / "tests" / "targets" / script_name
    proc = subprocess.run(
        [sys.executable, "-c", _WRAPPER, str(script), *argv],
        capture_output=True, text=True, timeout=timeout, env=env)
    first, _, rest = proc.stdout.partition("\n")
    assert first.startswith("TARGETPID "), proc.stdout[:300]
    proc.stdout = rest
    return proc, int(first.split()[1])


def run_cli(*argv, timeout=20):
    """Run the CLI standalone (no target needed); return CompletedProcess."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(NEXT_ROOT)
    env.pop("CLICOLOR_FORCE", None)
    return subprocess.run(
        [sys.executable, "-m", "pyprobe", *argv],
        capture_output=True, text=True, timeout=timeout, env=env)


class TestStackCliLive:
    def test_stack_json_against_live_target(self):
        """端到端：`stack --json` 对 live target——退出码 0、schema 合法、
        pid/线程/bg-worker 名正确（CLI 薄壳到 cpython 遍历的全链路）。"""
        proc, pid = run_cli_with_target(
            "target_app.py", "stack", "-p", "__PID__", "--json")
        assert proc.returncode == 0, proc.stderr
        doc = json.loads(proc.stdout)
        assert doc["process"]["pid"] == pid
        names = [t["name"] for t in doc["threads"]]
        assert "bg-worker" in names

    def test_stack_text_has_thread_header(self):
        """端到端：文本输出含进程头与 Thread 块（复刻格式在真实管道中）。"""
        proc, pid = run_cli_with_target(
            "target_app.py", "stack", "-p", "__PID__")
        assert proc.returncode == 0, proc.stderr
        assert f"Process {pid}:" in proc.stdout
        assert "Thread " in proc.stdout

    def test_stack_dead_pid_rc1(self):
        """端到端失败模式：不存在的 pid → rc=1 + `[!]` 到 stderr。"""
        proc = run_cli("stack", "-p", str(1 << 22))
        assert proc.returncode == 1
        assert "[!]" in proc.stderr


class TestSyscallCliLive:
    def test_syscall_max_events(self):
        """端到端：`syscall --max-events 2` 捕获事件后自动退出 rc=0
        （ptrace 引擎经 CLI 全链路）。"""
        proc, _ = run_cli_with_target(
            "fast_syscall_app.py", "syscall", "-p", "__PID__",
            "--max-events", "2")
        assert proc.returncode == 0, proc.stderr
        assert "(" in proc.stdout and " = " in proc.stdout


class TestRecordCliLive:
    def test_record_duration_writes_folded(self, tmp_path):
        """端到端：`record -d 0.5 -o file` 产出 folded 文件 + stderr 摘要
        （采样引擎经 CLI 全链路；≤15s 预算内）。spin_app 保证有 R 态
        burn 帧——sleep-only 的 target_app 全 idle，folded 必空
        （旧 conftest 文档化教训）。"""
        out = tmp_path / "out.folded"
        proc, _ = run_cli_with_target(
            "spin_app.py", "record", "-p", "__PID__", "-r", "100",
            "-d", "0.5", "-o", str(out))
        assert proc.returncode == 0, proc.stderr
        folded = out.read_text()
        assert "burn" in folded
        assert "[i] pyprobe recorded" in proc.stderr


class TestVersionFlag:
    def test_version_flag(self):
        """端到端：--version 退出码 0 且输出 pyprobe 前缀。"""
        proc = run_cli("--version")
        assert proc.returncode == 0
        assert proc.stdout.startswith("pyprobe ")
