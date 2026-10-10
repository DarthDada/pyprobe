"""/proc metadata helpers — cmdline, task enumeration, thread names.

契约（contracts.md 批 1 表 P1–P4，含 A7 琐碎项修复 P3）。
当前为骨架：函数返回哨兵，批次 1 实现填实。
"""

import os

from ..errors import PermissionDenied, ProcessNotFound


def read_cmdline(pid: int) -> str | None:
    """Read ``/proc/<pid>/cmdline`` as a space-joined UTF-8 string.

    NUL bytes become spaces, trailing spaces stripped, undecodable bytes
    replaced (P1). Returns ``None`` on any ``OSError`` (no such process,
    permission, …).
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            data = f.read()
        return data.replace(b"\x00", b" ").rstrip(b" ").decode("utf-8", "replace")
    except OSError:
        return None


def list_tids(pid: int) -> list[int]:
    """Return the sorted thread ids of ``pid`` from ``/proc/<pid>/task``.

    Raises ``ProcessNotFound`` when the process is gone (P2) and
    ``PermissionDenied`` when /proc is unreadable (P3 — A7 琐碎项修复:
    the old tree let PermissionError escape as a raw traceback).
    """
    try:
        return sorted(int(x) for x in os.listdir(f"/proc/{pid}/task"))
    except FileNotFoundError:
        raise ProcessNotFound(pid) from None
    except PermissionError:
        raise PermissionDenied(pid) from None


def read_comm(pid: int, tid: int) -> str:
    """Read ``/proc/<pid>/task/<tid>/comm``, whitespace-stripped.

    Returns ``""`` on any ``OSError`` (P4).
    """
    try:
        with open(f"/proc/{pid}/task/{tid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""
