"""CPython process identification and version detection.

Answers "is this a CPython process, and which version" from the target
binary: exe path resolution, ``Py_Version`` constant readout, and the
packed-uint decode. Layout selection based on the version lives in
``target/layout.py``; interpreter-graph resolution lives in observe/
(batch 4).

契约（contracts.md 批 2 表 I1–I4）。当前为骨架：函数返回哨兵，
批次 2 实现填实。
"""

import os

from ..errors import ProcessNotFound
from .symbols import read_const

UNKNOWN_VERSION = "?"  # 哨兵：ProcessInfo.python_version 默认值语义来源（I4）


def decode_py_version(hexval: int) -> str:
    """Decode a CPython ``Py_Version`` uint into ``"major.minor.micro"``.

    ``Py_Version`` packs the version into the top three bytes of a 32-bit
    field: ``major`` at bits 24-31, ``minor`` at 16-23, ``micro`` at 8-15
    (low byte holds release-level flags, intentionally dropped — 契约 I1).
    """
    major = (hexval >> 24) & 0xFF
    minor = (hexval >> 16) & 0xFF
    micro = (hexval >> 8) & 0xFF
    return f"{major}.{minor}.{micro}"


def exe_path_of(pid: int) -> str:
    """Resolve ``/proc/<pid>/exe``; OSError → ``ProcessNotFound(pid)`` (I3)."""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError as e:
        raise ProcessNotFound(pid) from e  # from 链保留底层诊断 (I3)


def detect_version(exe_path: str) -> str:
    """Detect the CPython version of the binary at ``exe_path``.

    Reads the ``Py_Version`` constant (8 bytes, little-endian) via
    ``target/symbols.read_const`` and decodes it (I4). Returns
    :data:`UNKNOWN_VERSION` when the constant is unreadable.
    """
    data = read_const(exe_path, "Py_Version", 8)
    if data is None:
        return UNKNOWN_VERSION  # I4 哨兵：ProcessInfo 版本未知态语义来源
    return decode_py_version(int.from_bytes(data, "little"))
