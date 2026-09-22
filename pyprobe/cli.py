"""pyprobe CLI entry point.

Usage:
  python -m pyprobe stack -p <pid>            Python stack dump
  python -m pyprobe stack -p <pid> --native   Native stack dump (gdb-style)
  python -m pyprobe syscall -p <pid>           Syscall tracing (strace-style)
  python -m pyprobe record -p <pid>            Sample & write folded stacks
  python -m pyprobe top -p <pid>               Live sampling top view

The top-level command dispatches to subcommands. The ``stack`` subcommand
analyses the thread call stacks of a target CPython process; the
``syscall`` subcommand traces system calls of all its threads;
``record`` / ``top`` periodically sample its Python stacks.
"""

import argparse
import sys
from importlib.metadata import version as _pkg_version

from .native_dump import dump_native
from .record import dump_record
from .stack_dump import dump_python
from .syscall_tracer import dump_syscalls
from .top import dump_top


def _version() -> str:
    """Return the installed package version (single source: pyproject.toml).

    Resolved lazily via ``importlib.metadata`` so ``pyprobe.cli`` no longer
    depends on ``pyprobe.__init__`` for the version string — importing the
    CLI module does not force the package-root ``__version__`` attribute.
    """
    try:
        return _pkg_version("pyprobe")
    except Exception:
        return "0.0.0+unknown"


def _positive_float(lo, hi, what):
    """argparse type: float in (lo, hi] with a clear error message."""
    def check(value):
        try:
            f = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{what} must be a number, got {value!r}") from None
        if not (lo < f <= hi):
            raise argparse.ArgumentTypeError(
                f"{what} must be in ({lo:g}, {hi:g}], got {value!r}")
        return f
    return check


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pyprobe",
        description="CPython out-of-process stack inspection tool.",
    )
    parser.add_argument(
        "--version", action="version", version=f"pyprobe {_version()}")
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="<command>")

    stack = subparsers.add_parser(
        "stack",
        help="dump thread call stacks of a target process",
        description=(
            "Dump Python or native (C) call stacks of the threads of a "
            "target CPython process. By default Python stacks are dumped; "
            "pass --native for gdb-style native stacks via ptrace + libdwfl."
        ),
    )
    stack.add_argument(
        "-p", "--pid", type=int, required=True, metavar="<pid>",
        help="target process PID")
    stack.add_argument(
        "--native", action="store_true",
        help="dump native (C) stacks via ptrace + libdwfl instead of Python stacks")
    stack.add_argument(
        "-v", "--verbose", action="store_true",
        help="show full source file / module paths instead of shortened ones")
    stack.add_argument(
        "--json", action="store_true",
        help="output machine-readable JSON instead of text "
             "(full paths, never colored)")
    stack.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto",
        help="colorize output (default: auto-detect tty)")

    syscall = subparsers.add_parser(
        "syscall",
        help="trace system calls of all threads (strace-style)",
        description=(
            "Trace system calls of all threads of a target process in "
            "real time via ptrace (PTRACE_SEIZE + PTRACE_SYSCALL). "
            "Trace new threads automatically; render strace-style "
            "arguments for ~50 common syscalls."
        ),
    )
    syscall.add_argument(
        "-p", "--pid", type=int, required=True, metavar="<pid>",
        help="target process PID")
    syscall.add_argument(
        "-e", "--trace", default="", metavar="<expr>",
        help=(
            "filter which syscalls to trace: group names "
            "(file, network, process, memory, signal, desc) or "
            "comma-separated syscall names; '!'-prefix excludes "
            "(e.g. -e trace=file, -e trace=read,write, -e trace=!futex)"))
    syscall.add_argument(
        "--max-events", type=int, default=None, metavar="<n>",
        help="stop after <n> captured events (default: until Ctrl-C)")
    syscall.add_argument(
        "--summary", action="store_true",
        help="print a strace -c style summary table instead of "
             "streaming events")
    syscall.add_argument(
        "-v", "--verbose", action="store_true",
        help="show full string arguments instead of 32-char truncation")
    syscall.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto",
        help="colorize output (default: auto-detect tty)")

    record = subparsers.add_parser(
        "record",
        help="periodically sample stacks and write folded stacks",
        description=(
            "Periodically sample the Python stacks of a target process "
            "and aggregate them into folded stacks (flamegraph.pl / "
            "inferno-flamegraph input format). Idle threads are excluded. "
            "Samples at a fixed rate using process_vm_readv — no ptrace."
        ),
    )
    record.add_argument(
        "-p", "--pid", type=int, required=True, metavar="<pid>",
        help="target process PID")
    record.add_argument(
        "-r", "--rate", type=_positive_float(0, 1000, "rate"),
        default=50, metavar="<hz>",
        help="sampling rate in Hz (default: 50; must be in (0, 1000])")
    record.add_argument(
        "-d", "--duration", type=_positive_float(0, 3600, "duration"),
        default=None, metavar="<sec>",
        help="stop recording after <sec> seconds (default: until Ctrl-C)")
    record.add_argument(
        "-o", "--output", default=None, metavar="<file>",
        help="write folded stacks to <file> (default: stdout)")
    record.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto",
        help="colorize stderr progress output (default: auto-detect tty)")

    top = subparsers.add_parser(
        "top",
        help="live sampling view of thread hot spots",
        description=(
            "Continuously sample the Python stacks of a target process "
            "and redraw the terminal with per-thread current frames and "
            "an own/total hot-function ranking. Requires a terminal; "
            "Ctrl-C exits cleanly."
        ),
    )
    top.add_argument(
        "-p", "--pid", type=int, required=True, metavar="<pid>",
        help="target process PID")
    top.add_argument(
        "-r", "--rate", type=_positive_float(0, 1000, "rate"),
        default=50, metavar="<hz>",
        help="sampling rate in Hz (default: 50; must be in (0, 1000])")
    top.add_argument(
        "-i", "--interval", type=_positive_float(0, 60, "interval"),
        default=1.0, metavar="<sec>",
        help="terminal refresh interval in seconds (default: 1.0)")
    top.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto",
        help="colorize output (default: auto-detect tty)")

    return parser


_COLOR_MAP = {"auto": None, "always": True, "never": False}


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "stack":
        color = _COLOR_MAP[args.color]
        if args.native:
            return dump_native(args.pid, color=color, verbose=args.verbose,
                              json_output=args.json)
        return dump_python(args.pid, color=color, verbose=args.verbose,
                           json_output=args.json)

    if args.command == "syscall":
        color = _COLOR_MAP[args.color]
        # accept both "-e file" and "-e trace=file" (strace compat)
        trace = args.trace
        if trace.startswith("trace="):
            trace = trace[len("trace="):]
        return dump_syscalls(
            args.pid, color=color, verbose=args.verbose, trace=trace,
            max_events=args.max_events, summary=args.summary)

    if args.command == "record":
        return dump_record(
            args.pid, rate=args.rate, duration=args.duration,
            output=args.output, color=_COLOR_MAP[args.color])

    if args.command == "top":
        return dump_top(
            args.pid, rate=args.rate, interval=args.interval,
            color=_COLOR_MAP[args.color])

    return 1


if __name__ == "__main__":
    sys.exit(main())
