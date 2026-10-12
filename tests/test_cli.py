"""Contract tests for cli.py (CLI1–CLI4, argparse 薄壳复刻).

Dispatch targets are monkeypatched at the cli module's import seams;
tests pin argument shapes (复刻约束) and exit-code passthrough.
"""

import sys

import pytest

import pyprobe.cli as cli
from pyprobe.cli import main


@pytest.fixture
def dumps(monkeypatch):
    """Record dump_* invocations; each returns a marker rc."""
    calls = {}

    def make(name):
        def fake(*args, **kwargs):
            calls[name] = (args, kwargs)
            return {"dump_python": 0, "dump_native": 0, "dump_syscalls": 0,
                    "dump_record": 0, "dump_top": 0}[name]
        return fake

    for name in ("dump_python", "dump_native", "dump_syscalls",
                 "dump_record", "dump_top"):
        monkeypatch.setattr(cli, name, make(name))
    return calls


class TestStackDispatch:
    def test_python_stack_default(self, dumps):
        """CLI1：stack -p 默认走 dump_python（非 native、非 verbose、
        非 json、color auto→None）。"""
        assert main(["stack", "-p", "123"]) == 0
        assert dumps["dump_python"] == ((123,), {
            "color": None, "verbose": False, "json_output": False})

    def test_native_stack(self, dumps):
        """CLI1：--native 走 dump_native。"""
        assert main(["stack", "-p", "123", "--native"]) == 0
        assert dumps["dump_native"] == ((123,), {
            "color": None, "verbose": False, "json_output": False})

    def test_pid_long_and_equals_forms(self, dumps):
        """CLI1：--pid 长选项与 -p=/--pid= 等号形态。"""
        main(["stack", "--pid", "42"])
        assert dumps["dump_python"][0] == (42,)
        main(["stack", "--pid=43"])
        assert dumps["dump_python"][0] == (43,)
        main(["stack", "-p44"])
        assert dumps["dump_python"][0] == (44,)

    def test_verbose_and_json(self, dumps):
        """CLI1：-v/--json 透传（位置在 pid 前后均可）。"""
        main(["stack", "--native", "-v", "-p", "7", "--json"])
        assert dumps["dump_native"] == ((7,), {
            "color": None, "verbose": True, "json_output": True})

    def test_color_mapping(self, dumps):
        """CLI1：--color auto/always/never → None/True/False。"""
        main(["stack", "-p", "1", "--color", "always"])
        assert dumps["dump_python"][1]["color"] is True
        main(["stack", "-p", "1", "--color", "never"])
        assert dumps["dump_python"][1]["color"] is False
        main(["stack", "-p", "1", "--color", "auto"])
        assert dumps["dump_python"][1]["color"] is None

    def test_color_invalid_value(self):
        """CLI2：--color 非法值 → SystemExit 2。"""
        with pytest.raises(SystemExit) as ei:
            main(["stack", "-p", "1", "--color", "sometimes"])
        assert ei.value.code == 2


class TestSyscallDispatch:
    def test_basic_dispatch(self, dumps):
        """CLI1：syscall 基本分发（默认 trace=""、max_events=None、
        summary/json 关闭）。"""
        assert main(["syscall", "-p", "55"]) == 0
        assert dumps["dump_syscalls"] == ((55,), {
            "color": None, "verbose": False, "trace": "",
            "max_events": None, "summary": False, "json_output": False})

    def test_trace_filter(self, dumps):
        """CLI1：-e 表达式透传。"""
        main(["syscall", "-p", "55", "-e", "file"])
        assert dumps["dump_syscalls"][1]["trace"] == "file"

    def test_trace_prefix_stripped(self, dumps):
        """CLI1：-e trace=file 的 trace= 前缀剥离（strace 兼容）。"""
        main(["syscall", "-p", "55", "-e", "trace=file"])
        assert dumps["dump_syscalls"][1]["trace"] == "file"

    def test_max_events(self, dumps):
        """CLI1：--max-events 透传 int。"""
        main(["syscall", "-p", "55", "--max-events", "10"])
        assert dumps["dump_syscalls"][1]["max_events"] == 10

    def test_summary(self, dumps):
        """CLI1：--summary 透传。"""
        main(["syscall", "-p", "55", "--summary"])
        assert dumps["dump_syscalls"][1]["summary"] is True

    def test_json(self, dumps):
        """CLI1：--json 透传。"""
        main(["syscall", "-p", "55", "--json"])
        assert dumps["dump_syscalls"][1]["json_output"] is True

    def test_summary_and_json_mutually_exclusive(self):
        """CLI2：--summary 与 --json 互斥（两序皆拒）。"""
        with pytest.raises(SystemExit):
            main(["syscall", "-p", "55", "--summary", "--json"])
        with pytest.raises(SystemExit):
            main(["syscall", "-p", "55", "--json", "--summary"])


