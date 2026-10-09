"""Unit tests for --json output of the stack and syscall subcommands.

Guards: JSON is parseable, colors never leak ANSI codes into it, data
fields keep full paths (path shortening is a display-layer-only concern),
None fields become null, and the error path stays plain-text stderr + rc 1.
"""

import json

from pyprobe.errors import ProcessNotFound
from pyprobe.native_dump import dump_native, format_native_json
from pyprobe.stack_dump import dump_python, format_process_json
from pyprobe.syscall_render import format_syscalls_json
from pyprobe.syscall_tracer import dump_syscalls
from pyprobe.types import (
    FrameInfo,
    NativeFrame,
    NativeThreadInfo,
    ProcessInfo,
    SyscallEvent,
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


def _syscall_events():
    return [
        SyscallEvent(tid=100, nr=257, name="openat",
                     args=[0xFFFFFFFFFFFFFF9C, 0x7F00, 0, 0, 0, 0],
                     rendered='AT_FDCWD, "/tmp/x", O_RDONLY|O_CLOEXEC',
                     ret=3, error=None, elapsed=0.000021),
        SyscallEvent(tid=101, nr=257, name="openat",
                     args=[0xFFFFFFFFFFFFFF9C, 0x7F01, 0, 0, 0, 0],
                     rendered='AT_FDCWD, "/nope", O_RDONLY',
                     ret=-1, error=2, elapsed=0.000015),
    ]


class TestFormatSyscallsJson:
    def test_shape(self):
        data = json.loads(format_syscalls_json(_syscall_events()))
        assert set(data.keys()) == {"events"}
        assert len(data["events"]) == 2
        ev0 = data["events"][0]
        assert ev0["tid"] == 100
        assert ev0["nr"] == 257
        assert ev0["name"] == "openat"
        assert ev0["args"][0] == 0xFFFFFFFFFFFFFF9C
        assert ev0["rendered"] == 'AT_FDCWD, "/tmp/x", O_RDONLY|O_CLOEXEC'
        assert ev0["ret"] == 3
        assert ev0["error"] is None
        assert ev0["elapsed"] == 0.000021

    def test_error_event_serializes_errno_and_minus_one(self):
        data = json.loads(format_syscalls_json(_syscall_events()))
        ev1 = data["events"][1]
        assert ev1["error"] == 2
        assert ev1["ret"] == -1

    def test_empty_events(self):
        assert json.loads(format_syscalls_json([])) == {"events": []}

    def test_no_ansi(self):
        out = format_syscalls_json(_syscall_events())
        assert "\x1b" not in out


class TestDumpSyscallsJson:
    """dump_syscalls(json_output=True): single JSON doc, no text stream."""

    def _stub(self, monkeypatch, events):
        class FakeTracer:
            def __init__(self, pid, verbose=False):
                self.events = list(events)

            def attach(self):
                pass

            def run(self, *, trace=None, max_events=None, on_event=None):
                if on_event is not None:
                    for ev in self.events:
                        on_event(ev)
                return self.events

            def detach(self):
                pass

        monkeypatch.setattr("pyprobe.syscall_tracer.SyscallTracer",
                            FakeTracer)

    def test_output_parses_and_streaming_suppressed(self, monkeypatch, capsys):
        self._stub(monkeypatch, _syscall_events())
        rc = dump_syscalls(42, color=True, max_events=2, json_output=True)
        assert rc == 0
        out, _ = capsys.readouterr()
        assert "\x1b" not in out  # JSON never colored, even with color=True
        assert out.lstrip().startswith("{")  # exactly one JSON doc, no lines
        data = json.loads(out)
        assert [e["name"] for e in data["events"]] == ["openat", "openat"]
        assert data["events"][1]["error"] == 2

    def test_error_path_still_text_stderr(self, monkeypatch, capsys):
        class FailingTracer:
            def __init__(self, pid, verbose=False):
                pass

            def attach(self):
                raise ProcessNotFound(999)

        monkeypatch.setattr("pyprobe.syscall_tracer.SyscallTracer",
                            FailingTracer)
        rc = dump_syscalls(999, json_output=True)
        assert rc == 1
        _, err = capsys.readouterr()
        assert "[!]" in err

    def test_json_takes_precedence_over_summary(self, monkeypatch, capsys):
        self._stub(monkeypatch, _syscall_events()[:1])
        rc = dump_syscalls(42, summary=True, json_output=True)
        assert rc == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert data["events"][0]["name"] == "openat"
        assert "calls" not in out  # no strace -c table mixed in

    def test_text_stream_mode_unchanged(self, monkeypatch, capsys):
        self._stub(monkeypatch, _syscall_events()[:1])
        rc = dump_syscalls(42, color=False, max_events=1)
        assert rc == 0
        out, _ = capsys.readouterr()
        assert out.startswith("100  openat(")  # strace-style line, no JSON

    def test_summary_mode_unchanged(self, monkeypatch, capsys):
        self._stub(monkeypatch, _syscall_events())
        rc = dump_syscalls(42, color=False, summary=True)
        assert rc == 0
        out, _ = capsys.readouterr()
        assert out.startswith("syscall")  # strace -c table, no JSON
