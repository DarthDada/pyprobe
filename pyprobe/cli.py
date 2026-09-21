"""pyprobe CLI entry point.

Usage:
  python -m pyprobe stack -p <pid>            Python stack dump
  python -m pyprobe stack -p <pid> --native   Native stack dump (gdb-style)
  python -m pyprobe syscall -p <pid>          Syscall tracing (strace-style)

The top-level command dispatches to subcommands. The ``stack`` subcommand
analyses the thread call stacks of a target CPython process; the
``syscall`` subcommand traces system calls of all its threads.
"""

import argparse
import sys

from .stack_dump import dump_python
from .native_dump import dump_native
from .syscall_trace import dump_syscalls
from . import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pyprobe",
        description="CPython out-of-process stack inspection tool.",
    )
    parser.add_argument(
        "--version", action="version", version=f"pyprobe {__version__}")
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
            return dump_native(args.pid, color=color, verbose=args.verbose)
        return dump_python(args.pid, color=color, verbose=args.verbose)

    if args.command == "syscall":
        color = _COLOR_MAP[args.color]
        # accept both "-e file" and "-e trace=file" (strace compat)
        trace = args.trace
        if trace.startswith("trace="):
            trace = trace[len("trace="):]
        return dump_syscalls(
            args.pid, color=color, verbose=args.verbose, trace=trace,
            max_events=args.max_events, summary=args.summary)

    return 1


if __name__ == "__main__":
    sys.exit(main())
