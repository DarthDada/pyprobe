"""Folded-stack profiling: periodic sampling + ``record`` subcommand.

Layered like the other features:

* ``collect_profile(pid, rate, duration)`` — run the sampling loop and
  aggregate into :class:`pyprobe.types.ProfileData`.  ``KeyboardInterrupt``
  and :class:`ProcessExited` are absorbed: the partial data collected so
  far is returned (that is what ``record`` should do when the user hits
  Ctrl-C or the target dies mid-recording).
* ``format_folded(profile)`` — pure data → folded-stacks text.
* ``dump_record(...)`` — CLI wrapper: collect + write folded stacks to
  stdout (or ``-o`` file) + stderr summary.  Folded text goes to stdout
  only, progress/summary to stderr only, so the output pipes cleanly
  into flamegraph tooling.
"""

import sys
import time
from typing import Optional

from .colors import red, should_color
from .sampler import Sampler
from .types import ProfileData, ThreadInfo
from .errors import ProcessExited, PyProbeError


def _fold_key(thread: ThreadInfo) -> str:
    """Fold one thread observation into a folded-stack key.

    Layout: ``<thread-prefix>;<root>;...;<leaf>``.  ``ThreadInfo.frames``
    is leaf→root, so the frames are reversed.  The thread prefix is the
    quoted thread name (or ``tid-<n>`` when unnamed) — names come from
    the Sampler's startup snapshot and stay stable for the whole
    recording, keeping aggregation keys consistent.  Frame names are bare
    function names (``None`` → ``?``), no file/line, matching the folded
    format consumed by flamegraph.pl / inferno-flamegraph.
    """
    if thread.name:
        prefix = f'"{thread.name}"'
    else:
        prefix = f"tid-{thread.native_tid}"
    names = [f.name if f.name is not None else "?" for f in thread.frames]
    return ";".join([prefix] + list(reversed(names)))


def collect_profile(pid: int, *, rate: float = 50,
                    duration: Optional[float] = None) -> ProfileData:
    """Sample ``pid`` at ``rate`` Hz until ``duration`` seconds elapse.

    ``duration=None`` samples until interrupted (Ctrl-C) or the target
    exits — in both cases the partial profile is returned.  Scheduling is
    absolute-time based (``next += interval``) so slow samples do not
    accumulate drift; when the loop falls behind by more than one
    interval the baseline resets instead of bursting catch-up samples.
    """
    sampler = Sampler(pid)
    interval = 1.0 / rate

    counts = {}
    samples = 0
    idle_samples = 0

    start = time.monotonic()
    next_t = start
    try:
        while duration is None or (time.monotonic() - start) < duration:
            for t in sampler.sample():
                if t.idle:
                    idle_samples += 1
                    continue
                key = _fold_key(t)
                counts[key] = counts.get(key, 0) + 1
                samples += 1

            next_t += interval
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # fell behind by more than an interval — reset the baseline
                # instead of firing a burst of catch-up samples
                next_t = time.monotonic()
    except (KeyboardInterrupt, ProcessExited):
        pass

    return ProfileData(
        proc_info=sampler.proc_info,
        counts=counts,
        samples=samples,
        idle_samples=idle_samples,
        elapsed=time.monotonic() - start,
        version_warning=sampler.session.version_warning,
    )


def format_folded(profile: ProfileData) -> str:
    """Render profile counts as sorted folded stacks.

    Lines are ordered by count descending, then key ascending — the
    output is deterministic for identical inputs.
    """
    lines = [
        f"{key} {count}"
        for key, count in sorted(profile.counts.items(),
                                 key=lambda kv: (-kv[1], kv[0]))
    ]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def dump_record(pid: int, *, rate: float = 50,
                duration: Optional[float] = None,
                output: Optional[str] = None,
                color: Optional[bool] = None) -> int:
    """CLI entry point: sample + write folded stacks. Returns exit code.

    Folded text is written to stdout (or the ``-o`` file); progress and
    the summary line go to stderr so stdout stays pipeable.
    """
    use_color = should_color(sys.stdout) if color is None else color
    err_color = should_color(sys.stderr) if color is None else color
    try:
        profile = collect_profile(pid, rate=rate, duration=duration)
    except PyProbeError as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

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
