"""Batch-7 dual-tree CLI runner (helper subprocess for test_legacy_diff.py).

Usage:
    legacy_diff_runner.py <legacy_root> <next_root> <target_script> <cli args...>

Parents *target_script* (this process becomes an ancestor → allowed under
yama ptrace_scope=1), then runs the CLI twice in-process — once importing
pyprobe from each tree — and prints a single JSON line:

    {"pid": <pid>, "old": [rc, stdout, stderr], "new": [rc, stdout, stderr]}

Running both trees in ONE process is what makes this the ~same target~
comparison: pid/tid/thread_id/pc/rendered addresses are identical, so the
only deltas to normalise are timing fields (§10.5-1).  This helper is a
throwaway asset for the batch-7 gate and is removed at the swap (§10.6).
"""

import contextlib
import importlib
import io
import json
import subprocess
import sys

legacy_root, next_root, script, *cli_args = sys.argv[1:]


def _purge():
    for finder in list(sys.meta_path):
        mod = getattr(finder, "__module__", "") or ""
        if "editable" in mod.lower() or "editable" in type(finder).__name__.lower():
            sys.meta_path.remove(finder)
    for name in [m for m in sys.modules
                 if m == "pyprobe" or m.startswith("pyprobe.")]:
        del sys.modules[name]


def run_tree(tree_root, argv):
    state = list(sys.path)
    sys.path.insert(0, tree_root)
    _purge()
    try:
        cli = importlib.import_module("pyprobe.cli")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(argv))
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 0
        return [rc, out.getvalue(), err.getvalue()]
    finally:
        sys.path[:] = state
        _purge()


def _output_path(argv):
    """Return the -o/--output value if present (record's folded file)."""
    for i, a in enumerate(argv):
        if a in ("-o", "--output") and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--output="):
            return a.split("=", 1)[1]
    return None


def main():
    target = subprocess.Popen(
        [sys.executable, script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = target.stdout.readline()
        if not line.startswith("READY"):
            raise SystemExit(f"target failed to start: {line!r}")
        pid = int(line.split()[1])
        argv = [a.replace("__PID__", str(pid)) for a in cli_args]
        out_path = _output_path(argv)
        old = run_tree(legacy_root, argv)
        old_file = _read(out_path)
        new = run_tree(next_root, argv)
        new_file = _read(out_path)
        print(json.dumps({
            "pid": pid, "old": old, "new": new,
            "old_output": old_file, "new_output": new_file,
        }))
    finally:
        target.terminate()
        try:
            target.wait(timeout=5)
        except subprocess.TimeoutExpired:
            target.kill()
            target.wait()
        target.stdout.close()


def _read(path):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


main()
