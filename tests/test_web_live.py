"""Integration: web mode end-to-end（E2E-W1，批 8b）.

真子进程 + 真 Monitor + in-process 原生 ASGI 驱动（不经 uvicorn）拉
/api/events：首帧 stats 含目标函数名/线程名；terminate 目标后收到
exit 帧。目标为 R 态忙循环（busy_app）——采样帧必然含活跃帧链
（target_app 全线程睡眠态会被空闲剪枝，不适用）。

驱动方式说明（契约批 8b 实施期修订）：starlette 1.6 的 TestClient
HTTP 传输全量缓冲（portal.call 阻塞至 app 完成），增量 SSE 语义不可
经其观测——原生 ASGI send() 回调恰按产出逐块送达，是 in-process 的
确定化通道（契约原文的「TestClient（in-process，不经 uvicorn）」
意图不变）。

子进程纪律（AGENTS.md 手动探测约束）：subprocess.Popen 经 stdout
握手取 PID，finally 中 terminate() + wait(5)。
"""

import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pyprobe.web.app import create_app
from pyprobe.web.monitor import Monitor

pytestmark = pytest.mark.integration

TARGETS = Path(__file__).parent / "targets"

_SCOPE = {
    "type": "http", "asgi": {"version": "3.0"},
    "http_version": "1.1", "method": "GET", "scheme": "http",
    "path": "/api/events", "raw_path": b"/api/events",
    "query_string": b"", "root_path": "", "headers": [],
    "client": ("testclient", 50000), "server": ("testserver", 80),
}


def _spawn_busy():
    """E2E-W1：派生忙循环目标（conftest target 模式的本地复刻——
    需要测试中途 terminate，不复用 session 级 fixture）。"""
    if sys.platform != "linux":
        pytest.skip("integration tests require Linux process_vm_readv")
    child = subprocess.Popen(
        [sys.executable, str(TARGETS / "busy_app.py")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = child.stdout.readline()  # 阻塞至 READY（就绪轮询）
        if not line.startswith("READY"):
            pytest.skip(f"busy target failed to start: {line!r}")
        return child, int(line.split()[1])
    except BaseException:
        child.terminate()
        child.wait(timeout=5)
        child.stdout.close()
        raise


def _kill(child):
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def _drive(app, sent):
    """后台线程跑 app：send() 逐块收集（增量），receive 永久阻塞
    （真实网络无消息语义——立即返回会让 listen_for_disconnect 热自旋
    饿死事件循环；取消式 is_disconnected 轮询拿 CancelledError 判存活）。"""

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    asyncio.run(app(dict(_SCOPE), receive, send))


def _body(sent):
    return b"".join(m.get("body", b"") for m in sent
                    if m["type"] == "http.response.body")


def _stats_payloads(body):
    """完整 stats 帧的 data 解析（半帧跳过——轮询窗口内可能未写完）。"""
    for raw in body.split(b"\n\n"):
        if not raw.startswith(b"event: stats\n"):
            continue
        try:
            yield json.loads(raw.split(b"\ndata: ", 1)[1])
        except (json.JSONDecodeError, IndexError):
            continue


def test_sse_first_stats_then_exit():
    """E2E-W1：首帧 stats 含 busy-worker 线程名与 worker 函数名；
    目标 terminate 后收到 exit 帧且流正常结束（WA4）。"""
    child, pid = _spawn_busy()
    try:
        monitor = Monitor(pid)
        monitor.start()
        try:
            sent = []
            thread = threading.Thread(
                target=_drive, args=(create_app(monitor), sent), daemon=True)
            thread.start()

            deadline = time.monotonic() + 8
            got_stats = None
            while time.monotonic() < deadline and got_stats is None:
                for data in _stats_payloads(_body(sent)):
                    assert data["process"]["pid"] == pid
                    if any(t["name"] == "busy-worker"
                           and any(f["name"] == "worker"
                                   for f in t["frames"])
                           for t in data["threads"]):
                        got_stats = data
                        break
                if got_stats is None:
                    time.sleep(0.05)
            assert got_stats is not None, "8s 内未见含 worker 的 stats 帧"

            _kill(child)  # 目标死亡 → Monitor 检测 → exit 帧 → 流结束
            thread.join(8)
            assert not thread.is_alive(), "exit 帧后流未结束（WA4）"

            body = _body(sent)
            assert b"event: exit\ndata: {}\n\n" in body
            # 流以最终空 body 块收尾（StreamingResponse 正常完成）
            assert sent[-1]["type"] == "http.response.body"
            assert not sent[-1].get("more_body", False)
        finally:
            monitor.stop()
    finally:
        if child.poll() is None:
            _kill(child)
        child.stdout.close()


def test_snapshot_endpoint_full_frames_then_409():
    """E2E-W2：/api/snapshot 端到端——200 键集（process/threads）、
    busy-worker 完整帧链、**空闲 MainThread 仍带完整帧链**（stack 语义
    vs stats 空闲剪枝的差异点）；terminate 后转 409（exited 粘性与
    NoThreadState 双路径收敛同一状态码）。"""
    child, pid = _spawn_busy()
    try:
        monitor = Monitor(pid)
        monitor.start()
        try:
            client = TestClient(create_app(monitor))
            r = client.get("/api/snapshot")
            assert r.status_code == 200
            data = r.json()
            assert data["process"]["pid"] == pid
            assert set(data) == {"process", "threads"}
            busy = [t for t in data["threads"] if t["name"] == "busy-worker"]
            assert busy, "快照应含 busy-worker 线程"
            assert any(f["name"] == "worker" for f in busy[0]["frames"])
            # 空闲 MainThread（time.sleep 态）在 stack 语义下保留完整帧链
            main = [t for t in data["threads"] if t["name"] == "MainThread"]
            assert main, "快照应含 MainThread"
            assert main[0]["idle"] is True
            assert main[0]["frames"], "空闲线程帧链不得被剪枝"

            _kill(child)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                r = client.get("/api/snapshot")
                if r.status_code == 409:
                    assert r.json() == {"detail": "target process exited"}
                    return
                time.sleep(0.05)
            pytest.fail("5s 内 /api/snapshot 未转 409")
        finally:
            monitor.stop()
    finally:
        if child.poll() is None:
            _kill(child)
        child.stdout.close()
