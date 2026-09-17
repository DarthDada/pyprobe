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

    def fake_dump_python(pid):
        calls["python"].append(pid)
        return 0

    def fake_dump_native(pid):
        calls["native"].append(pid)
        return 0

    monkeypatch.setattr(cli, "dump_python", fake_dump_python)
    monkeypatch.setattr(cli, "dump_native", fake_dump_native)
    return calls


class TestStackDispatch:
    def test_python_stack_default(self, stub_dumps):
        rc = cli.main(["stack", "-p", "1234"])
        assert rc == 0
        assert stub_dumps["python"] == [1234]
        assert stub_dumps["native"] == []

    def test_pid_long_option(self, stub_dumps):
        cli.main(["stack", "--pid", "42"])
        assert stub_dumps["python"] == [42]

    def test_native_stack(self, stub_dumps):
        rc = cli.main(["stack", "-p", "7", "--native"])
        assert rc == 0
        assert stub_dumps["native"] == [7]
        assert stub_dumps["python"] == []

    def test_native_flag_before_pid(self, stub_dumps):
        cli.main(["stack", "--native", "-p", "7"])
        assert stub_dumps["native"] == [7]

    def test_pid_equals_form(self, stub_dumps):
        cli.main(["stack", "--pid=99"])
        assert stub_dumps["python"] == [99]

    def test_short_pid_attached(self, stub_dumps):
        cli.main(["stack", "-p1234"])
        assert stub_dumps["python"] == [1234]


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
