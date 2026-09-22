"""Unit tests for pyprobe.elf — ELF parsing, symbol lookup.

``read_cmdline`` / ``decode_py_version`` used to live in elf.py; they now
live in ``procmeta.py`` and are covered by ``test_procmeta.py``.
"""

import os
import sys

import pytest

from pyprobe.elf import find_symbol, read_const


class TestFindSymbolRealPython:
    """Integration-style: look up symbols in the real CPython executable."""

    def test_find_pyruntime(self, exe_path):
        addr = find_symbol(exe_path, "_PyRuntime", os.getpid())
        assert addr != 0

    def test_find_missing_symbol(self, exe_path):
        assert find_symbol(exe_path, "__nonexistent_symbol__", os.getpid()) == 0


class TestReadConstRealPython:
    def test_read_py_version(self, exe_path):
        """Py_Version should decode to the running interpreter's version."""
        data = read_const(exe_path, "Py_Version", 8)
        assert data is not None
        assert len(data) == 8
        from pyprobe.procmeta import decode_py_version
        version = decode_py_version(int.from_bytes(data, "little"))
        info = sys.version_info
        assert version == f"{info.major}.{info.minor}.{info.micro}"

    def test_read_missing_const(self, exe_path):
        assert read_const(exe_path, "__nope__", 8) is None


@pytest.fixture(scope="module")
def exe_path():
    return os.readlink("/proc/self/exe")
