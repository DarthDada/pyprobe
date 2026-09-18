"""pyprobe CLI entry point.

Usage:
  python -m pyprobe stack -p <pid>            Python stack dump
  python -m pyprobe stack -p <pid> --native   Native stack dump (gdb-style)

The top-level command dispatches to subcommands. The ``stack`` subcommand
analyses the thread call stacks of a target CPython process.
"""

import argparse
import sys

from .stack_dump import dump_python
from .native_dump import dump_native
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
            return dump_native(args.pid, color=color)
        return dump_python(args.pid, color=color)

    return 1


if __name__ == "__main__":
    sys.exit(main())
