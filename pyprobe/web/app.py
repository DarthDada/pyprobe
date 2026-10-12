"""FastAPI 路由 + SSE 编码 + 静态伺服（WA1–WA5，批 8b）。

monitor 经 create_app 注入，无全局状态（WA5）；SSE 帧编码归本层——
monitor 不知 SSE 存在（批 8a 契约分工）。事件流为 async 生成器：
``is_disconnected`` 轮询 + 短粒度线程池等待，让断连退订确定性发生
（纯同步生成器在线程池中不可取消，退订将依赖 GC，不可接受）。
"""

import asyncio
import json
import queue
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    PlainTextResponse,
    StreamingResponse,
)
from starlette.staticfiles import StaticFiles

PING_TIMEOUT = 15.0  # WA4：15s 无帧 → 心跳
_POLL = 0.5          # 断连轮询粒度（is_disconnected 响应上限）

_MISSING = ("pyprobe web frontend is missing — no static files were "
            "found; build the frontend or reinstall pyprobe")


def _encode(frame: dict) -> str:
    """WA4 — SSE 帧编码（契约逐字 pin）。"""
    if frame.get("type") == "exit":
        return "event: exit\ndata: {}\n\n"
    return f"event: stats\ndata: {json.dumps(frame['data'])}\n\n"


async def _event_stream(request: Request, monitor):
    """WA3/WA4 — 订阅 → 帧转发 → 断连/exit 收尾（finally 恒退订）。"""
    q = monitor.subscribe()  # WA3 — 进入即订阅
    last_emit = time.monotonic()
    try:
        while True:
            # WA3 — 客户端断开即收尾（uvicorn 经 receive 投递 disconnect）。
            if await request.is_disconnected():
                return
            try:
                frame = await asyncio.to_thread(q.get, True, _POLL)
            except queue.Empty:
                # WA4 — 15s 无帧：注释行心跳，代理/浏览器不掐空闲连接。
                if time.monotonic() - last_emit >= PING_TIMEOUT:
                    yield ": ping\n\n"
                    last_emit = time.monotonic()
                continue
            yield _encode(frame)
            last_emit = time.monotonic()
            if frame.get("type") == "exit":  # WA4 — exit 后流正常结束
                return
    finally:
        monitor.unsubscribe(q)  # WA3 — 断开即退订


def create_app(monitor, *, static_dir=None) -> FastAPI:
    """WA5 — 组装路由（monitor 注入，无全局状态）。"""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    static_path = Path(static_dir) if static_dir is not None else None

    @app.get("/", include_in_schema=False)
    def index():
        # WA1 — index.html；构建产物缺席 → 503 纯文本，不 traceback。
        if static_path is not None and (static_path / "index.html").is_file():
            return FileResponse(static_path / "index.html")
        return PlainTextResponse(_MISSING, status_code=503)

    # WA1 — /assets/* 经 StaticFiles 挂载（目录穿越防护由其实现承担）。
    if static_path is not None and (static_path / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_path / "assets"),
                  name="assets")

    @app.get("/api/process")
    def api_process():
        # WA2 — asdict(proc_info) 四键 + version_warning（None → null）。
        return {**asdict(monitor.proc_info),
                "version_warning": monitor.version_warning}

    @app.get("/api/events")
    async def api_events(request: Request):
        # WA3 — SSE 头部：no-cache + 代理直通（X-Accel-Buffering）。
        return StreamingResponse(
            _event_stream(request, monitor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"},
        )

    return app
