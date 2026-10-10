"""Batch 0 scaffold guards (TODO §10.4-0).

The dual-tree phase is only safe if ``import pyprobe`` inside the next suite
can never resolve to the frozen legacy tree. These tests pin that isolation
mechanically; everything else in the rebuild relies on it.
"""

import importlib.util
from pathlib import Path

import pyprobe

NEXT_ROOT = Path(__file__).resolve().parent.parent


def test_package_resolves_inside_next_tree():
    """双树隔离根守护：import pyprobe 必须解析到 next/pyprobe——解析到旧树
    意味着整个重构期的测试都在静默测旧代码，一切结果不可信。"""
    assert Path(pyprobe.__file__).resolve().is_relative_to(NEXT_ROOT)


def test_offsets_json_is_copied_and_versioned():
    """批次 0 交付物守护：offsets.json 在位且带 _version（批次 2 layout
    契约的输入，缺失会在彼时以诡异方式失败）。"""
    offsets = NEXT_ROOT / "pyprobe" / "offsets.json"
    assert offsets.is_file()
    assert '"_version"' in offsets.read_text(encoding="utf-8")


def test_legacy_modules_are_not_importable():
    """冻结铁律守护：旧模块名在 next 树永不可导入——其重现意味着跨树
    泄漏或未经授权的旧代码搬运（均为 §10 违规）。新架构模块住
    kernel/ target/ cpython/ observe/ present/ cli（ADR A8），不复用这些名字。
    例外：pyprobe.offsets 是 §10.5-2 API 契约（API3）批准的兼容门面
    （只读、无 configure 全局副作用，由 test_structure.py 守护），
    非旧模块回归。"""
    for legacy in ("stack_dump", "sampler", "syscall_tracer", "native_dump"):
        assert importlib.util.find_spec(f"pyprobe.{legacy}") is None
