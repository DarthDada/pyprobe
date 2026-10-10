"""Dump-layer orchestration (collect/format/dump 三层契约的 dump 层).

Each ``dump_*``: decide color per stream (or forced), collect, print
(text or JSON), surface the session version warning, return an exit
code. Printing happens ONLY here and in cli — collect/format layers
never print.

契约（contracts.md 批 6 表 DP1–DP5）。编排逻辑自旧树各 dump_* 复刻；
collect/format 行为由 observe/present 各自批次守护，本层只钉编排。
"""

import sys
import time

from ..errors import (
    AttachFailed,
    NoInterpreterState,
    NoThreadState,
    ProcessExited,
    ProcessNotFound,
    PyProbeError,
    SymbolNotFound,
    UnsupportedArchitecture,
)
from ..kernel.procfs import read_cmdline, read_comm  # noqa: F401  # 接缝
from ..observe.native import collect_native
from ..observe.profile import collect_profile
from ..observe.sampling import Sampler
from ..observe.session import open_session
from ..observe.snapshot import collect_snapshot
from ..observe.syscalls import collect_syscalls
from ..observe.topstats import TopStats
from .color import red, should_color, yellow_bold
from .jsonout import (
    format_native_json,
    format_process_json,
    format_syscalls_json,
)
from .text import (
    CLEAR_SCREEN,
    HIDE_CURSOR,
    SHOW_CURSOR,
    format_folded,
    format_native,
    format_process,
    format_summary,
    format_syscall_event,
    render_top,
)


def _stdout_is_tty():
    """Terminal check, factored out for testability (capsys swaps
    ``sys.stdout`` after fixtures patch it, so an instance-level isatty
    monkeypatch would be lost — 旧 top.py 同款接缝)."""
    return sys.stdout.isatty()


def dump_python(pid: int, color: bool | None = None, verbose: bool = False,
                json_output: bool = False) -> int:
    """CLI entry: collect + format + print Python stacks (DP1/DP2).

    The version warning is printed before thread collection (it is the
    root cause when collection later fails with fallback offsets — must
    not be swallowed by the early error return).
    """
    # DP1: color is decided per stream (clicolors); a forced value applies
    # to both. JSON output is never colored (jsonout has no color path).
    use_color = should_color(sys.stdout) if color is None else color
    err_color = should_color(sys.stderr) if color is None else color
    try:
        session = open_session(pid)
    except (ProcessNotFound, SymbolNotFound,
            NoInterpreterState, OSError) as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    # DP2: surface the version warning BEFORE thread collection — when the
    # latter fails with wrong fallback offsets (unverified version), the
    # warning is the root cause and must not be swallowed by the early
    # error return (旧 stack_dump L338-342 语义).
    if session.version_warning:
        print(red(session.version_warning, err_color), file=sys.stderr)

    try:
        threads = collect_snapshot(session)
    except (NoThreadState, OSError) as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    if json_output:
        print(format_process_json(session.proc_info, threads))
    else:
        print(format_process(session.proc_info, threads, color=use_color,
                             verbose=verbose))
    return 0


def dump_native(pid: int, color: bool | None = None, verbose: bool = False,
                json_output: bool = False) -> int:
    """CLI entry: collect + format + print native stacks (DP1)."""
    use_color = should_color(sys.stdout) if color is None else color
    err_color = should_color(sys.stderr) if color is None else color
    cmdline = read_cmdline(pid) or ""
    if not json_output:
        # Header printed before collecting, exactly once (旧树曾重复打印,
        # 2026-10 修复语义) so failure paths keep their context.
        print(f"Process {yellow_bold(str(pid), use_color)}: {cmdline}\n")

    try:
        threads = collect_native(pid)
    except (AttachFailed, OSError) as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    if json_output:
        print(format_native_json(pid, cmdline, threads))
    else:
        print(format_native(threads, color=use_color, verbose=verbose))
    return 0


