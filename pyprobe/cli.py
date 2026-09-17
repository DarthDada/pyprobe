"""pyprobe CLI entry point.

Usage:
  python -m pyprobe <pid>            Python stack dump
  python -m pyprobe <pid> --native   Native stack dump (gdb-style)
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
    parser.add_argument("pid", type=int, help="target process PID")
    parser.add_argument(
        "--native", action="store_true",
        help="dump native (C) stacks via ptrace + libdwfl instead of Python stacks")
    parser.add_argument(
        "--version", action="version", version=f"pyprobe {__version__}")
    return parser


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.native:
        return dump_native(args.pid)
    return dump_python(args.pid)


if __name__ == "__main__":
    sys.exit(main())
