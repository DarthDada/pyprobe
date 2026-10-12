"""Contract tests for web/app.py（WA1–WA5，批 8b）.

Stub monitor + TestClient，无真实目标进程（WA5）。starlette 1.6 的
TestClient HTTP 传输**全量缓冲**（portal.call 阻塞至 app 完成）——
/api/events 的内容断言按「流完整结束后读全部帧」进行（exit 帧收尾）；
增量语义（断连退订、首帧送达）走原生 ASGI 驱动（receive 投递
http.disconnect），恰是 uvicorn 生产机制的确定化复刻。
"""

import asyncio
import json
import queue
import threading
import time

from fastapi.testclient import TestClient

import pyprobe.web.app as app_mod
from pyprobe import dto
from pyprobe.web.app import create_app

PID = 4242


class StubMonitor:
    """路由依赖的最小 monitor 面：proc_info/version_warning/订阅扇出。"""

    def __init__(self, *, version_warning=None, frames=()):
        self.proc_info = dto.ProcessInfo(
            pid=PID, cmdline="python3 x — café", exe_path="/x/python",
            python_version="3.12.13")
        self.version_warning = version_warning
        self._frames = list(frames)
        self.queues = []
        self.unsubscribed = []

    def subscribe(self):
        q: queue.Queue = queue.Queue(maxsize=64)
        for f in self._frames:
            q.put_nowait(f)
        self.queues.append(q)
        return q

    def unsubscribe(self, q):
        self.unsubscribed.append(q)


def _client(monkeypatch, stub, *, static_dir=None, ping=None, poll=None):
    if ping is not None:
        monkeypatch.setattr(app_mod, "PING_TIMEOUT", ping)
    if poll is not None:
        monkeypatch.setattr(app_mod, "_POLL", poll)
    return TestClient(create_app(stub, static_dir=static_dir))


def _sse_frames(text):
    """完整响应文本 → SSE 帧列表（含 \\n\\n 终结符）。"""
    return [f + "\n\n" for f in text.split("\n\n") if f]


class TestIndexAndAssets:
    def test_index_and_assets_served(self, monkeypatch, tmp_path):
        """WA1：GET / → static/index.html；GET /assets/* 经 StaticFiles
        挂载（static_dir 注入点）。"""
        static = tmp_path / "static"
        (static / "assets").mkdir(parents=True)
        (static / "index.html").write_text("<html>pyprobe</html>")
        (static / "assets" / "app.js").write_text("console.log('x')")
        client = _client(monkeypatch, StubMonitor(), static_dir=static)
        r = client.get("/")
        assert r.status_code == 200
        assert "pyprobe" in r.text
        r = client.get("/assets/app.js")
        assert r.status_code == 200
        assert r.text == "console.log('x')"

    def test_index_missing_static_503(self, monkeypatch):
        """WA1：static_dir 缺席 → 503 纯文本提示构建产物缺席（非
        traceback）；/assets 未挂载 → 404。"""
        client = _client(monkeypatch, StubMonitor())
        r = client.get("/")
        assert r.status_code == 503
        assert r.headers["content-type"].startswith("text/plain")
        assert "missing" in r.text
        assert "Traceback" not in r.text and "<html" not in r.text
        assert client.get("/assets/app.js").status_code == 404

    def test_static_dir_without_index_503(self, monkeypatch, tmp_path):
        """WA1 边界：static_dir 存在但无 index.html → 同样 503。"""
        empty = tmp_path / "s"
        empty.mkdir()
        client = _client(monkeypatch, StubMonitor(), static_dir=empty)
        assert client.get("/").status_code == 503


class TestApiProcess:
    def test_key_set_and_null_warning(self, monkeypatch):
        """WA2：asdict(proc_info) 四键 + version_warning 键（None →
        null）；键集 pin；非 ASCII 值不 pin 转义形态（解析后等值即可）。"""
        client = _client(monkeypatch, StubMonitor())
        r = client.get("/api/process")
        assert r.status_code == 200
        assert r.json() == {
            "pid": PID, "cmdline": "python3 x — café",
            "exe_path": "/x/python", "python_version": "3.12.13",
            "version_warning": None}

    def test_warning_value(self, monkeypatch):
        """WA2：version_warning 非 None → 原样透出。"""
        client = _client(
            monkeypatch, StubMonitor(version_warning="[!] stub warn"))
        assert client.get("/api/process").json()["version_warning"] == (
            "[!] stub warn")

    def test_monitor_injected_no_global_state(self, monkeypatch):
        """WA5：monitor 经 create_app 注入——两个 app 各自回报自己的
        monitor，无全局状态串扰。"""
        c1 = _client(monkeypatch, StubMonitor())
        stub2 = StubMonitor()
        stub2.proc_info = dto.ProcessInfo(
            pid=9999, cmdline="other", exe_path="/y/python",
            python_version="3.13.0")
        c2 = _client(monkeypatch, stub2)
        assert c1.get("/api/process").json()["pid"] == PID
        assert c2.get("/api/process").json()["pid"] == 9999


