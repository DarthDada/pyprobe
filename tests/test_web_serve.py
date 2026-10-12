"""Contract tests for web/serve.py（WS1–WS5，批 8b）.

编排层语义钉死：Monitor 构造失败 → 红色 [!] + rc 1；warning 启动前打印；
启动行 + uvicorn log_level 压 warning；端口占用友好报错；Ctrl-C 干净退出。
Monitor 与 uvicorn 均经 monkeypatch 注入（契约注：uvicorn 细节以
注入/monkeypatch 单测）。
"""

import socket

import pytest

import pyprobe.web.serve as serve_mod
from pyprobe.errors import ProcessNotFound

PID = 4242
STARTUP = f"[i] pyprobe web: monitoring pid {PID} at http://127.0.0.1:9470/\n"


class FakeMonitor:
    def __init__(self, *, warning=None):
        self.version_warning = warning
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


@pytest.fixture
def serve_harness(monkeypatch):
    """Monitor 注入 + uvicorn.run 记录桩；返回可控句柄。"""
    state = {"monitor": None, "monitor_error": None, "run_kwargs": None,
             "run_calls": 0, "run_raises": None}

    def fake_monitor(pid, *, rate=50, interval=1.0, **_kw):
        if state["monitor_error"] is not None:
            raise state["monitor_error"]
        state["monitor"].pid = pid
        state["monitor"].ctor_kwargs = {"rate": rate, "interval": interval}
        return state["monitor"]

    def fake_run(app, **kwargs):
        state["run_calls"] += 1
        state["run_kwargs"] = kwargs
        state["run_app"] = app
        state["stopped_at_run"] = state["monitor"].stopped  # 运行中采样
        if state["run_raises"] is not None:
            raise state["run_raises"]

    monkeypatch.setattr(serve_mod, "Monitor", fake_monitor)
    monkeypatch.setattr(serve_mod.uvicorn, "run", fake_run)

    def start(monitor=None, **call_kwargs):
        state["monitor"] = monitor or FakeMonitor()
        kwargs = {"rate": 50, "interval": 1.0, "host": "127.0.0.1",
                  "port": 9470}
        kwargs.update(call_kwargs)
        return serve_mod.serve_web(PID, **kwargs)

    return state, start


class TestConstructionFailure:
    def test_pyprobe_error_red_and_rc1(self, serve_harness, capsys,
                                       monkeypatch):
        """WS1：Monitor 构造失败 → stderr `[!] <err>`（红色，DP1 承接）
        + rc 1；不进 uvicorn、不打印启动行。"""
        state, start = serve_harness
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        state["monitor_error"] = ProcessNotFound(PID)
        rc = start()
        err = capsys.readouterr().err
        assert rc == 1
        assert f"\x1b[31m[!] Process {PID} not found\x1b[0m" in err
        assert "[i]" not in err
        assert state["run_calls"] == 0

    def test_plain_stderr_when_not_tty(self, serve_harness, capsys,
                                       monkeypatch):
        """WS1：非 tty 且无 CLICOLOR_FORCE → 纯文本（DP1 每流判定）。"""
        state, start = serve_harness
        monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
        state["monitor_error"] = ProcessNotFound(PID)
        rc = start()
        err = capsys.readouterr().err
        assert rc == 1
        assert f"[!] Process {PID} not found" in err
        assert "\x1b[" not in err


class TestStartup:
    def test_warning_before_startup_line(self, serve_harness, capsys):
        """WS2/WS3：version_warning 启动前打 stderr（无 JSON 模式恒打印），
        顺序在启动行之前。"""
        _, start = serve_harness
        start(FakeMonitor(warning="[!] unverified version"))
        err = capsys.readouterr().err
        assert "[!] unverified version" in err
        assert err.index("[!] unverified version") < err.index("[i]")

    def test_no_warning_when_none(self, serve_harness, capsys):
        """WS2 边界：warning 为 None → 无 warning 行。"""
        _, start = serve_harness
        start(FakeMonitor())
        err = capsys.readouterr().err
        assert "unverified" not in err

    def test_startup_line_and_uvicorn_kwargs(self, serve_harness, capsys):
        """WS3：启动行走 stderr（record/top 摘要惯例）；uvicorn 收到
        log_level=warning（访问日志噪声不入 stderr）+ host/port；
        monitor 已 start。"""
        state, start = serve_harness
        m = FakeMonitor()
        start(m)
        err = capsys.readouterr().err
        assert STARTUP in err
        assert state["run_calls"] == 1
        assert state["run_kwargs"]["log_level"] == "warning"
        assert state["run_kwargs"]["host"] == "127.0.0.1"
        assert state["run_kwargs"]["port"] == 9470
        assert m.started == 1
        assert state["stopped_at_run"] == 0  # 服务器运行期间不停 monitor
        assert m.stopped == 1  # uvicorn 返回后 finally 停（WS4）

    def test_startup_line_reflects_host_port(self, serve_harness, capsys):
        """WS3：启动行随 --host/--port 变化（用户可见 URL 正确）。"""
        _, start = serve_harness
        start(FakeMonitor(), host="0.0.0.0", port=8080)
        err = capsys.readouterr().err
        assert ("[i] pyprobe web: monitoring pid 4242 at "
                "http://0.0.0.0:8080/\n") in err


class TestPortInUse:
    def test_port_occupied_rc1(self, serve_harness, capsys):
        """WS4：端口占用 → `[!] port <port> already in use` + rc 1（uvicorn
        启动失败的友好前置包装）；不进 uvicorn。"""
        state, start = serve_harness
        blocker = socket.create_server(("127.0.0.1", 0))  # 占住一个真实端口
        try:
            port = blocker.getsockname()[1]
            rc = start(port=port)
        finally:
            blocker.close()
        err = capsys.readouterr().err
        assert rc == 1
        assert f"[!] port {port} already in use" in err
        assert state["run_calls"] == 0

    def test_free_port_proceeds(self, serve_harness, capsys):
        """WS4 边界：端口空闲 → 正常进 uvicorn（预检不误伤）。"""
        state, start = serve_harness
        # 临时占住再释放：拿到一个确定空闲的临时端口
        probe = socket.create_server(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        rc = start(port=port)
        assert rc == 0
        assert state["run_calls"] == 1


class TestShutdown:
    def test_ctrl_c_clean_exit(self, serve_harness, capsys):
        """WS5：Ctrl-C（uvicorn.run 抛 KeyboardInterrupt）→ monitor.stop()
        + rc 0（DP5 干净退出语义承接）。"""
        state, start = serve_harness
        state["run_raises"] = KeyboardInterrupt()
        m = FakeMonitor()
        rc = start(m)
        assert rc == 0
        assert m.started == 1
        assert m.stopped == 1

    def test_normal_run_stops_monitor(self, serve_harness):
        """WS5：uvicorn 正常返回（服务器自行关闭）→ 同样 stop。"""
        state, start = serve_harness
        m = FakeMonitor()
        assert start(m) == 0
        assert m.stopped == 1

    def test_server_outlives_target(self, serve_harness):
        """WS5/A13：目标退出后服务器继续运行——monitor 已 exited 不影响
        uvicorn 被调用与 rc 0（页面保活显示终态）。"""
        state, start = serve_harness
        m = FakeMonitor()
        m.exited = True
        assert start(m) == 0
        assert state["run_calls"] == 1
        assert m.started == 1
