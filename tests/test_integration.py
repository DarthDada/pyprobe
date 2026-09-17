"""Integration tests: end-to-end library API against a live child process.

These tests spawn a child Python process (``tests/targets/target_app.py``) as
a descendant of pytest, then exercise ``collect_python`` / ``format_process``
/ ``dump_python`` against it.  Spawning as a descendant is required so that
``process_vm_readv`` works under the default ``ptrace_scope=1``.

Run only the integration tests::

    uv run python -m pytest tests/test_integration.py -v

Skip integration tests::

    uv run python -m pytest tests/ -m "not integration"
"""
import os
import sys

import pytest

from pyprobe import (
    collect_python, format_process, dump_python,
    ProcessInfo, ThreadInfo, FrameInfo,
    PyProbeError, ProcessNotFound,
)
from pyprobe import offsets

pytestmark = pytest.mark.integration


class TestCollectPython:
    """Verify collect_python returns well-formed structured data."""

    def test_returns_process_info(self, target_pid):
        proc_info, threads = collect_python(target_pid)
        assert isinstance(proc_info, ProcessInfo)
        assert proc_info.pid == target_pid
        assert proc_info.python_version != "?"
        assert proc_info.python_version.startswith("3.")
        assert proc_info.exe_path
        assert proc_info.cmdline

    def test_returns_threads_list(self, target_pid):
        _, threads = collect_python(target_pid)
        assert isinstance(threads, list)
        assert len(threads) >= 1
        for t in threads:
            assert isinstance(t, ThreadInfo)

    def test_finds_multiple_threads(self, target_pid):
        """target_app spawns a bg-worker thread → at least 2 threads."""
        _, threads = collect_python(target_pid)
        assert len(threads) >= 2

    def test_finds_bg_worker_name(self, target_pid):
        _, threads = collect_python(target_pid)
        names = {t.name for t in threads}
        assert "bg-worker" in names

    def test_finds_main_thread_name(self, target_pid):
        _, threads = collect_python(target_pid)
        names = {t.name for t in threads}
        assert "MainThread" in names

    def test_threads_sorted_by_tid(self, target_pid):
        _, threads = collect_python(target_pid)
        tids = [t.native_tid for t in threads]
        assert tids == sorted(tids)

    def test_main_thread_has_frames(self, target_pid):
        _, threads = collect_python(target_pid)
        main = next(t for t in threads if t.name == "MainThread")
        assert len(main.frames) >= 1
        for f in main.frames:
            assert isinstance(f, FrameInfo)
            assert f.line >= 1

    def test_bg_worker_has_frames(self, target_pid):
        _, threads = collect_python(target_pid)
        worker = next(t for t in threads if t.name == "bg-worker")
        assert len(worker.frames) >= 1
        top = worker.frames[0]
        assert top.name == "worker"

    def test_frames_reference_target_app(self, target_pid):
        """At least one frame should point at target_app.py."""
        _, threads = collect_python(target_pid)
        all_filenames = []
        for t in threads:
            for f in t.frames:
                if f.filename:
                    all_filenames.append(f.filename)
        assert any("target_app.py" in fn for fn in all_filenames)

    def test_bg_worker_idle(self, target_pid):
        """The bg-worker calls time.sleep → should be detected as idle."""
        _, threads = collect_python(target_pid)
        worker = next(t for t in threads if t.name == "bg-worker")
        assert worker.idle is True

    def test_repeated_collect_is_stable(self, target_pid):
        """Calling collect_python twice yields consistent results."""
        _, threads1 = collect_python(target_pid)
        _, threads2 = collect_python(target_pid)
        assert len(threads1) == len(threads2)
        assert {t.name for t in threads1} == {t.name for t in threads2}


class TestFormatProcess:
    """Verify format_process renders the collected data correctly."""

    def test_header_contains_pid_and_version(self, target_pid):
        proc_info, threads = collect_python(target_pid)
        out = format_process(proc_info, threads)
        assert f"Process {target_pid}" in out
        assert "Python v" in out

    def test_contains_thread_headers(self, target_pid):
        proc_info, threads = collect_python(target_pid)
        out = format_process(proc_info, threads)
        for t in threads:
            assert f"Thread {t.native_tid}" in out

    def test_contains_bg_worker_name(self, target_pid):
        proc_info, threads = collect_python(target_pid)
        out = format_process(proc_info, threads)
        assert '"bg-worker"' in out

    def test_contains_frame_lines(self, target_pid):
        proc_info, threads = collect_python(target_pid)
        out = format_process(proc_info, threads)
        assert "#0" in out

    def test_contains_target_app_filename(self, target_pid):
        proc_info, threads = collect_python(target_pid)
        out = format_process(proc_info, threads)
        assert "target_app.py" in out


class TestDumpPythonCli:
    """Verify the CLI wrapper returns 0 and prints to stdout."""

    def test_dump_python_returns_zero(self, target_pid, capsys):
        rc = dump_python(target_pid)
        captured = capsys.readouterr()
        assert rc == 0
        assert f"Process {target_pid}" in captured.out

    def test_dump_python_includes_version(self, target_pid, capsys):
        dump_python(target_pid)
        captured = capsys.readouterr()
        assert "Python v" in captured.out


class TestErrorPaths:
    """Verify collect_python raises proper exceptions for bad inputs."""

    def test_nonexistent_pid_raises(self):
        with pytest.raises(ProcessNotFound):
            collect_python(0xFFFFFFF)

    def test_nonexistent_pid_is_pyprobe_error(self):
        with pytest.raises(PyProbeError):
            collect_python(0xFFFFFFF)

    def test_dump_python_nonexistent_returns_1(self, capsys):
        rc = dump_python(0xFFFFFFF)
        assert rc == 1


class TestNativeDump:
    """Verify collect_native against the child (may skip if ptrace denied)."""

    def test_collect_native_or_skip(self, target_pid):
        from pyprobe import collect_native, AttachFailed
        try:
            threads = collect_native(target_pid)
        except AttachFailed:
            pytest.skip("ptrace attach not permitted in this environment")
        assert isinstance(threads, list)
        assert len(threads) >= 1
