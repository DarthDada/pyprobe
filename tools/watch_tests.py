"""Watch sources and tests, re-running pytest on every change.

Invoked by ``scripts/run_tests.sh watch``: the shell script resolves the mode
selector (unit / integration / all, default unit) plus any extra pytest args
and passes them as argv; this module owns the watchfiles loop.

Ctrl-C stops the watcher cleanly (exit 0). The pytest exit code is
intentionally NOT propagated -- the whole point is to keep iterating on a
red suite; use plain ``scripts/run_tests.sh`` for a failing exit code.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCH_PATHS = (ROOT / "pyprobe", ROOT / "tests")
DEBOUNCE_MS = 250  # batch editor atomic writes / multi-file saves


def run_pytest(extra):
    print(f"\n[watch] pytest {' '.join(extra)}", flush=True)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", *extra], cwd=ROOT
    ).returncode


def main(argv):
    try:
        from watchfiles import watch
    except ImportError:
        sys.exit("watchfiles is not installed -- run scripts/sync.sh first")

    run_pytest(argv)
    print("[watch] watching: pyprobe/ tests/  (Ctrl-C to stop)", flush=True)
    try:
        for _changes in watch(
            *WATCH_PATHS,
            watch_filter=lambda change, path: path.endswith(".py"),
            debounce=DEBOUNCE_MS,
            step=50,
        ):
            run_pytest(argv)
    except KeyboardInterrupt:
        print("\n[watch] stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
