"""Test suite fixtures.

Post-§10.6 the dual-tree isolation guard is retired (a single tree remains).
What stays is the layout-override isolation (contract L12, §10.2-7): the
autouse fixture points ``target.layout``'s default dev override at a
nonexistent path so the suite always validates the built-in offsets table,
never a tracked ``offsets.json``. Tests that pass ``overrides_path``
explicitly (the A1 injection point) are unaffected.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_layout_overrides(monkeypatch):
    """契约 L12（§10.2-7 override 隔离）：把 target.layout 的默认 dev
    override 路径指向不存在文件，防套件静默验证 tracked offsets.json 而非
    内置表——显式 overrides_path 的测试不受此影响（A1 显式传参的意义）。"""
    import pyprobe.target.layout as layout_mod

    monkeypatch.setattr(layout_mod, "_DEFAULT_OVERRIDES_PATH",
                        "/nonexistent/pyprobe-offsets-override.json")


@pytest.fixture(scope="session")
def target_pid():
    """契约 E2E1：spawn target_app 并 yield 其 PID。

    就绪经子进程 stdout 的 READY 行握手（就绪轮询替代固定 sleep,
    §10.2-7）——该行在 bg-worker 线程启动后才打印，fixture 返回时目标
    线程图已完整。子进程是 pytest 的后代，默认 ptrace_scope=1 下
    process_vm_readv 可用。TARGET_PYTHON 选择目标解释器（跨版本端到端，
    pyprobe 自身仍跑 venv 解释器）。
    """
    yield from _spawn_target("target_app.py")


@pytest.fixture(scope="session")
def fast_syscall_pid():
    """§10.2-7 fast target：0.2s 级 clock_nanosleep 循环（syscall 追踪
    集成测试用，保住 elapsed≥0.1s 断言且事件窗口快）。"""
    yield from _spawn_target("fast_syscall_app.py")


def _spawn_target(script_name):
    """Shared spawn/READY-handshake/teardown for target fixtures (E2E1)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    if sys.platform != "linux":
        pytest.skip("integration tests require Linux process_vm_readv")

    interpreter = os.environ.get("TARGET_PYTHON") or sys.executable
    script = Path(__file__).parent / "targets" / script_name
    child = subprocess.Popen(
        [interpreter, str(script)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = child.stdout.readline()  # 阻塞至 READY 或 EOF（即就绪轮询）
        if not line.startswith("READY"):
            pytest.skip(f"target process failed to start: {line!r}")
        yield int(line.split()[1])
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        child.stdout.close()
