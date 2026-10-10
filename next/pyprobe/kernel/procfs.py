"""/proc metadata helpers — cmdline, task enumeration, thread names,
maps load-base parsing (P5, contracts.md 批 2).

契约（contracts.md 批 1 表 P1–P4，含 A7 琐碎项修复 P3；批 2 表 P5）。
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


def parse_load_base(maps_text: str, exe_path: str) -> int:
    """Parse a /proc/<pid>/maps text for the load base of ``exe_path``.

    Returns the start address of the **first** mapping whose path column
    equals ``exe_path`` (a " (deleted)" suffix is tolerated), hex-decoded;
    0 when no line matches (P5, contracts.md 批 2).
    """
    for line in maps_text.splitlines():
        path_start = line.find("/")
        if path_start < 0:
            continue  # 匿名映射无路径列，不参与匹配 (P5)
        path = line[path_start:].strip()
        if path == exe_path or path == exe_path + " (deleted)":
            # 首个匹配映射的起始地址即 ELF 加载基址 (P5)
            return int(line.split("-")[0], 16)
    return 0


def load_base(pid: int, exe_path: str) -> int:
    """Load base of ``exe_path`` in process ``pid``; 0 on any OSError (P5)."""
    try:
        with open(f"/proc/{pid}/maps") as f:
            return parse_load_base(f.read(), exe_path)
    except OSError:
        return 0
