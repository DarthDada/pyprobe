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
    assert Path(pyprobe.__file__).resolve().is_relative_to(NEXT_ROOT)


def test_offsets_json_is_copied_and_versioned():
    offsets = NEXT_ROOT / "pyprobe" / "offsets.json"
    assert offsets.is_file()
    assert '"_version"' in offsets.read_text(encoding="utf-8")


def test_legacy_modules_are_not_importable():
    # Legacy module names must stay absent from the next tree for the whole
    # rebuild: their reappearance means either a cross-tree import leak or an
    # unauthorized port from the frozen tree (both are §10 rule violations).
    # New-architecture modules live under kernel/ target/ cpython/ observe/
    # present/ cli (ADR A8) and never reuse these names.
    for legacy in ("stack_dump", "sampler", "syscall_tracer", "native_dump", "offsets"):
        assert importlib.util.find_spec(f"pyprobe.{legacy}") is None