class TestRecordDispatch:
    def test_basic_dispatch(self, dumps):
        """CLI1：record 默认值（rate=50、duration=None、output=None）。"""
        assert main(["record", "-p", "9"]) == 0
        assert dumps["dump_record"] == ((9,), {
            "rate": 50, "duration": None, "output": None, "color": None})

    def test_rate_duration_output(self, dumps):
        """CLI1：-r/-d/-o 透传（浮点解析）。"""
        main(["record", "-p", "9", "-r", "100", "-d", "2.5",
              "-o", "out.folded"])
        assert dumps["dump_record"] == ((9,), {
            "rate": 100.0, "duration": 2.5, "output": "out.folded",
            "color": None})

    def test_rate_out_of_range(self):
        """CLI2：rate 越界（0 或 >1000）→ SystemExit 2。"""
        with pytest.raises(SystemExit) as ei:
            main(["record", "-p", "9", "-r", "0"])
        assert ei.value.code == 2
        with pytest.raises(SystemExit):
            main(["record", "-p", "9", "-r", "1001"])

    def test_duration_out_of_range(self):
        """CLI2：duration 越界 → SystemExit 2。"""
        with pytest.raises(SystemExit):
            main(["record", "-p", "9", "-d", "-1"])

    def test_rate_not_a_number(self):
        """CLI2：rate 非数字 → SystemExit 2（消息含原值）。"""
        with pytest.raises(SystemExit):
            main(["record", "-p", "9", "-r", "fast"])


class TestTopDispatch:
    def test_basic_dispatch(self, dumps):
        """CLI1：top 默认值（rate=50、interval=1.0）。"""
        assert main(["top", "-p", "9"]) == 0
        assert dumps["dump_top"] == ((9,), {
            "rate": 50, "interval": 1.0, "color": None})

    def test_interval_and_rate(self, dumps):
        """CLI1：-i/-r 透传。"""
        main(["top", "-p", "9", "-i", "0.5", "-r", "20"])
        assert dumps["dump_top"] == ((9,), {
            "rate": 20.0, "interval": 0.5, "color": None})

    def test_interval_out_of_range(self):
        """CLI2：interval 越界（>60）→ SystemExit 2。"""
        with pytest.raises(SystemExit):
            main(["top", "-p", "9", "-i", "61"])


class TestArgumentErrors:
    def test_missing_subcommand(self):
        """CLI2：缺子命令 → SystemExit 2。"""
        with pytest.raises(SystemExit) as ei:
            main([])
        assert ei.value.code == 2

    def test_unknown_subcommand(self):
        """CLI2：未知子命令 → SystemExit 2。"""
        with pytest.raises(SystemExit):
            main(["frobnicate", "-p", "1"])

    def test_stack_missing_pid(self):
        """CLI2：stack 缺 -p → SystemExit 2。"""
        with pytest.raises(SystemExit):
            main(["stack"])

    def test_pid_not_integer(self):
        """CLI2：pid 非整数 → SystemExit 2。"""
        with pytest.raises(SystemExit):
            main(["stack", "-p", "abc"])


class TestVersion:
    def test_version_prints_and_exits(self, capsys):
        """CLI3：--version 输出 `pyprobe <版本>` 并 SystemExit 0。"""
        with pytest.raises(SystemExit) as ei:
            main(["--version"])
        assert ei.value.code == 0
        assert capsys.readouterr().out.startswith("pyprobe ")


