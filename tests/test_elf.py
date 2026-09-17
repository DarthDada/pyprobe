"""Unit tests for pyprobe.elf — ELF parsing, symbol lookup, version decode."""

import os
import struct
import sys

import pytest

from pyprobe.elf import decode_py_version, read_cmdline, find_symbol, read_const


class TestDecodePyVersion:
    def test_3_12_13(self):
        hexval = (3 << 24) | (12 << 16) | (13 << 8)
        assert decode_py_version(hexval) == "3.12.13"

    def test_3_11_0(self):
        hexval = (3 << 24) | (11 << 16) | (0 << 8)
        assert decode_py_version(hexval) == "3.11.0"

    def test_3_0_0(self):
        assert decode_py_version((3 << 24) | (0 << 16)) == "3.0.0"

    def test_known_py_version(self):
        """Decode the actual running interpreter's Py_Version."""
        version = sys.version_info
        hexval = (version.major << 24) | (version.minor << 16) | (version.micro << 8)
        assert decode_py_version(hexval) == f"{version.major}.{version.minor}.{version.micro}"


class TestReadCmdline:
    def test_self_cmdline(self):
        """read_cmdline on our own PID returns a non-empty string."""
        cmdline = read_cmdline(os.getpid())
        assert cmdline is not None
        assert "python" in cmdline.lower() or len(cmdline) > 0

    def test_nonexistent_pid(self):
        assert read_cmdline(0xFFFFFFF) is None


@pytest.fixture(scope="module")
def exe_path():
    return os.readlink("/proc/self/exe")


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
        version = decode_py_version(int.from_bytes(data, "little"))
        info = sys.version_info
        assert version == f"{info.major}.{info.minor}.{info.micro}"

    def test_read_missing_const(self, exe_path):
        assert read_const(exe_path, "__nope__", 8) is None
