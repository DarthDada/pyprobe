"""Unit tests for pyprobe.procmeta — process metadata helpers.

Covers the functions moved out of elf.py (TODO §8.6): ``read_cmdline``
(reads /proc/<pid>/cmdline) and ``decode_py_version`` (decodes the
CPython ``Py_Version`` uint into a dotted triple).
"""

import os
import sys

from pyprobe.procmeta import decode_py_version, read_cmdline


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
        assert decode_py_version(hexval) == (
            f"{version.major}.{version.minor}.{version.micro}")


class TestReadCmdline:
    def test_self_cmdline(self):
        """read_cmdline on our own PID returns a non-empty string."""
        cmdline = read_cmdline(os.getpid())
        assert cmdline is not None
        assert "python" in cmdline.lower() or len(cmdline) > 0

    def test_nonexistent_pid(self):
        assert read_cmdline(0xFFFFFFF) is None
