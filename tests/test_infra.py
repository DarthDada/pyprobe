"""Structural guards for engineering infrastructure (原 TODO §6).

Mechanically pin the process guards AGENTS.md mandates but that are easy to
erode by hand:

* git hooks exist, are executable, and delegate to ``scripts/`` (no bare
  pytest/ruff — the "命令一律走 scripts/" rule);
* CI covers the cross-version matrix (3.11–3.14 ``TARGET_PYTHON``; the
  mechanism lives in conftest but was idle in CI) and keeps orchestration
  inside ``scripts/``.
"""

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_git_hooks_delegate_to_scripts():
    """基础设施守护（原 TODO §6-1）：pre-commit/pre-push 存在且可执行，且只经 scripts/ 层运行
    （防操作变形——同 AGENTS「命令一律走 scripts/」）；dev 环境按需经
    scripts/install_hooks.sh 启用，故测试只钉钩子本身而非 core.hooksPath。"""
    for name in ("pre-commit", "pre-push"):
        hook = REPO / ".githooks" / name
        assert hook.is_file(), f"missing git hook: {name}"
        assert os.access(hook, os.X_OK), f"git hook not executable: {name}"
        text = hook.read_text(encoding="utf-8")
        assert "scripts/run_tests.sh" in text, (
            f"{name} must run tests via scripts/run_tests.sh")
        assert not re.search(r"\bpytest\b|\bruff\b", text), (
            f"{name} must not call pytest/ruff directly")


def test_ci_workflow_has_cross_version_matrix():
    """基础设施守护（原 TODO §6-1）：CI 覆盖 3.11–3.14 的 TARGET_PYTHON 矩阵（机制在 conftest，
    此前 CI 闲置），且编排仍只经 scripts/（禁裸命令）。"""
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    for version in ("3.11", "3.12", "3.13", "3.14"):
        assert f'"{version}"' in workflow, f"CI matrix missing {version}"
    assert "matrix" in workflow
    assert "scripts/ci_target.sh" in workflow
    assert not re.search(r"\bpytest\b|\bruff\b", workflow)


def test_docs_contract_checker_passes():
    """A14 守护：文档契约检查器（scripts/check_docs.sh）必须全绿——文档规则
    不靠自觉，悬空引用/重复漂移的先例见 ADR A14（contracts.md）；与 import
    方向契约（test_structure.py）同享机械强制（契约回链：A14 D1–D5）。"""
    proc = subprocess.run(
        [str(REPO / "scripts" / "check_docs.sh")],
        capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stdout + proc.stderr
