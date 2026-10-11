"""Contract tests for target/identity.py (I1–I4)."""

import os
import sys

import pytest

from pyprobe.errors import ProcessNotFound
from pyprobe.target.identity import (
    UNKNOWN_VERSION,
    decode_py_version,
    detect_version,
    exe_path_of,
)

DEAD_PID = 1 << 22  # beyond the default kernel pid_max — never a live process


class TestDecodePyVersion:
    """I1: Py_Version 位布局（major 24-31 / minor 16-23 / micro 8-15）——
    版本判定的一切下游（layout 选择、版本告警）都建立在这三个字段上。"""

    def test_3_12_13(self):
        hexval = (3 << 24) | (12 << 16) | (13 << 8)
        assert decode_py_version(hexval) == "3.12.13"

    def test_3_11_0(self):
        hexval = (3 << 24) | (11 << 16) | (0 << 8)
        assert decode_py_version(hexval) == "3.11.0"

    def test_3_0_0(self):
        """I1 边界：minor/micro 为 0 不丢位（位移掩码错误的经典暴露点）。"""
        assert decode_py_version((3 << 24) | (0 << 16)) == "3.0.0"

    def test_release_level_dropped(self):
        """I1：低字节 release-level flags 必须丢弃（3.12.13 final 的 0xF0
        不得渗入版本串）。"""
        hexval = (3 << 24) | (12 << 16) | (13 << 8) | 0xF0
        assert decode_py_version(hexval) == "3.12.13"


class TestDecodePyVersionSpecOracle:
    def test_running_interpreter_roundtrip(self):
        """I2 (spec-oracle)：用运行中解释器的 sys.version_info 重组 hexval，
        解码必须回到同一版本串——编码规则与 CPython 实际打包方式互证。"""
        v = sys.version_info
        hexval = (v.major << 24) | (v.minor << 16) | (v.micro << 8)
        assert decode_py_version(hexval) == f"{v.major}.{v.minor}.{v.micro}"


class TestExePathOf:
    def test_self_exe_path(self):
        """I3：真实 /proc 自检——本进程 exe 解析结果与 readlink 一致。"""
        assert exe_path_of(os.getpid()) == os.readlink("/proc/self/exe")

    def test_dead_pid_raises_process_not_found(self):
        """I3 失败模式：进程不存在 → ProcessNotFound 且带 pid（CLI `[!]`
        诊断链的起点）。"""
        with pytest.raises(ProcessNotFound) as ei:
            exe_path_of(DEAD_PID)
        assert ei.value.pid == DEAD_PID


class TestDetectVersion:
    def test_real_interpreter(self):
        """I4 (spec-oracle)：真实解释器二进制的 Py_Version 常量解码 ==
        platform 版本——identity 全链路（read_const→小端→位解码）互证。"""
        exe = os.readlink("/proc/self/exe")
        v = sys.version_info
        assert detect_version(exe) == f"{v.major}.{v.minor}.{v.micro}"

    def test_unreadable_returns_unknown_sentinel(self, monkeypatch):
        """I4 失败模式：Py_Version 读不到 → "?" 哨兵（ProcessInfo 版本
        未知态的语义来源；哨兵可辨识，不与任何真实版本串混淆）。"""
        monkeypatch.setattr(
            "pyprobe.target.identity.read_const", lambda *a: None)
        assert detect_version("/nonexistent/python") == UNKNOWN_VERSION
