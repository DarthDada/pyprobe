"""Unit tests for pyprobe.record — folded-stack profiling (library layer).

``collect_profile`` is tested with a stubbed ``Sampler``; scheduling is
tested with a fake clock (absolute-time guard against drift); formatting
is tested as a pure function.
"""

import time

import pytest

from pyprobe.record import collect_profile, format_folded, _fold_key
from pyprobe.types import FrameInfo, ThreadInfo, ProcessInfo, ProfileData
from pyprobe.errors import ProcessExited, ProcessNotFound
from pyprobe import record


def _thread(tid, name, frames, idle=False):
    return ThreadInfo(native_tid=tid, name=name,
                      frames=[FrameInfo(n, f, ln) for n, f, ln in frames],
                      idle=idle)


def _profile(counts, samples=1, idle=0, elapsed=1.0):
    return ProfileData(
        proc_info=ProcessInfo(pid=1, cmdline="py app.py",
                             exe_path="/usr/bin/python3.12",
                             python_version="3.12.13"),
        counts=counts, samples=samples, idle_samples=idle, elapsed=elapsed)


class TestFoldKey:
    def test_root_first(self):
        # frames are leaf→root; folded lines are root→leaf
        t = _thread(1, "w", [("leaf", "a.py", 1), ("root", "a.py", 9)])
        assert _fold_key(t) == '"w";root;leaf'

    def test_thread_name_prefix(self):
        t = _thread(42, "spin-worker", [("burn", "s.py", 8)])
        assert _fold_key(t) == '"spin-worker";burn'

    def test_tid_prefix_when_unnamed(self):
        t = _thread(42, "", [("burn", "s.py", 8)])
        assert _fold_key(t) == "tid-42;burn"

    def test_none_frame_name_placeholder(self):
        t = _thread(1, "w", [(None, "a.py", 1)])
        assert _fold_key(t) == '"w";?'


class TestFormatFolded:
    def test_sorted_count_desc_then_lexicographic(self):
        p = _profile({'"w";b': 5, '"w";a': 5, '"w";c': 9}, samples=19)
        assert format_folded(p) == '"w";c 9\n"w";a 5\n"w";b 5\n'

    def test_empty(self):
        p = _profile({}, samples=0, idle=3)
        assert format_folded(p) == ""

    def test_single_frame_function_name_only(self):
        t = ThreadInfo(native_tid=1, name="m",
                       frames=[FrameInfo("run", "/x/y/ runners.py", 118)])
        key = _fold_key(t)
        assert "runners.py" not in key
        assert key == '"m";run'


