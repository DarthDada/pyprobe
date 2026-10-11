"""Package layout guards (post-swap, contracts.md §10.6).

The dual-tree isolation guard retired with the legacy tree; what remains
worth pinning mechanically:

* the package resolves inside this repo (not a stray site-packages copy);
* ``offsets.json`` is in place and versioned (the layout contract's input);
* legacy module names never reappear — ADR A8 confines the new architecture
  to kernel/ target/ cpython/ observe/ present/.

The ``pyprobe.offsets`` facade is the contracts.md §10.5-2 (API3) sanctioned exception
(read-only, no ``configure`` global side effect, guarded by test_structure).
"""

import importlib.util
from pathlib import Path

import pyprobe

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_package_resolves_inside_repo():
    """包解析根守护：import pyprobe 必须解析到本仓库——解析到别处意味着
    测试在静默验证另一份代码，结果不可信。"""
    assert Path(pyprobe.__file__).resolve().is_relative_to(REPO_ROOT)


def test_offsets_json_present_and_versioned():
    """offsets.json 在位且带 _version（layout 契约的输入，缺失会以诡异
    方式失败）。"""
    offsets = REPO_ROOT / "pyprobe" / "offsets.json"
    assert offsets.is_file()
    assert '"_version"' in offsets.read_text(encoding="utf-8")


def test_legacy_modules_are_not_importable():
    """架构守护（ADR A8）：旧模块名永不可导入——其重现意味着未授权的旧
    代码搬运。新架构模块住 kernel/ target/ cpython/ observe/ present/。
    例外：pyprobe.offsets 是 contracts.md §10.5-2 API 契约批准的兼容门面
    （只读、无 configure 全局副作用，由 test_structure.py 守护）。"""
    for legacy in ("stack_dump", "sampler", "syscall_tracer", "native_dump"):
        assert importlib.util.find_spec(f"pyprobe.{legacy}") is None
