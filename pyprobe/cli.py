"""pyprobe CLI entry point.

Usage:
  python -m pyprobe <pid>            Python stack dump
  python -m pyprobe <pid> --native   Native stack dump (gdb-style)
"""

import sys

from .stack_dump import dump_python
from .native_dump import dump_native


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    native_mode = False
    if len(argv) == 2 and argv[1] == "--native":
        native_mode = True
    elif len(argv) != 1:
        print(f"usage: {sys.argv[0]} <pid> [--native]")
        return 1

    pid = int(argv[0])

    if native_mode:
        return dump_native(pid)
    else:
        return dump_python(pid)


if __name__ == "__main__":
    sys.exit(main())
