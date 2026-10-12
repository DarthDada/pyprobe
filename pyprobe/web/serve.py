"""Web 编排（WS1–WS5，批 8b）：monitor 生命周期 + uvicorn + 异常→退出码。

对齐 present/dumps.py 的 dump_* 语义位：打印只发生在本层与 cli。
端口占用经本地预检给出友好报错（uvicorn 自身失败是裸 sys.exit(1)，
用户不可读）；Ctrl-C / 服务器关闭后 monitor 必然 stop（finally）。
"""

import errno
import socket
import sys
from pathlib import Path

import uvicorn

from ..errors import PyProbeError
from ..present.color import red, should_color
from .app import create_app
from .monitor import Monitor

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def serve_web(pid: int, *, rate: float = 50, interval: float = 1.0,
              host: str = "127.0.0.1", port: int = 9470) -> int:
    """WS1–WS5 — web 子命令编排入口，返回进程退出码。"""
    err_color = should_color(sys.stderr)

    try:
        # WS1 — 构造即解析目标：失败同步上抛为红色 [!] + rc 1（DP1 承接）。
        monitor = Monitor(pid, rate=rate, interval=interval)
    except PyProbeError as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    # WS2 — version_warning 启动前打 stderr（web 无 JSON 模式，恒打印）。
    if monitor.version_warning:
        print(red(monitor.version_warning, err_color), file=sys.stderr)

    # WS4 — 端口占用友好报错（先于启动行：失败的启动不展示 URL）。
    if _port_in_use(host, port):
        print(red(f"[!] port {port} already in use", err_color),
              file=sys.stderr)
        return 1

    # WS3 — 启动行走 stderr（record/top 摘要惯例）。
    print(f"[i] pyprobe web: monitoring pid {pid} at "
          f"http://{host}:{port}/", file=sys.stderr)

    app = create_app(monitor, static_dir=_STATIC_DIR)
    monitor.start()
    try:
        # WS3 — log_level 压 warning：访问日志噪声不入 stderr 惯例。
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        # WS5 — Ctrl-C 干净退出（uvicorn 通常自行吞掉 SIGINT 正常返回，
        # 这里兜底其向上抛出的形态）。目标退出不影响本路径（A13：服务
        # 器保活显示终态，由 Monitor 的 exit 粘性帧承担）。
        pass
    finally:
        monitor.stop()  # WS5 — 任何退出路径都停采样线程
    return 0


def _port_in_use(host: str, port: int) -> bool:
    """WS4 — EADDRINUSE 预检（仅此 errno 转友好报错；其余异常上抛）。"""
    try:
        sock = socket.create_server((host, port))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            return True
        raise
    sock.close()
    return False
