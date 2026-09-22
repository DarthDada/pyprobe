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
    FrameInfo,
    ProcessInfo,
    ProcessNotFound,
    PyProbeError,
    ThreadInfo,
    collect_python,
    dump_python,
    format_process,
)

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
        from pyprobe import AttachFailed, collect_native
        try:
            threads = collect_native(target_pid)
        except AttachFailed:
            pytest.skip("ptrace attach not permitted in this environment")
        assert isinstance(threads, list)
        assert len(threads) >= 1


class TestSyscall:
    """Verify syscall tracing against the child (skip if ptrace denied)."""

    def _collect(self, target_pid, **kw):
        from pyprobe import AttachFailed, collect_syscalls
        try:
            return collect_syscalls(target_pid, **kw)
        except AttachFailed:
            pytest.skip("ptrace attach not permitted in this environment")

    def test_collect_returns_events(self, target_pid):
        events = self._collect(target_pid, max_events=6)
        assert isinstance(events, list)
        assert len(events) == 6

    def test_clock_nanosleep_captured(self, target_pid):
        events = self._collect(target_pid, max_events=6)
        names = {e.name for e in events}
        assert "clock_nanosleep" in names  # target_app is a sleep loop

    def test_multiple_tids(self, target_pid):
        events = self._collect(target_pid, max_events=6)
        tids = {e.tid for e in events}
        assert len(tids) >= 2  # main thread + bg-worker

    def test_elapsed_recorded(self, target_pid):
        events = self._collect(target_pid, max_events=6)
        assert all(e.elapsed >= 0.0 for e in events)
        assert any(e.elapsed > 0.1 for e in events)  # sleeps >= 1s

    def test_event_format_renders(self, target_pid):
        events = self._collect(target_pid, max_events=4)
        for ev in events:
            line = ev.format()
            assert str(ev.tid) in line
            assert ev.name in line
            assert "= " in line

    def test_target_survives_tracing(self, target_pid, capsys):
        self._collect(target_pid, max_events=4)
        # tracing must not kill or hang the target
        from pyprobe import collect_python
        proc_info, threads = collect_python(target_pid)
        assert proc_info.pid == target_pid

    def test_dump_output(self, target_pid, capsys):
        from pyprobe import AttachFailed, dump_syscalls
        try:
            rc = dump_syscalls(target_pid, color=False, max_events=4)
        except AttachFailed:
            pytest.skip("ptrace attach not permitted in this environment")
        assert rc == 0
        out = capsys.readouterr().out
        assert "clock_nanosleep" in out

    def test_summary_table(self, target_pid, capsys):
        from pyprobe import AttachFailed, dump_syscalls
        try:
            rc = dump_syscalls(target_pid, color=False, max_events=4,
                               summary=True)
        except AttachFailed:
            pytest.skip("ptrace attach not permitted in this environment")
        assert rc == 0
        out = capsys.readouterr().out
        assert "syscall" in out
        assert "calls" in out
        assert "total" in out

    def test_nonexistent_pid_raises(self):
        from pyprobe import PyProbeError, collect_syscalls
        with pytest.raises(PyProbeError):
            collect_syscalls(999999, max_events=1)


class TestSampler:
    """Sampling engine against the CPU-bound spin target."""

    def test_sample_returns_threads(self, spin_pid):
        from pyprobe import Sampler
        threads = Sampler(spin_pid).sample()
        assert len(threads) >= 2  # MainThread + spin-worker
        assert all(isinstance(t, ThreadInfo) for t in threads)
        names = {t.name for t in threads}
        assert "spin-worker" in names

    def test_consecutive_samples_independent(self, spin_pid):
        from pyprobe import Sampler
        s = Sampler(spin_pid)
        first = s.sample()
        second = s.sample()  # fresh reader per sample
        assert {t.native_tid for t in first} == {t.native_tid for t in second}

    def test_spin_worker_active_with_burn_frame(self, spin_pid):
        from pyprobe import Sampler
        threads = Sampler(spin_pid).sample()
        spin = [t for t in threads if t.name == "spin-worker"]
        assert spin and not spin[0].idle
        assert any(f.name == "burn" for f in spin[0].frames)

    def test_process_exited(self):
        import subprocess
        import time as _time

        from tests.conftest import _can_read_descendant
        if not _can_read_descendant():
            pytest.skip("integration tests require Linux process_vm_readv")
        from pyprobe import ProcessExited, Sampler
        script = os.path.join(os.path.dirname(__file__), "targets",
                              "spin_app.py")
        child = subprocess.Popen([sys.executable, script],
                                 stdout=subprocess.PIPE, text=True)
        try:
            line = child.stdout.readline()
            pid = int(line.split(":")[-1].strip())
            _time.sleep(0.3)
            s = Sampler(pid)
            s.sample()  # alive → works
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()
        with pytest.raises(ProcessExited):
            s.sample()  # memory gone after exit


class TestRecord:
    """record (folded profiling) against the spin target."""

    def test_collect_profile_counts(self, spin_pid):
        from pyprobe import collect_profile
        p = collect_profile(spin_pid, rate=20, duration=1.0)
        assert p.samples > 0
        assert any(key.startswith('"spin-worker";') for key in p.counts)
        assert p.elapsed >= 1.0

    def test_folded_output_format(self, spin_pid):
        from pyprobe import collect_profile, format_folded
        p = collect_profile(spin_pid, rate=20, duration=1.0)
        text = format_folded(p)
        assert text
        for line in text.strip().splitlines():
            # "<thread>";<root>;...;<leaf> <count>
            assert " " in line
            key, count = line.rsplit(" ", 1)
            assert count.isdigit()
            assert ";" in key

    def test_dump_record_to_file(self, spin_pid, tmp_path):
        from pyprobe import dump_record
        path = tmp_path / "profile.folded"
        rc = dump_record(spin_pid, rate=20, duration=0.5,
                         output=str(path), color=False)
        assert rc == 0
        content = path.read_text()
        assert '"spin-worker";' in content

    def test_dump_record_stdout_streams(self, spin_pid, capsys):
        from pyprobe import dump_record
        rc = dump_record(spin_pid, rate=20, duration=0.5, color=False)
        assert rc == 0
        out, err = capsys.readouterr()
        assert out  # folded on stdout
        assert "[i]" in err  # summary on stderr

    def test_idle_threads_excluded_from_sleep_target(self, target_pid):
        """target_app's threads are sleep loops → mostly/fully idle."""
        from pyprobe import collect_profile
        p = collect_profile(target_pid, rate=20, duration=0.5)
        assert p.idle_samples > 0  # both threads sleep → idle dominates


class TestTopStatsLive:
    def test_render_contains_spin_worker(self, spin_pid):
        from pyprobe import Sampler, TopStats
        s = Sampler(spin_pid)
        stats = TopStats()
        for _ in range(5):
            stats.update(s.sample())
        out = stats.render(s.proc_info, elapsed=0.25, color=False)
        assert "spin-worker" in out
        assert "burn" in out
        assert "Top functions" in out


class TestJsonOutputLive:
    def test_dump_python_json(self, spin_pid, capsys):
        import json

        from pyprobe import dump_python
        rc = dump_python(spin_pid, color=False, json_output=True)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["process"]["pid"] == spin_pid
        assert len(data["threads"]) >= 2

    def test_json_cross_check_with_collect(self, spin_pid, capsys):
        import json

        from pyprobe import dump_python
        rc = dump_python(spin_pid, color=False, json_output=True)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        _, threads = collect_python(spin_pid)
        # same thread set, same frame counts
        assert [t["native_tid"] for t in data["threads"]] == \
            [t.native_tid for t in threads]