def dump_syscalls(pid: int, *, color: bool | None = None,
                  verbose: bool = False, trace: str = "",
                  max_events=None, summary: bool = False,
                  json_output: bool = False) -> int:
    """CLI entry: trace + stream events (or summary/JSON) (DP3).

    KeyboardInterrupt detaches cleanly and still prints the summary of
    what was collected; ``json_output`` takes precedence over ``summary``
    and is never colored.
    """
    use_color = should_color(sys.stdout) if color is None else color
    err_color = should_color(sys.stderr) if color is None else color

    # DP3 + SY14: the on_event channel accumulates every emitted event and
    # (in stream mode) prints it live. Accumulation is what survives a
    # KeyboardInterrupt — collect_syscalls never returns in that case.
    events = []

    def on_event(ev):
        events.append(ev)
        if not summary and not json_output:
            print(format_syscall_event(ev, color=use_color))

    try:
        collect_syscalls(pid, trace=trace, max_events=max_events,
                         verbose=verbose, on_event=on_event)
    except (AttachFailed, ProcessNotFound,
            UnsupportedArchitecture) as e:
        # DP3: attach-time failures (A7 P0-2 的 CLI 面——UnsupportedArchitecture
        # 同样走此径).
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # DP3: Ctrl-C — the engine detached in its own finally; fall
        # through and summarize what the on_event channel collected.
        if not json_output:
            print()

    if json_output:
        # DP3: json takes precedence over summary and is never colored.
        print(format_syscalls_json(events))
    elif summary:
        print(format_summary(events, color=use_color))
    return 0


def dump_record(pid: int, *, rate: float = 50,
                duration: float | None = None,
                output: str | None = None,
                color: bool | None = None) -> int:
    """CLI entry: sample + write folded stacks (DP4).

    Folded text goes to stdout (or ``-o`` file) only; progress/summary
    go to stderr only — stdout stays pipeable into flamegraph tooling.
    """
    err_color = should_color(sys.stderr) if color is None else color
    try:
        profile = collect_profile(pid, rate=rate, duration=duration)
    except PyProbeError as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    # DP4: the session version warning surfaces once at the dump layer.
    if profile.version_warning:
        print(red(profile.version_warning, err_color), file=sys.stderr)

    folded = format_folded(profile)
    if output is None:
        sys.stdout.write(folded)
    else:
        with open(output, "w") as f:
            f.write(folded)

    print(
        f"[i] pyprobe recorded {profile.samples} samples "
        f"(active {profile.samples}, idle {profile.idle_samples}) "
        f"from PID {pid} in {profile.elapsed:.1f}s",
        file=sys.stderr)
    return 0


def dump_top(pid: int, *, rate: float = 50, interval: float = 1.0,
             color: bool | None = None) -> int:
    """CLI entry: continuous sampling + terminal redraw (DP5).

    Returns 2 when stdout is not a terminal (no meaningful non-interactive
    downgrade — use record); 0 on clean exit (Ctrl-C or target death,
    cursor restored via finally); 1 on target errors.
    """
    use_color = should_color(sys.stdout) if color is None else color
    err_color = should_color(sys.stderr) if color is None else color

    if not _stdout_is_tty():
        # DP5: no non-interactive downgrade (README 承诺, 复刻约束) — rc=2.
        print(red("[!] pyprobe top requires a terminal (stdout is not a "
                  "tty); use pyprobe record for non-interactive sampling",
                  err_color), file=sys.stderr)
        return 2

    try:
        sampler = Sampler(pid)
    except PyProbeError as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    # DP5: the session version warning surfaces once at startup.
    if sampler.session.version_warning:
        print(red(sampler.session.version_warning, err_color),
              file=sys.stderr)

    stats = TopStats()
    sample_interval = 1.0 / rate
    out = sys.stdout

    out.write(HIDE_CURSOR)
    out.flush()
    start = time.monotonic()
    next_sample = start
    # DP5: render the first screen immediately, then once per interval.
    next_render = start
    try:
        while True:
            threads = sampler.sample()  # ProcessExited → handled below
            stats.update(threads)

            now = time.monotonic()
            if now >= next_render:
                out.write(CLEAR_SCREEN
                          + render_top(stats, sampler.proc_info,
                                       elapsed=now - start,
                                       color=use_color))
                out.flush()
                sampler.refresh_names()
                next_render += interval

            # Absolute-time scheduling: slow samples do not accumulate
            # drift; falling behind resets the baseline instead of
            # bursting catch-up samples.
            next_sample += sample_interval
            delay = next_sample - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_sample = time.monotonic()
    except (KeyboardInterrupt, ProcessExited):
        pass  # DP5: clean exit paths — the summary below still prints
    finally:
        out.write(SHOW_CURSOR)  # DP5: cursor restored on every exit path
        out.flush()

    print(f"[i] pyprobe top: {stats.samples} samples "
          f"in {time.monotonic() - start:.1f}s", file=sys.stderr)
    return 0