class TestExitCodePassthrough:
    def test_dump_rc_passthrough(self, monkeypatch):
        """CLI4：dump_* 返回码原样成为 main 返回值（如 dump_top 的 2）。"""
        monkeypatch.setattr(cli, "dump_top", lambda *a, **kw: 2)
        assert main(["top", "-p", "1"]) == 2


class _ImportBlocker:
    """meta_path finder：对指定顶层模块名抛 MNFE（模拟 web 依赖缺席）。"""

    def __init__(self, name):
        self.name = name

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] == self.name:
            raise ModuleNotFoundError(
                f"No module named {fullname!r}", name=fullname)
        return None


class TestWebDispatch:
    def _patch_serve(self, monkeypatch, rc=7):
        """替换 serve_web 为记录桩（延迟 import 的属性查找点）。"""
        import pyprobe.web.serve as serve_mod
        recorded = {}

        def fake_serve(pid, *, rate, interval, host, port):
            recorded.update(pid=pid, rate=rate, interval=interval,
                            host=host, port=port)
            return rc

        monkeypatch.setattr(serve_mod, "serve_web", fake_serve)
        return recorded

    def test_defaults_and_passthrough(self, monkeypatch):
        """WC1/WC3：web 默认值（rate=50、interval=1.0、host=127.0.0.1、
        port=9470）+ serve_web 返回码透传。"""
        recorded = self._patch_serve(monkeypatch, rc=7)
        assert main(["web", "-p", "123"]) == 7
        assert recorded == {"pid": 123, "rate": 50.0, "interval": 1.0,
                            "host": "127.0.0.1", "port": 9470}

    def test_explicit_options(self, monkeypatch):
        """WC1：-r/-i/--host/--port 显式形态透传。"""
        recorded = self._patch_serve(monkeypatch)
        assert main(["web", "-p", "5", "-r", "10", "-i", "2.5",
                     "--host", "0.0.0.0", "--port", "8080"]) == 7
        assert recorded == {"pid": 5, "rate": 10.0, "interval": 2.5,
                            "host": "0.0.0.0", "port": 8080}

    def test_pid_required(self):
        """WC1：-p 必填 → 缺失 SystemExit 2。"""
        with pytest.raises(SystemExit) as ei:
            main(["web"])
        assert ei.value.code == 2

    def test_rate_interval_ranges(self):
        """WC1：rate ∈ (0,1000]、interval ∈ (0,60]（_positive_float 同款
        越界拒绝）。"""
        for bad in (["web", "-p", "1", "-r", "0"],
                    ["web", "-p", "1", "-r", "1001"],
                    ["web", "-p", "1", "-i", "0"],
                    ["web", "-p", "1", "-i", "61"]):
            with pytest.raises(SystemExit) as ei:
                main(bad)
            assert ei.value.code == 2

    def test_missing_deps_friendly_rc2(self, monkeypatch, capsys):
        """WC2：web 依赖缺席（fastapi 被屏蔽）→ stderr 安装提示 + rc 2
        （DP5 环境不满足语义），非 pyprobe 缺席才转提示。"""
        monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
        for mod in list(sys.modules):
            if mod.split(".")[0] in ("fastapi", "uvicorn") \
                    or mod.startswith("pyprobe.web"):
                monkeypatch.delitem(sys.modules, mod)
        monkeypatch.setattr(
            sys, "meta_path", [_ImportBlocker("fastapi"), *sys.meta_path])
        assert main(["web", "-p", "123"]) == 2
        err = capsys.readouterr().err
        assert ("[!] the web subcommand requires extra dependencies "
                "— install with: pip install 'pyprobe[web]'") in err

    def test_own_import_error_not_swallowed(self, monkeypatch):
        """WC2 成对：pyprobe 自身 ImportError（serve_web 属性缺席）→
        原样上抛，不转安装提示。"""
        import types

        broken = types.ModuleType("pyprobe.web.serve")  # 无 serve_web 属性
        monkeypatch.setitem(sys.modules, "pyprobe.web.serve", broken)
        with pytest.raises(ImportError):
            main(["web", "-p", "123"])
