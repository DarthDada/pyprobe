"""端到端里程碑（contracts.md 批 4 表 E2E2/E2E3，§10.2-4 首批）：

open_session + collect_snapshot 对 live target 出栈。目标线程图由
tests/targets/target_app.py 提供（main 睡眠 + bg-worker 睡眠线程），
就绪经 READY 行握手（fixture 守护）。
"""

import sys

import pytest

from pyprobe.observe.session import open_session
from pyprobe.observe.snapshot import collect_snapshot

pytestmark = pytest.mark.integration


class TestOpenSessionLive:
    def test_session_resolves_live_target(self, target_pid):
        """E2E2：真实进程全解析——版本已验证、无 warning、interp 非 0、
        proc_info 版本与目标解释器一致（TARGET_PYTHON 默认即本解释器）。"""
        s = open_session(target_pid)
        assert s.interp_addr != 0
        assert s.layout.verified is True
        assert s.version_warning is None
        v = sys.version_info
        import os
        if not os.environ.get("TARGET_PYTHON"):
            assert s.proc_info.python_version == (
                f"{v.major}.{v.minor}.{v.micro}")

    def test_thread_names_resolved_live(self, target_pid):
        """E2E2/N 系集成：threading._active 遍历在真实解释器上解析出
        bg-worker 名（单元级矩阵之外的活体验证）。"""
        s = open_session(target_pid)
        assert "bg-worker" in s.names.values()


class TestCollectSnapshotLive:
    def test_snapshot_threads(self, target_pid):
        """E2E3：≥2 线程、bg-worker 名解析、native_tid 升序、主线程
        idle（双启发式在活体上的表现）。"""
        s = open_session(target_pid)
        threads = collect_snapshot(s)
        assert len(threads) >= 2
        tids = [t.native_tid for t in threads]
        assert tids == sorted(tids)
        by_name = {t.name: t for t in threads}
        assert "bg-worker" in by_name
        main = [t for t in threads if t.native_tid == target_pid]
        assert main and main[0].idle is True

    def test_snapshot_frames_reference_target(self, target_pid):
        """E2E3：bg-worker 帧含 worker 函数与 target_app.py 文件名
        （帧遍历、code 头、行号解析的活体互证）；行号为正。"""
        s = open_session(target_pid)
        threads = collect_snapshot(s)
        worker = next(t for t in threads if t.name == "bg-worker")
        names = [f.name for f in worker.frames]
        files = [f.filename for f in worker.frames]
        assert "worker" in names
        assert any(f and f.endswith("target_app.py") for f in files)
        assert all(f.line > 0 for f in worker.frames)

    def test_repeated_snapshots_stable(self, target_pid):
        """E2E3/A4 活体：两次快照结构一致（每次新建 SnapshotView，
        结果可复现）。"""
        s = open_session(target_pid)
        a = collect_snapshot(s)
        b = collect_snapshot(s)
        assert [t.native_tid for t in a] == [t.native_tid for t in b]
        assert [[f.name for f in t.frames] for t in a] == \
               [[f.name for f in t.frames] for t in b]
