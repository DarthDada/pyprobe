"""Contract tests for the library API surface (API1–API3, §10.5-2 预落地).

API1 pins the exact ``__all__`` list of the old tree plus the ADR A1
exception ``resolve_layout`` — the batch-7 alignment gate (§10.5-2) diffs
this list against the legacy tree, and this test is its unit-level twin.
"""

import pyprobe
from pyprobe.observe.session import Session
from pyprobe.present import color as present_color
from pyprobe.target import layout as target_layout

#: The old tree's __all__, verbatim (复刻约束; 注释分组同旧).
_EXPECTED_ALL = [
    "FrameInfo", "ThreadInfo", "ProcessInfo",
    "NativeFrame", "NativeThreadInfo",
    "SyscallEvent", "ProfileData",
    "PyProbeError", "ProcessNotFound", "PermissionDenied", "SymbolNotFound",
    "NoInterpreterState", "NoThreadState", "VersionNotSupported",
    "AttachFailed", "UnsupportedArchitecture", "ProcessExited",
    "collect_python", "format_process", "dump_python",
    "collect_frames", "collect_thread", "format_process_json",
    "read_thread_chain", "is_thread_idle_by_stat",
    "ProcessSession", "resolve_process",
    "DEFAULT_VERSION",
    "collect_native", "format_native", "dump_native",
    "format_native_json",
    "collect_syscalls", "dump_syscalls", "format_summary",
    "format_syscalls_json",
    "TraceFilter", "SyscallStat",
    "Sampler",
    "collect_profile", "format_folded", "dump_record",
    "dump_top", "TopStats",
    "offsets",
    "colors",
]


class TestAllList:
    def test_all_matches_legacy_plus_resolve_layout(self):
        """API1：__all__ = 旧树名单（逐项、同序）+ A1 显式例外
        resolve_layout（§10.5-2 的单元级孪生守护）。"""
        assert set(pyprobe.__all__) == (
            set(_EXPECTED_ALL) | {"resolve_layout"})
        assert "resolve_layout" in pyprobe.__all__

    def test_star_import_resolves(self):
        """API1：__all__ 每个名字在包命名空间可解析（断链即 API 回退）。"""
        namespace = {}
        exec("from pyprobe import *  # noqa: F403\n", namespace)
        for name in pyprobe.__all__:
            assert name in namespace, name


class TestCompatMappings:
    """API2：旧名 → 新实现的绑定语义抽查（名单之外的语义不断裂）。"""

    def test_process_session_is_session(self):
        """API2：ProcessSession 绑定 observe.session.Session。"""
        assert pyprobe.ProcessSession is Session

    def test_resolve_process_is_open_session(self):
        """API2：resolve_process 绑定 open_session（会话解析单一实现）。"""
        assert pyprobe.resolve_process is pyprobe.open_session

    def test_offsets_facade_readonly(self):
        """API3：offsets 门面的只读面可用（get/get_or/DEFAULT_VERSION/
        supported_versions）——且无 configure（A1 全局副作用不复活）。"""
        assert pyprobe.offsets.DEFAULT_VERSION == "3.12"
        assert pyprobe.offsets.get("pointer_size") == 8
        assert pyprobe.offsets.get_or("no.such.key") is None
        assert "3.12" in pyprobe.offsets.supported_versions()
        assert not hasattr(pyprobe.offsets, "configure")

    def test_colors_facade(self):
        """API2：colors 门面即 present.color 的再出口。"""
        assert pyprobe.colors.yellow_bold is present_color.yellow_bold

    def test_resolve_layout_exception(self):
        """API2/A1：resolve_layout 绑定 target.layout 的实现。"""
        assert pyprobe.resolve_layout is target_layout.resolve_layout

    def test_collect_python_composition(self, monkeypatch):
        """API2：collect_python = open_session + collect_snapshot 组合
        （返回 (proc_info, threads) 的旧形状）。"""
        from pyprobe import dto

        marker_session = type("S", (), {
            "proc_info": dto.ProcessInfo(pid=1, cmdline="c", exe_path="/x"),
        })()
        monkeypatch.setattr(pyprobe, "open_session", lambda pid: marker_session)
        monkeypatch.setattr(pyprobe, "collect_snapshot", lambda s: ["T"])
        proc_info, threads = pyprobe.collect_python(1)
        assert proc_info is marker_session.proc_info
        assert threads == ["T"]