class TestEvents:
    def test_headers_encoding_and_exit_end(self, monkeypatch):
        """WA3/WA4：media_type 与两响应头；SSE 帧编码逐字 pin（stats 单行
        json / exit 字面 {}）；exit 帧后流正常结束（响应完整返回）；
        结束即退订。TestClient 全量缓冲——内容按完整流断言。"""
        stub = StubMonitor(frames=(
            {"type": "stats", "data": {"samples": 1, "threads": []}},
            {"type": "exit", "data": {}},
        ))
        client = _client(monkeypatch, stub)
        r = client.get("/api/events")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["x-accel-buffering"] == "no"
        assert _sse_frames(r.text) == [
            'event: stats\ndata: ' + json.dumps(
                {"samples": 1, "threads": []}) + '\n\n',
            "event: exit\ndata: {}\n\n",
        ]
        assert stub.unsubscribed == [stub.queues[0]]  # WA3：结束即退订

    def test_ping_heartbeat(self, monkeypatch):
        """WA4：静默 15s（缩至 0.2s）输出 `: ping\\n\\n` 心跳——注释行
        语义，不干扰事件帧；exit 帧经 feeder 线程投递收尾。"""
        stub = StubMonitor()

        def feeder():
            while not stub.queues:  # 等 subscribe 发生
                time.sleep(0.01)
            time.sleep(0.5)  # ~两个 ping 周期后收尾
            stub.queues[0].put({"type": "exit", "data": {}})

        threading.Thread(target=feeder, daemon=True).start()
        client = _client(monkeypatch, stub, ping=0.2, poll=0.05)
        r = client.get("/api/events")
        frames = _sse_frames(r.text)
        assert frames.count(": ping\n\n") >= 2
        assert frames[-1] == "event: exit\ndata: {}\n\n"

    def test_disconnect_unsubscribes_and_ends(self, monkeypatch):
        """WA3：客户端断开（http.disconnect）→ 首帧已送达、生成器结束
        且退订——原生 ASGI 驱动确定化复刻 uvicorn 的断连语义（首帧
        立即到达的增量观测亦在此钉）。"""
        stub = StubMonitor(frames=({"type": "stats", "data": {"n": 1}},))
        app = create_app(stub)
        sent = []
        first_body = asyncio.Event()  # 首个 body 块送达后才放行断连

        async def receive():
            # 阻塞至首块送达（真实网络语义）——receive 立即返回会让
            # listen_for_disconnect 热自旋饿死事件循环。取消式轮询
            # （is_disconnected）在 checkpoint 处拿 CancelledError →
            # 判定存活，恰为 uvicorn 空闲连接的行为。
            await first_body.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if (message["type"] == "http.response.body"
                    and message.get("body")):
                first_body.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/api/events", "raw_path": b"/api/events",
            "query_string": b"", "root_path": "", "headers": [],
            "client": ("testclient", 50000), "server": ("testserver", 80),
        }
        asyncio.run(app(scope, receive, send))  # 正常返回即生成器已结束

        body = b"".join(m.get("body", b"") for m in sent
                        if m["type"] == "http.response.body")
        assert b"event: stats" in body        # 断开前首帧已送达
        assert stub.unsubscribed == [stub.queues[0]]  # WA3：断开即退订


class TestUncoveredGuards:
    def test_no_real_process_needed(self, monkeypatch):
        """WA5 佐证：全部路由经 stub monitor 可测（无真实目标进程）。"""
        stub = StubMonitor()
        client = _client(monkeypatch, stub)
        assert client.get("/api/process").status_code == 200
        assert client.get("/").status_code == 503

