"""Contract tests for present/jsonout.py (JS1/JS2, schema 复刻约束)."""

import json

from pyprobe import dto
from pyprobe.present.jsonout import (
    format_native_json,
    format_process_json,
    format_syscalls_json,
)


def _proc():
    return dto.ProcessInfo(pid=100, cmdline="python3 app.py",
                           exe_path="/usr/bin/python3.12",
                           python_version="3.12.13")


class TestWrappingShape:
    """JS1：三函数共享的包装形态——indent=2、ensure_ascii=False、尾换行。"""

    def test_indent_two_and_trailing_newline(self):
        out = format_process_json(_proc(), [])
        assert out.endswith("}\n")
        assert '\n  "process": {' in out  # indent=2 的物理证据
        json.loads(out)  # 合法 JSON

    def test_non_ascii_preserved(self):
        """JS1：ensure_ascii=False——非 ASCII 字符串不转义（人读性）。"""
        p = dto.ProcessInfo(pid=1, cmdline="python3 应用.py", exe_path="/x")
        assert "应用" in format_process_json(p, [])


class TestProcessJson:
    def test_process_and_threads_keys(self):
        """JS2：{"process":…,"threads":[…]} 键集与 dto asdict 一致（D1 的
        数据面在此封装）。"""
        t = dto.ThreadInfo(native_tid=101, name="main", frames=[
            dto.FrameInfo("run", "app/main.py", 10)])
        doc = json.loads(format_process_json(_proc(), [t]))
        assert set(doc) == {"process", "threads"}
        assert doc["process"]["pid"] == 100
        assert doc["process"]["python_version"] == "3.12.13"
        assert doc["threads"][0]["native_tid"] == 101
        assert doc["threads"][0]["frames"][0]["name"] == "run"

    def test_full_paths_not_shortened(self):
        """JS2：JSON 保留全路径（缩短是 text 层的显示关切，数据不丢）。"""
        t = dto.ThreadInfo(native_tid=1, frames=[
            dto.FrameInfo("run", "/a/b/c/d/runners.py", 1)])
        doc = json.loads(format_process_json(_proc(), [t]))
        assert doc["threads"][0]["frames"][0]["filename"] == \
            "/a/b/c/d/runners.py"


class TestNativeJson:
    def test_process_carries_pid_and_cmdline_only(self):
        """JS2：native 路径无 CPython 元数据——process 仅 pid/cmdline
        （旧注释语义：那些是 Python 栈概念）。"""
        t = dto.NativeThreadInfo(tid=101, comm="python3", frames=[
            dto.NativeFrame(pc=0x1000, symbol="main",
                            module="/usr/bin/python3.12")])
        doc = json.loads(format_native_json(100, "python3 app.py", [t]))
        assert doc["process"] == {"pid": 100, "cmdline": "python3 app.py"}
        assert doc["threads"][0]["frames"][0]["pc"] == 0x1000
        assert doc["threads"][0]["frames"][0]["symbol"] == "main"


class TestSyscallsJson:
    def test_events_list(self):
        """JS2：{"events":[…]}，事件键集含 rendered/error/elapsed。"""
        ev = dto.SyscallEvent(tid=1234, nr=257, name="openat",
                              rendered='AT_FDCWD, "/tmp/x"', ret=3,
                              elapsed=0.000123)
        doc = json.loads(format_syscalls_json([ev]))
        assert set(doc) == {"events"}
        (e,) = doc["events"]
        assert e["name"] == "openat"
        assert e["rendered"] == 'AT_FDCWD, "/tmp/x"'
        assert e["error"] is None
        assert e["elapsed"] == 0.000123
