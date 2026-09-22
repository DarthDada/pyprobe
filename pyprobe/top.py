"""Live hot-spot view: continuous sampling + terminal refresh.

``TopStats`` is the incremental aggregation (pure data in, pure stats
out): ``own`` counts samples where a function was the leaf frame,
``total`` counts samples where it appeared anywhere on a stack.
``dump_top`` runs a single interleaved loop — sampling at ``rate``
accumulates into TopStats, every ``interval`` seconds the terminal is
redrawn with the accumulated statistics.  A dedicated sampling thread
would add GIL/KeyboardInterrupt complexity for no benefit: rendering
takes a few ms and absolute-time scheduling absorbs the hiccup.
"""

import sys
import time

from .colors import cyan, dim, green, red, should_color, yellow_bold
from .errors import ProcessExited, PyProbeError
from .sampler import Sampler
from .types import FrameInfo, ProcessInfo, ThreadInfo

HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[H\x1b[2J"


def _stdout_is_tty():
    """Terminal check, factored out for testability (capsys swaps
    ``sys.stdout`` after fixtures patch it, so an instance-level isatty
    monkeypatch would be lost)."""
    return sys.stdout.isatty()


class TopStats:
    """Incremental aggregation of sampled threads."""

    def __init__(self):
        self.own: dict[str, int] = {}
        self.total: dict[str, int] = {}
        self.samples = 0
        self.idle_samples = 0
        # tid → (top FrameInfo, name) of the most recent active sample
        self.current: dict[int, tuple[FrameInfo, str]] = {}
        self.idle_threads: list[int] = []

    def update(self, threads: list[ThreadInfo]) -> None:
        for t in threads:
            if t.idle:
                self.idle_samples += 1
                if t.native_tid not in self.current:
                    self.idle_threads.append(t.native_tid)
                continue
            self.idle_threads = [tid for tid in self.idle_threads
                                if tid != t.native_tid]
            self.samples += 1
            if t.frames:
                top = t.frames[0]
                name = top.name if top.name is not None else "?"
                self.own[name] = self.own.get(name, 0) + 1
                self.current[t.native_tid] = (top, t.name)
            else:
                self.current[t.native_tid] = (None, t.name)
            seen = set()
            for f in t.frames:
                name = f.name if f.name is not None else "?"
                if name not in seen:
                    seen.add(name)
                    self.total[name] = self.total.get(name, 0) + 1

    def render(self, proc_info: ProcessInfo, *, elapsed: float,
               color: bool = False, top_n: int = 15) -> str:
        total = max(self.samples, 1)

        lines = [proc_info.format_header(color=color).rstrip()]
        lines.append(
            f"Elapsed {elapsed:.1f}s | {self.samples} samples "
            f"(idle {self.idle_samples})")
        lines.append("")

        lines.append("Active threads")
        lines.append(f"  {'TID':<7} {'OWN%':>6}  CURRENT")
        for tid, (frame, name) in sorted(self.current.items()):
            tid_s = yellow_bold(str(tid), color)
            label = f"{name} " if name else ""
            if frame is not None:
                own = self.own.get(frame.name, 0) / total
                fname = green(frame.name or "?", color)
                loc = f" ({cyan(frame.filename or '?', color)}:{frame.line})"
                current = f"{label}{fname}{dim(loc, color)}"
            else:
                own = 0.0
                current = dim(f"{label}(no Python frame)", color)
            lines.append(f"  {tid_s:<7} {own * 100:>5.1f}%  {current}")
        lines.append("")

        if self.idle_threads:
            idlers = ", ".join(str(t) for t in sorted(self.idle_threads))
            lines.append(f"Idle threads: {idlers}")
            lines.append("")

        lines.append("Top functions")
        lines.append(f"  {'OWN%':>6} {'TOTAL%':>7}  {'TIME':>7}  FUNCTION")
        ranked = sorted(self.own.items(), key=lambda kv: (-kv[1], kv[0]))
        for name, own_n in ranked[:top_n]:
            own_pct = own_n / total * 100
            tot_pct = self.total.get(name, 0) / total * 100
            time_s = own_n / total * elapsed
            lines.append(
                f"  {dim(f'{own_pct:>5.1f}%', color)} "
                f"{dim(f'{tot_pct:>6.1f}%', color)}  "
                f"{dim(f'{time_s:>6.1f}s', color)}  "
                f"{green(name, color)}")
        return "\n".join(lines) + "\n"


def dump_top(pid: int, *, rate: float = 50, interval: float = 1.0,
             color=None) -> int:
    """CLI entry point: continuous sampling + terminal redraw.

    Returns 0 on clean exit (Ctrl-C or target death), 1 on target
    errors, 2 when stdout is not a terminal (the live view has no
    meaningful non-interactive downgrade — use ``record`` for that).
    """
    use_color = should_color(sys.stdout) if color is None else color
    err_color = should_color(sys.stderr) if color is None else color

    if not _stdout_is_tty():
        print(red("[!] pyprobe top requires a terminal (stdout is not a "
                  "tty); use pyprobe record for non-interactive sampling",
                  err_color), file=sys.stderr)
        return 2

    try:
        sampler = Sampler(pid)
    except PyProbeError as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    # Surface the unverified-version warning once at startup (TODO §8.5):
    # the collect layer (resolve_process) captures it on the session; the
    # dump layer (here) prints it.
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
    # render the first screen immediately, then once per interval
    next_render = start
    try:
        while True:
            threads = sampler.sample()  # ProcessExited → handled below
            stats.update(threads)

            now = time.monotonic()
            if now >= next_render:
                out.write(CLEAR_SCREEN
                          + stats.render(sampler.proc_info,
                                         elapsed=now - start,
                                         color=use_color))
                out.flush()
                sampler.refresh_names()
                next_render += interval

            next_sample += sample_interval
            delay = next_sample - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_sample = time.monotonic()
    except (KeyboardInterrupt, ProcessExited):
        pass
    finally:
        out.write(SHOW_CURSOR)
        out.flush()

    print(f"[i] pyprobe top: {stats.samples} samples "
          f"in {time.monotonic() - start:.1f}s", file=sys.stderr)
    return 0
