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

    def fake_dump_python(pid, color=None, verbose=False, json_output=False):
        calls["python"].append((pid, color, verbose, json_output))
        return 0

    def fake_dump_native(pid, color=None, verbose=False, json_output=False):
        calls["native"].append((pid, color, verbose, json_output))
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
        assert stub_dumps["python"] == [(1234, None, False, False)]
        assert stub_dumps["native"] == []

    def test_pid_long_option(self, stub_dumps):
        cli.main(["stack", "--pid", "42"])
        assert stub_dumps["python"] == [(42, None, False, False)]

    def test_native_stack(self, stub_dumps):
        rc = cli.main(["stack", "-p", "7", "--native"])
        assert rc == 0
        assert stub_dumps["native"] == [(7, None, False, False)]
        assert stub_dumps["python"] == []

    def test_native_flag_before_pid(self, stub_dumps):
        cli.main(["stack", "--native", "-p", "7"])
        assert stub_dumps["native"] == [(7, None, False, False)]

    def test_pid_equals_form(self, stub_dumps):
        cli.main(["stack", "--pid=99"])
        assert stub_dumps["python"] == [(99, None, False, False)]

    def test_short_pid_attached(self, stub_dumps):
        cli.main(["stack", "-p1234"])
        assert stub_dumps["python"] == [(1234, None, False, False)]


class TestVerboseOption:
    def test_verbose_short_flag(self, stub_dumps):
        rc = cli.main(["stack", "-p", "1", "-v"])
        assert rc == 0
        assert stub_dumps["python"] == [(1, None, True, False)]

    def test_verbose_long_flag(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--verbose"])
        assert stub_dumps["python"] == [(1, None, True, False)]

    def test_verbose_native(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--native", "--verbose"])
        assert stub_dumps["native"] == [(1, None, True, False)]

    def test_default_not_verbose(self, stub_dumps):
        cli.main(["stack", "-p", "1"])
        assert stub_dumps["python"] == [(1, None, False, False)]


class TestColorOption:
    def test_default_is_auto(self, stub_dumps):
        cli.main(["stack", "-p", "1"])
        assert stub_dumps["python"] == [(1, None, False, False)]

    def test_color_always(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--color", "always"])
        assert stub_dumps["python"] == [(1, True, False, False)]

    def test_color_never(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--color=never"])
        assert stub_dumps["python"] == [(1, False, False, False)]

    def test_color_native_always(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--native", "--color=always"])
        assert stub_dumps["native"] == [(1, True, False, False)]

    def test_color_invalid_value(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["stack", "-p", "1", "--color", "bogus"])
        err = capsys.readouterr().err
        assert "color" in err.lower() or "usage" in err.lower()


class TestJsonOption:
    def test_json_flag(self, stub_dumps):
        rc = cli.main(["stack", "-p", "1", "--json"])
        assert rc == 0
        assert stub_dumps["python"] == [(1, None, False, True)]

    def test_json_with_verbose(self, stub_dumps):
        cli.main(["stack", "-p", "1", "--json", "-v"])
        assert stub_dumps["python"] == [(1, None, True, True)]

    def test_json_native(self, stub_dumps):
        rc = cli.main(["stack", "-p", "1", "--native", "--json"])
        assert rc == 0
        assert stub_dumps["native"] == [(1, None, False, True)]
        assert stub_dumps["python"] == []


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


@pytest.fixture
def stub_record(monkeypatch):
    calls = []

    def fake_dump_record(pid, *, rate=50, duration=None, output=None,
                         color=None):
        calls.append({
            "pid": pid, "rate": rate, "duration": duration,
            "output": output, "color": color,
        })
        return 0

    monkeypatch.setattr(cli, "dump_record", fake_dump_record)
    return calls


class TestRecordDispatch:
    def test_basic_dispatch(self, stub_record):
        rc = cli.main(["record", "-p", "1234"])
        assert rc == 0
        assert stub_record == [{
            "pid": 1234, "rate": 50, "duration": None,
            "output": None, "color": None,
        }]

    def test_rate_duration_output(self, stub_record):
        rc = cli.main(["record", "-p", "1", "-r", "100", "-d", "5",
                       "-o", "/tmp/p.folded"])
        assert rc == 0
        assert stub_record == [{
            "pid": 1, "rate": 100, "duration": 5,
            "output": "/tmp/p.folded", "color": None,
        }]

    def test_long_options(self, stub_record):
        cli.main(["record", "-p", "1", "--rate=20", "--duration=0.5",
                  "--output=out.folded"])
        call = stub_record[0]
        assert call["rate"] == 20
        assert call["duration"] == 0.5
        assert call["output"] == "out.folded"

    def test_color_always(self, stub_record):
        cli.main(["record", "-p", "1", "--color", "always"])
        assert stub_record[0]["color"] is True

    def test_missing_pid(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["record"])
        err = capsys.readouterr().err
        assert "pid" in err.lower()

    @pytest.mark.parametrize("bad", ["0", "-5", "1001", "abc"])
    def test_rate_out_of_range(self, capsys, bad):
        with pytest.raises(SystemExit):
            cli.main(["record", "-p", "1", "-r", bad])
        err = capsys.readouterr().err
        assert "rate" in err.lower() or "invalid" in err.lower()

    @pytest.mark.parametrize("bad", ["0", "-1", "abc"])
    def test_duration_out_of_range(self, capsys, bad):
        with pytest.raises(SystemExit):
            cli.main(["record", "-p", "1", "-d", bad])
        err = capsys.readouterr().err
        assert "duration" in err.lower() or "invalid" in err.lower()


@pytest.fixture
def stub_top(monkeypatch):
    calls = []

    def fake_dump_top(pid, *, rate=50, interval=1.0, color=None):
        calls.append({
            "pid": pid, "rate": rate, "interval": interval, "color": color,
        })
        return 0

    monkeypatch.setattr(cli, "dump_top", fake_dump_top)
    return calls


class TestTopDispatch:
    def test_basic_dispatch(self, stub_top):
        rc = cli.main(["top", "-p", "1234"])
        assert rc == 0
        assert stub_top == [{
            "pid": 1234, "rate": 50, "interval": 1.0, "color": None,
        }]

    def test_interval_and_rate(self, stub_top):
        rc = cli.main(["top", "-p", "1", "-i", "0.5", "-r", "20"])
        assert rc == 0
        assert stub_top == [{
            "pid": 1, "rate": 20, "interval": 0.5, "color": None,
        }]

    def test_long_options(self, stub_top):
        cli.main(["top", "-p", "1", "--interval=2", "--rate=100"])
        call = stub_top[0]
        assert call["interval"] == 2
        assert call["rate"] == 100

    def test_color_never(self, stub_top):
        cli.main(["top", "-p", "1", "--color=never"])
        assert stub_top[0]["color"] is False

    def test_missing_pid(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["top"])
        err = capsys.readouterr().err
        assert "pid" in err.lower()

    @pytest.mark.parametrize("bad", ["0", "-1", "1001"])
    def test_rate_out_of_range(self, capsys, bad):
        with pytest.raises(SystemExit):
            cli.main(["top", "-p", "1", "-r", bad])

    @pytest.mark.parametrize("bad", ["0", "-0.1", "abc"])
    def test_interval_out_of_range(self, capsys, bad):
        with pytest.raises(SystemExit):
            cli.main(["top", "-p", "1", "-i", bad])
