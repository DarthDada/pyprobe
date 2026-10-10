"""Contract tests for kernel/procfs.py (P1–P4, incl. the A7 trivial fix P3).

Real /proc is used where possible (self pid); failure paths are forced by
monkeypatching ``os.listdir`` — the exact syscall boundary the old tree got
wrong (PermissionError escaping as a traceback, old §9 P1-9).
"""

import os

import pytest

from pyprobe.errors import PermissionDenied, ProcessNotFound
from pyprobe.kernel.procfs import list_tids, read_cmdline, read_comm

DEAD_PID = 1 << 22  # beyond the default kernel pid_max — never a live process


class TestReadCmdline:
    def test_self_cmdline_is_decodable(self):
        """P1：真实 /proc 自检——本进程 cmdline 可读、NUL 已替换、无残留空串。"""
        out = read_cmdline(os.getpid())
        assert isinstance(out, str) and out
        assert "\x00" not in out

    def test_dead_pid_returns_none(self):
        """P1 失败模式：进程不存在 → None（与失败模式成对的边界）。"""
        assert read_cmdline(DEAD_PID) is None

    def test_nul_becomes_space_and_trailing_stripped(self, monkeypatch, tmp_path):
        """P1 格式 pin：NUL→空格、尾部空格去除（权威格式：proc(5) NUL 分隔
        argv 带尾 NUL；用合成文件精确复现）。"""
        import builtins

        real_open = builtins.open

        def fake_open(path, mode="r", *a, **kw):
            if path == f"/proc/{DEAD_PID}/cmdline":
                return real_open(tmp_path / "cmdline", mode, *a, **kw)
            return real_open(path, mode, *a, **kw)

        (tmp_path / "cmdline").write_bytes(b"python3\x00-x\x00print(1)\x00")
        monkeypatch.setattr(builtins, "open", fake_open)
        assert read_cmdline(DEAD_PID) == "python3 -x print(1)"


class TestListTids:
    def test_self_tids_sorted_and_nonempty(self):
        """P2：真实 /proc 自检——tid 列表升序、全 int、至少含主线程
        （升序是引擎 seize 顺序与输出线程序的复刻约束来源）。"""
        tids = list_tids(os.getpid())
        assert tids == sorted(tids)
        assert all(isinstance(t, int) for t in tids)
        assert tids  # every process has at least its main thread

    def test_dead_pid_raises_process_not_found(self):
        """P2 失败模式：进程不存在 → ProcessNotFound 且带 pid 属性。"""
        with pytest.raises(ProcessNotFound) as ei:
            list_tids(DEAD_PID)
        assert ei.value.pid == DEAD_PID

    def test_permission_error_raises_permission_denied(self, monkeypatch):
        """P3 (A7 fix): /proc permission failure must not escape as a raw
        PermissionError traceback — it becomes PermissionDenied."""
        def denied(path):
            raise PermissionError(13, "Permission denied", path)

        monkeypatch.setattr(os, "listdir", denied)
        with pytest.raises(PermissionDenied) as ei:
            list_tids(1234)
        assert ei.value.pid == 1234

    def test_file_not_found_raises_process_not_found(self, monkeypatch):
        """P2 失败路径精确性：FileNotFoundError 映射 ProcessNotFound——与 P3
        的 PermissionError→PermissionDenied 区分两种缺失语义，防混淆。"""
        def gone(path):
            raise FileNotFoundError(2, "No such file or directory", path)

        monkeypatch.setattr(os, "listdir", gone)
        with pytest.raises(ProcessNotFound):
            list_tids(1234)


class TestReadComm:
    def test_self_comm_nonempty(self):
        """P4：真实 /proc 自检——本进程主线程 comm 可读非空。"""
        pid = os.getpid()
        assert read_comm(pid, pid) != ""

    def test_dead_tid_returns_empty(self):
        """P4 失败模式：tid 不存在 → ""（native 线程头显示容忍无名线程）。"""
        assert read_comm(os.getpid(), DEAD_PID) == ""

    def test_dead_pid_returns_empty(self):
        """P4 失败模式：整个进程不存在 → ""（与 dead tid 成对的边界）。"""
        assert read_comm(DEAD_PID, DEAD_PID) == ""
