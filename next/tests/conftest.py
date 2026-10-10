"""Dual-tree import guard (TODO §10.4-0).

Both trees ship a package named ``pyprobe`` and share one venv. The root
project is installed into that venv as an *editable* install, and setuptools'
editable finder sits on ``sys.meta_path`` and maps ``pyprobe.<module>``
lookups back to the legacy tree — even when the parent package resolved to
``next/pyprobe``. Left in place, a missing next-side module would silently
fall back to frozen legacy code, defeating the dual-tree isolation.

Purging that finder here (conftest runs before any test module imports
``pyprobe``) makes the legacy tree unreachable from the next suite;
``test_scaffold.py`` pins the resulting isolation mechanically.
"""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _purge_legacy_editable_finder() -> None:
    for finder in list(sys.meta_path):
        module = getattr(finder, "__module__", "") or ""
        if "editable" in module.lower() or "editable" in type(finder).__name__.lower():
            sys.meta_path.remove(finder)
    # A repo-root sys.path entry (e.g. from a non-strict editable .pth) would
    # also expose the legacy tree; drop it for the same reason.
    for entry in list(sys.path):
        if entry and Path(entry).resolve() == _REPO_ROOT:
            sys.path.remove(entry)
    # If anything imported pyprobe before this guard ran, force re-resolution.
    for name in [m for m in sys.modules if m == "pyprobe" or m.startswith("pyprobe.")]:
        del sys.modules[name]


_purge_legacy_editable_finder()


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
    import os
    import subprocess
    import sys

    if sys.platform != "linux":
        pytest.skip("integration tests require Linux process_vm_readv")

    interpreter = os.environ.get("TARGET_PYTHON") or sys.executable
    script = Path(__file__).parent / "targets" / "target_app.py"
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
