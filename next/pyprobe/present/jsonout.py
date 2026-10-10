"""Machine-readable JSON output (schema 复刻约束, contracts.md 批 6 JS1/JS2).

All three documents are ``json.dumps(dataclasses.asdict(...), indent=2,
ensure_ascii=False) + "\\n"`` — never colored; full paths (shortening is
a text-layer concern).
"""

import json
from dataclasses import asdict

from .. import dto


def format_process_json(proc_info: dto.ProcessInfo,
                        threads: list[dto.ThreadInfo]) -> str:
    """``{"process": …, "threads": […]}`` (JS1/JS2)."""
    return json.dumps(
        {"process": asdict(proc_info),
         "threads": [asdict(t) for t in threads]},
        indent=2, ensure_ascii=False) + "\n"


def format_native_json(pid: int, cmdline: str,
                       threads: list[dto.NativeThreadInfo]) -> str:
    """``{"process": {"pid", "cmdline"}, "threads": […]}`` — the native
    path carries no CPython metadata (JS2, 旧注释语义)."""
    return json.dumps(
        {"process": {"pid": pid, "cmdline": cmdline},
         "threads": [asdict(t) for t in threads]},
        indent=2, ensure_ascii=False) + "\n"


def format_syscalls_json(events: list[dto.SyscallEvent]) -> str:
    """``{"events": […]}`` (JS1/JS2)."""
    return json.dumps(
        {"events": [asdict(ev) for ev in events]},
        indent=2, ensure_ascii=False) + "\n"
