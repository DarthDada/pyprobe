"""Unit tests for pyprobe.top — live hot-spot view (TopStats + dump_top).

``TopStats`` aggregation/rendering is tested as pure logic with preset
``ThreadInfo`` lists; ``dump_top`` is tested with a stubbed Sampler, a
fake clock, and monkeypatched ``sys.stdout.isatty``.
"""

import sys
import time

import pytest

import pyprobe.top
from pyprobe.top import TopStats, dump_top
from pyprobe.types import FrameInfo, ThreadInfo, ProcessInfo
from pyprobe.errors import ProcessExited, ProcessNotFound
from pyprobe import top


def _thread(tid, name, frames, idle=False):
    return ThreadInfo(native_tid=tid, name=name,
                      frames=[FrameInfo(n, f, ln) for n, f, ln in frames],
                      idle=idle)


PROC = ProcessInfo(pid=42, cmdline="python3 app.py",
                   exe_path="/usr/bin/python3.12", python_version="3.12.13")


class TestUpdate:
    def test_own_and_total(self):
        stats = TopStats()
        # two samples: A→B→C and A→B (frames are leaf→root)
        stats.update([_thread(1, "w", [("C", "x.py", 3), ("B", "x.py", 2),
                                        ("A", "x.py", 1)])])
        stats.update([_thread(1, "w", [("B", "x.py", 2), ("A", "x.py", 1)])])
        assert stats.own == {"C": 1, "B": 1}
        assert stats.total == {"A": 2, "B": 2, "C": 1}

    def test_idle_excluded(self):
        stats = TopStats()
        stats.update([_thread(1, "w", [], idle=True)])
        stats.update([_thread(1, "w", [("burn", "s.py", 8)])])
        assert stats.own == {"burn": 1}
        assert stats.total == {"burn": 1}
        assert stats.idle_samples == 1
        assert stats.samples == 1

    def test_current_frame_tracked(self):
        stats = TopStats()
        stats.update([_thread(7, "w", [("f1", "x.py", 1)])])
        stats.update([_thread(7, "w", [("f2", "x.py", 2)])])
        assert stats.current[7][0].name == "f2"

    def test_empty_stack_active_thread_counts_as_sample(self):
        # an active thread with no frames still bumps samples (edge case)
        stats = TopStats()
        stats.update([_thread(1, "w", [])])
        assert stats.samples == 1


class TestRender:
    def _stats(self):
        stats = TopStats()
        for _ in range(3):
            stats.update([_thread(1, "spin", [("burn", "s.py", 8),
                                              ("main", "s.py", 2)])])
        for _ in range(1):
            stats.update([_thread(2, "m", [("main", "a.py", 20)])])
        return stats

    def test_layout_headers_and_values(self):
        out = self._stats().render(PROC, elapsed=4.0, color=False)
        assert out.startswith("Process 42: python3 app.py\n")
        assert "Python v3.12.13 (/usr/bin/python3.12)" in out
        assert "Active threads" in out and "Top functions" in out
        assert "burn" in out and "spin" in out
        # burn: own 3/4 = 75.0%
        assert "75.0%" in out
        # total: 3/4
        assert "TOTAL%" in out

    def test_no_ansi_without_color(self):
        out = self._stats().render(PROC, elapsed=4.0, color=True)
        assert "\x1b" in out
        plain = self._stats().render(PROC, elapsed=4.0, color=False)
        assert "\x1b" not in plain

    def test_top_n_truncates(self):
        stats = TopStats()
        # 30 samples, each with a different leaf function: ranking is
        # all-tied so top_n=10 must cut the list of 30
        for i in range(30):
            stats.update([_thread(1, "w", [(f"f{i:02d}", "x.py", i)])])
        out = stats.render(PROC, elapsed=1.0, color=False, top_n=10)
        tail = out.split("Top functions")[-1]
        assert tail.count("f") == 10  # exactly 10 function rows
        assert "f29" not in tail

    def test_idle_threads_listed(self):
        stats = TopStats()
        stats.update([_thread(1, "w", [], idle=True)])
        out = stats.render(PROC, elapsed=1.0, color=False)
        assert "Idle threads: 1" in out


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, d):
        assert d > 0
        self.sleeps.append(d)
        self.now += d


class StubSampler:
    preset = []

    def __init__(self, pid):
        self.pid = pid
        self._queue = list(type(self).preset)
        self.proc_info = PROC
        self.n_samples = 0

    def sample(self):
        self.n_samples += 1
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return []

    def refresh_names(self):
        pass


@pytest.fixture
def tty(monkeypatch):
    # patch the check function: capsys replaces sys.stdout after instance
    # patches, so patching sys.stdout.isatty directly would be lost
    monkeypatch.setattr(pyprobe.top, "_stdout_is_tty", lambda: True)


@pytest.fixture
def fake_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    return clock


class TestDumpTop:
    def test_not_a_tty_rc2(self, monkeypatch, capsys):
        monkeypatch.setattr(pyprobe.top, "_stdout_is_tty", lambda: False)
        monkeypatch.setattr(top, "Sampler", StubSampler)
        rc = dump_top(42, color=False)
        assert rc == 2
        err = capsys.readouterr().err
        assert "terminal" in err

    def test_target_error_rc1(self, monkeypatch, capsys, tty):
        class BoomSampler(StubSampler):
            def __init__(self, pid):
                raise ProcessNotFound(pid)
        monkeypatch.setattr(top, "Sampler", BoomSampler)
        rc = dump_top(999, color=False)
        assert rc == 1
        err = capsys.readouterr().err
        assert "[!]" in err

    def test_exits_on_process_exited(self, monkeypatch, capsys, tty,
                                     fake_clock):
        StubSampler.preset = [
            [_thread(1, "w", [("burn", "s.py", 8)])],
            ProcessExited(42),
        ]
        monkeypatch.setattr(top, "Sampler", StubSampler)
        rc = dump_top(42, rate=50, interval=1000.0, color=False)
        assert rc == 0
        out, err = capsys.readouterr()
        assert "burn" in out or "burn" in err

    def test_keyboardinterrupt_rc0(self, monkeypatch, capsys, tty,
                                   fake_clock):
        StubSampler.preset = [
            [_thread(1, "w", [("burn", "s.py", 8)])],
            KeyboardInterrupt(),
        ]
        monkeypatch.setattr(top, "Sampler", StubSampler)
        rc = dump_top(42, rate=50, interval=1000.0, color=False)
        assert rc == 0

    def test_first_render_before_first_interval(self, monkeypatch, capsys,
                                                tty, fake_clock):
        # interval 1000s: only one render must happen, immediately
        StubSampler.preset = [
            [_thread(1, "w", [("burn", "s.py", 8)])],
            KeyboardInterrupt(),
        ]
        monkeypatch.setattr(top, "Sampler", StubSampler)
        rc = dump_top(42, rate=50, interval=1000.0, color=False)
        assert rc == 0
        out, _ = capsys.readouterr()
        assert "Process 42" in out
        assert "burn" in out
