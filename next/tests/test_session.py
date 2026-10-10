"""Contract tests for observe/session.py (SE1–SE6).

Lower layers are monkeypatched at the session module's import seams —
session 的职责是组合与不变量一次性解析，下层行为由各自批次测试守护。
"""

import pytest

import pyprobe.observe.session as sess_mod
from pyprobe import dto
from pyprobe.errors import SymbolNotFound
from pyprobe.kernel.mem import Transport
from pyprobe.kernel.views import SnapshotView
from pyprobe.observe.session import open_session
from pyprobe.target.layout import DEFAULT_VERSION, Layout

PID = 4242
EXE = "/usr/bin/python3.12"
RUNTIME = 0x555000
INTERP = 0x666000


@pytest.fixture
def seams(monkeypatch):
    """Wire the common happy-path seams; tests override what they exercise."""
    monkeypatch.setattr(sess_mod, "exe_path_of", lambda pid: EXE)
    monkeypatch.setattr(sess_mod, "find_symbol",
                        lambda exe, name, pid: RUNTIME)
    monkeypatch.setattr(sess_mod, "detect_version", lambda exe: "3.12.13")
    monkeypatch.setattr(sess_mod, "resolve_interpreter",
                        lambda view, layout, runtime: INTERP)
    monkeypatch.setattr(sess_mod, "resolve_trampoline",
                        lambda view, layout, interp: 0x777000)
    monkeypatch.setattr(sess_mod, "get_thread_names",
                        lambda view, layout, interp: {11: "worker"})
    monkeypatch.setattr(sess_mod, "read_cmdline",
                        lambda pid: "python3 app.py")


class TestOpenFlow:
    def test_resolves_all_invariants(self, seams):
        """SE1/SE4/SE6：open 一次性解析全部存活期不变量并落盘到 Session
        （下层解析结果被忠实搬运，layout 随会话显式携带——A1 落点）。"""
        s = open_session(PID)
        assert (s.pid, s.exe_path, s.runtime_addr) == (PID, EXE, RUNTIME)
        assert s.interp_addr == INTERP
        assert s.trampoline_addr == 0x777000
        assert s.names == {11: "worker"}
        assert s.version_warning is None
        assert isinstance(s.layout, Layout)
        assert s.layout.key == "3.12" and s.layout.verified is True
        assert s.proc_info == dto.ProcessInfo(
            pid=PID, cmdline="python3 app.py", exe_path=EXE,
            python_version="3.12.13")

    def test_symbol_not_found(self, seams, monkeypatch):
        """SE1 失败模式：_PyRuntime 缺失 → SymbolNotFound 带符号与路径
        （CLI `[!]` 诊断链，属性 pin 见 errors 契约）。"""
        monkeypatch.setattr(sess_mod, "find_symbol", lambda exe, name, pid: 0)
        with pytest.raises(SymbolNotFound) as ei:
            open_session(PID)
        assert ei.value.symbol == "_PyRuntime"
        assert ei.value.exe_path == EXE

    def test_default_view_factory_builds_snapshot_view(self, seams):
        """SE5：默认 view_factory 产出 SnapshotView(Transport)（A4 单次
        快照语义的默认声明）。"""
        s = open_session(PID)
        view = s.view_factory(PID)
        assert isinstance(view, SnapshotView)
        assert isinstance(view.transport, Transport)
        assert view.transport.pid == PID

    def test_view_factory_injection(self, seams):
        """SE5：注入点——自定义 factory 原样入会话（测试/特殊观测的通道）。"""
        marker = object()
        s = open_session(PID, view_factory=lambda pid: marker)
        assert s.view_factory(PID) is marker


class TestVersionWarning:
    def test_unverified_version_warning_verbatim(self, seams, monkeypatch):
        """SE2（逐字复刻）：未验证版本 → "[!] " + VersionNotSupported 消息，
        且 layout 为 fallback（verified=False，A1 契约承接点）。"""
        monkeypatch.setattr(sess_mod, "detect_version", lambda exe: "9.9.1")
        s = open_session(PID)
        assert s.version_warning == (
            "[!] CPython 9.9.1 is not a verified version; "
            "falling back to 3.12 offsets (output may be incorrect)")
        assert s.layout.verified is False
        assert s.layout.key == DEFAULT_VERSION
        assert s.proc_info.python_version == "9.9.1"

    def test_unknown_version_warning_verbatim(self, seams, monkeypatch):
        """SE2（逐字复刻）：Py_Version 读不出（"?"）→ 专用"无法判定"文案，
        与未验证版本文案区分（两种失败对用户含义不同）。"""
        monkeypatch.setattr(sess_mod, "detect_version", lambda exe: "?")
        s = open_session(PID)
        assert s.version_warning == (
            "[!] Warning: cannot determine target CPython version, "
            "using default offsets — output may be incorrect.")
        assert s.layout.verified is False
        assert s.proc_info.python_version == "?"

    def test_verified_version_no_warning(self, seams):
        """SE2：已验证版本 → None（无 warning 字段污染输出）。"""
        assert open_session(PID).version_warning is None


class TestCmdlineFallback:
    def test_cmdline_none_falls_back_to_exe(self, seams, monkeypatch):
        """SE3：/proc cmdline 读不出（zombie 窗口期）→ exe_path 兜底，
        proc_info 不留空 cmdline。"""
        monkeypatch.setattr(sess_mod, "read_cmdline", lambda pid: None)
        s = open_session(PID)
        assert s.proc_info.cmdline == EXE