class FakeClock:
    """time.monotonic/sleep replacement: time passes only inside sleep()."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, d):
        assert d > 0, "sleep() must never be called with a non-positive delay"
        self.sleeps.append(d)
        self.now += d


class StubSampler:
    """Preset-driven Sampler double; ``samples`` is consumed call by call."""

    last_pid = None

    def __init__(self, pid):
        StubSampler.last_pid = pid
        self._queue = list(type(self).preset)
        self.proc_info = ProcessInfo(
            pid=pid, cmdline="py app.py", exe_path="/usr/bin/python3.12",
            python_version="3.12.13")

    def sample(self):
        item = self._queue.pop(0) if self._queue else None
        # KeyboardInterrupt derives from BaseException, not Exception
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def stub_sampler(monkeypatch):
    def install(preset):
        StubSampler.preset = preset
        monkeypatch.setattr(record, "Sampler", StubSampler)
        return StubSampler
    return install


class TestCollectProfile:
    def test_counts_and_idle_exclusion(self, stub_sampler, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        monkeypatch.setattr(time, "sleep", clock.sleep)
        active1 = _thread(1, "w", [("burn", "s.py", 8)])
        active2 = _thread(2, "m", [("main", "a.py", 20)])
        idle_t = _thread(3, "sleeper", [], idle=True)
        # 2 samples then the clock runs out of duration (interval 0.1s)
        stub_sampler([[active1, active2, idle_t], [active1, idle_t],
                      KeyboardInterrupt()])
        p = collect_profile(42, rate=10, duration=0.25)
        assert p.samples == 3
        assert p.idle_samples == 2
        assert p.counts == {'"w";burn': 2, '"m";main': 1}
        assert p.proc_info.pid == 42

    def test_keyboardinterrupt_returns_partial(self, stub_sampler, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        monkeypatch.setattr(time, "sleep", clock.sleep)
        active = _thread(1, "w", [("burn", "s.py", 8)])
        stub_sampler([[active], KeyboardInterrupt()])
        p = collect_profile(1, rate=100, duration=10)
        assert p.samples == 1
        assert p.counts == {'"w";burn': 1}

    def test_process_exited_returns_partial(self, stub_sampler, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        monkeypatch.setattr(time, "sleep", clock.sleep)
        active = _thread(1, "w", [("burn", "s.py", 8)])
        stub_sampler([[active], ProcessExited(1)])
        p = collect_profile(1, rate=100, duration=10)
        assert p.samples == 1

    def test_absolute_scheduling_no_drift(self, stub_sampler, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        monkeypatch.setattr(time, "sleep", clock.sleep)
        active = _thread(1, "w", [("burn", "s.py", 8)])

        class SlowSampler(StubSampler):
            def sample(self):
                clock.now += 0.005  # each sample takes 5 ms of wall time
                return [active]

        monkeypatch.setattr(record, "Sampler", SlowSampler)
        SlowSampler.preset = []
        p = collect_profile(1, rate=50, duration=0.1)
        # interval 0.02 - work 0.005 = 0.015 sleep per iteration
        assert all(d == pytest.approx(0.015) for d in clock.sleeps)
        assert p.samples >= 5

    def test_behind_schedule_resets_baseline(self, stub_sampler, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        monkeypatch.setattr(time, "sleep", clock.sleep)
        active = _thread(1, "w", [("burn", "s.py", 8)])

        class SlowSampler(StubSampler):
            def sample(self):
                clock.now += 0.03  # slower than the 0.02 interval
                return [active]

        monkeypatch.setattr(record, "Sampler", SlowSampler)
        SlowSampler.preset = []
        p = collect_profile(1, rate=50, duration=0.12)
        # never tried to catch up: no sleeps at all (work >= interval)
        assert clock.sleeps == []
        assert p.samples >= 4


class TestDumpRecord:
    def _install(self, monkeypatch):
        p = _profile({'"w";burn': 9, '"m";main': 4}, samples=13, idle=2)
        monkeypatch.setattr(record, "collect_profile",
                            lambda pid, rate, duration: p)
        return p

    def test_stdout_folded_stderr_summary(self, monkeypatch, capsys):
        self._install(monkeypatch)
        rc = record.dump_record(42, rate=50, duration=2.0, color=False)
        assert rc == 0
        out, err = capsys.readouterr()
        assert out == '"w";burn 9\n"m";main 4\n'
        assert "[i]" in err and "13" in err

    def test_output_file(self, monkeypatch, capsys, tmp_path):
        self._install(monkeypatch)
        path = tmp_path / "profile.folded"
        rc = record.dump_record(42, rate=50, duration=2.0, output=str(path))
        assert rc == 0
        assert path.read_text() == '"w";burn 9\n"m";main 4\n'
        out, _ = capsys.readouterr()
        assert out == ""  # nothing on stdout when -o is used

    def test_target_error_rc1(self, monkeypatch, capsys):
        def boom(pid, rate, duration):
            raise ProcessNotFound(pid)
        monkeypatch.setattr(record, "collect_profile", boom)
        rc = record.dump_record(999, color=False)
        assert rc == 1
        _, err = capsys.readouterr()
        assert "[!]" in err
