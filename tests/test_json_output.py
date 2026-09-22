"""Unit tests for --json output of the stack subcommand.

Guards: JSON is parseable, colors never leak ANSI codes into it, data
fields keep full paths (path shortening is a display-layer-only concern),
None fields become null, and the error path stays plain-text stderr + rc 1.
"""

import json

from pyprobe.errors import ProcessNotFound
from pyprobe.native_dump import dump_native, format_native_json
from pyprobe.stack_dump import dump_python, format_process_json
from pyprobe.types import (
    FrameInfo,
    NativeFrame,
    NativeThreadInfo,
    ProcessInfo,
    ThreadInfo,
)

PROC = ProcessInfo(pid=42, cmdline="python3 app.py",
                   exe_path="/usr/bin/python3.12", python_version="3.12.13")


def _threads():
    return [
        ThreadInfo(native_tid=100, thread_id=1000, name="MainThread",
                   frames=[FrameInfo("run", "/usr/lib/python3.12/x.py", 118)],
                   idle=False),
        ThreadInfo(native_tid=101, thread_id=1001, name="worker",
                   frames=[FrameInfo(None, None, 0)], idle=True),
    ]


class TestFormatProcessJson:
    def test_shape(self):
        out = format_process_json(PROC, _threads())
        data = json.loads(out)
        assert set(data.keys()) == {"process", "threads"}
        assert data["process"]["pid"] == 42
        assert data["process"]["cmdline"] == "python3 app.py"
        assert data["process"]["python_version"] == "3.12.13"
        assert len(data["threads"]) == 2
        t0 = data["threads"][0]
        assert t0["native_tid"] == 100
        assert t0["name"] == "MainThread"
        assert t0["frames"][0] == {
            "name": "run", "filename": "/usr/lib/python3.12/x.py", "line": 118}

    def test_none_fields_become_null(self):
        data = json.loads(format_process_json(PROC, _threads()))
        assert data["threads"][1]["frames"][0]["name"] is None
        assert data["threads"][1]["frames"][0]["filename"] is None

    def test_full_paths_no_shortening(self):
        data = json.loads(format_process_json(PROC, _threads()))
        # full path preserved — verbose is a display-layer concept only
        assert data["threads"][0]["frames"][0]["filename"] == \
            "/usr/lib/python3.12/x.py"

    def test_no_ansi_even_with_color(self):
        # color is a format-layer concern; JSON output must never be colored
        out = format_process_json(PROC, _threads())
        assert "\x1b" not in out


class TestDumpPythonJson:
    def _stub(self, monkeypatch, proc=PROC, threads=None):
        """Patch resolve_process + _collect_threads_from_session (TODO §8.1).

        ``dump_python`` no longer routes through ``collect_python`` — it
        needs the ``ProcessSession.version_warning`` field — so the two
        helpers it actually calls are patched.
        """
        from pyprobe.process import ProcessSession
        threads = threads if threads is not None else _threads()
        session = ProcessSession(
            pid=proc.pid, exe_path=proc.exe_path, runtime_addr=0,
            interp_addr=0, trampoline_addr=0, proc_info=proc, names={})
        monkeypatch.setattr("pyprobe.stack_dump.resolve_process",
                            lambda pid, **kw: session)
        monkeypatch.setattr("pyprobe.stack_dump._collect_threads_from_session",
                            lambda session: threads)

    def test_output_parses(self, monkeypatch, capsys):
        self._stub(monkeypatch)
        rc = dump_python(42, color=True, json_output=True)
        assert rc == 0
        out, _ = capsys.readouterr()
        assert "\x1b" not in out
        data = json.loads(out)
        assert data["process"]["pid"] == 42
        assert len(data["threads"]) == 2

    def test_error_path_still_text_stderr(self, monkeypatch, capsys):
        def boom(pid, **kw):
            raise ProcessNotFound(pid)
        monkeypatch.setattr("pyprobe.stack_dump.resolve_process", boom)
        rc = dump_python(999, json_output=True)
        assert rc == 1
        _, err = capsys.readouterr()
        assert "[!]" in err

    def test_text_mode_unchanged(self, monkeypatch, capsys):
        self._stub(monkeypatch)
        rc = dump_python(42, color=False)
        assert rc == 0
        out, _ = capsys.readouterr()
        assert out.startswith("Process 42:")


class TestFormatNativeJson:
    def _native_threads(self):
        return [
            NativeThreadInfo(tid=100, comm="python3",
                             frames=[NativeFrame(pc=0x4000, symbol="main",
                                                  module="/usr/bin/python3.12")]),
        ]

    def test_shape(self):
        out = format_native_json(42, "python3 app.py", self._native_threads())
        data = json.loads(out)
        assert data["process"] == {"pid": 42, "cmdline": "python3 app.py"}
        t0 = data["threads"][0]
        assert t0["tid"] == 100
        assert t0["comm"] == "python3"
        assert t0["frames"][0]["pc"] == 0x4000
        assert t0["frames"][0]["module"] == "/usr/bin/python3.12"

    def test_unwind_failed_flag(self):
        ts = [NativeThreadInfo(tid=1, comm="iou-sqp-3", frames=[],
                               unwind_failed=True)]
        data = json.loads(format_native_json(1, "x", ts))
        assert data["threads"][0]["unwind_failed"] is True


class TestDumpNativeJson:
    def test_output_parses_and_no_text_header(self, monkeypatch, capsys):
        threads = [NativeThreadInfo(tid=100, comm="python3",
                                    frames=[NativeFrame(0x4000, "main")])]
        monkeypatch.setattr("pyprobe.native_dump.collect_native",
                            lambda pid: threads)
        monkeypatch.setattr("pyprobe.native_dump.read_cmdline",
                            lambda pid: "python3 app.py")
        rc = dump_native(42, color=True, json_output=True)
        assert rc == 0
        out, _ = capsys.readouterr()
        assert "\x1b" not in out
        assert "Process 42: python3 app.py" not in out  # text header suppressed
        data = json.loads(out)
        assert data["threads"][0]["tid"] == 100
