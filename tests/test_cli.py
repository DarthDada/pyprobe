"""Unit tests for the pyprobe CLI argument parsing and dispatch.

These tests exercise ``pyprobe.cli.main`` without spawning a real target
process: ``dump_python`` / ``dump_native`` are monkeypatched to record the
PID they receive and return a sentinel exit code.
"""

import pytest

from pyprobe import cli


@pytest.fixture
def stub_dumps(monkeypatch):
    calls = {"python": [], "native": []}

    def fake_dump_python(pid, color=None, verbose=False):
        calls["python"].append((pid, color, verbose))
        return 0

    def fake_dump_native(pid, color=None, verbose=False):
        calls["native"].append((pid, color, verbose))
        return 0

    monkeypatch.setattr(cli, "dump_python", fake_dump_python)
    monkeypatch.setattr(cli, "dump_native", fake_dump_native)
    return calls


@pytest.fixture
def stub_syscall(monkeypatch):
    calls = []

    def fake_dump_syscalls(pid, color=None, verbose=False, trace="",
                           max_events=None, summary=False):
        calls.append({
            "pid": pid, "color": color, "verbose": verbose,
            "trace": trace, "max_events": max_events,
            "summary": summary,
        })
        return 0

    monkeypatch.setattr(cli, "dump_syscalls", fake_dump_syscalls)
    return calls


class TestStackDispatch:
    def test_python_stack_default(self, stub_dumps):
        rc = cli.main(["stack", "-p", "1234"])
        assert rc == 0
        assert stub_dumps["python"] == [(1234, None, False)]
        assert stub_dumps["native"] == []

    def test_pid_long_option(self, stub_dumps):
        cli.main(["stack", "--pid", "42"])
        assert stub_dumps["python"] == [(42, None, False)]

    def test_native_stack(self, stub_dumps):
        rc = cli.main(["stack", "-p", "7", "--native"])
        assert rc == 0
        assert stub_dumps["native"] == [(7, None, False)]
        assert stub_dumps["python"] == []

    def test_native_flag_before_pid(self, stub_dumps):
        cli.main(["stack", "--native", "-p", "7"])
        assert stub_dumps["native"] == [(7, None, False)]

    def test_pid_equals_form(self, stub_dumps):
        cli.main(["stack", "--pid=99"])
        assert stub_dumps["python"] == [(99, None, False)]

    def test_short_pid_attached(self, stub_dumps):
        cli.main(["stack", "-p1234"])
        assert stub_dumps["python"] == [(1234, None, False)]


class TestVerboseOption:
    def test_verbose_short_flag(self, stub_dumps):
        rc = cli.main(["stack", "-p", "1", "-v"])
        assert rc == 0
        assert stub_dumps["python"] == [(1, None, True)]

    def test_verbose_long_flag(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--verbose"])
        assert stub_dumps["python"] == [(1, None, True)]

    def test_verbose_native(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--native", "--verbose"])
        assert stub_dumps["native"] == [(1, None, True)]

    def test_default_not_verbose(self, stub_dumps):
        cli.main(["stack", "-p", "1"])
        assert stub_dumps["python"] == [(1, None, False)]


class TestColorOption:
    def test_default_is_auto(self, stub_dumps):
        cli.main(["stack", "-p", "1"])
        assert stub_dumps["python"] == [(1, None, False)]

    def test_color_always(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--color", "always"])
        assert stub_dumps["python"] == [(1, True, False)]

    def test_color_never(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--color=never"])
        assert stub_dumps["python"] == [(1, False, False)]

    def test_color_native_always(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--native", "--color=always"])
        assert stub_dumps["native"] == [(1, True, False)]

    def test_color_invalid_value(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["stack", "-p", "1", "--color", "bogus"])
        err = capsys.readouterr().err
        assert "color" in err.lower() or "usage" in err.lower()


class TestArgumentErrors:
    def test_missing_subcommand(self, capsys):
        with pytest.raises(SystemExit):
            cli.main([])
        err = capsys.readouterr().err
        assert "command" in err.lower() or "usage" in err.lower()

    def test_unknown_subcommand(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["bogus", "-p", "1"])
        err = capsys.readouterr().err
        assert "invalid" in err.lower() or "usage" in err.lower()

    def test_stack_missing_pid(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["stack"])
        err = capsys.readouterr().err
        assert "pid" in err.lower()

    def test_pid_not_integer(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["stack", "-p", "abc"])
        err = capsys.readouterr().err
        assert "invalid" in err.lower() or "pid" in err.lower()


class TestVersion:
    def test_version_prints_and_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "pyprobe" in out


class TestSyscallDispatch:
    def test_basic_dispatch(self, stub_syscall):
        rc = cli.main(["syscall", "-p", "1234"])
        assert rc == 0
        assert len(stub_syscall) == 1
        call = stub_syscall[0]
        assert call["pid"] == 1234
        assert call["color"] is None
        assert call["verbose"] is False
        assert call["trace"] == ""
        assert call["max_events"] is None
        assert call["summary"] is False

    def test_trace_filter(self, stub_syscall):
        cli.main(["syscall", "-p", "1", "-e", "trace=file"])
        assert stub_syscall[0]["trace"] == "file"

    def test_trace_filter_short_form(self, stub_syscall):
        cli.main(["syscall", "-p", "1", "-e", "network"])
        assert stub_syscall[0]["trace"] == "network"

    def test_trace_filter_names(self, stub_syscall):
        cli.main(["syscall", "-p", "1", "--trace", "read,write"])
        assert stub_syscall[0]["trace"] == "read,write"

    def test_max_events(self, stub_syscall):
        cli.main(["syscall", "-p", "1", "--max-events", "30"])
        assert stub_syscall[0]["max_events"] == 30

    def test_summary(self, stub_syscall):
        cli.main(["syscall", "-p", "1", "--summary"])
        assert stub_syscall[0]["summary"] is True

    def test_verbose(self, stub_syscall):
        cli.main(["syscall", "-p", "1", "-v"])
        assert stub_syscall[0]["verbose"] is True

    def test_color_always(self, stub_syscall):
        cli.main(["syscall", "-p", "1", "--color", "always"])
        assert stub_syscall[0]["color"] is True

    def test_summary_with_max_events(self, stub_syscall):
        cli.main(["syscall", "-p", "1", "--summary", "--max-events", "200"])
        call = stub_syscall[0]
        assert call["summary"] is True
        assert call["max_events"] == 200

    def test_missing_pid(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["syscall"])
        err = capsys.readouterr().err
        assert "pid" in err.lower()

    def test_pid_not_integer(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["syscall", "-p", "abc"])
        err = capsys.readouterr().err
        assert "invalid" in err.lower() or "pid" in err.lower()
