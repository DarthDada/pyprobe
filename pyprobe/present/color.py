"""ANSI color helpers for pyprobe terminal output (A6 保留).

Zero-dependency (stdlib only). Color decision follows the clicolors spec
(https://bixense.com/clicolors/): CLICOLOR_FORCE forces color on;
otherwise color requires a tty without NO_COLOR, without TERM=dumb, and
CLICOLOR != "0".

All helpers are pure functions: with ``color=False`` (default) they return
``text`` unchanged, so non-color call sites are byte-identical to the
no-color output (契约 CL1–CL3; 与旧 colors.py 逐字语义).
"""

import os

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"

__all__ = [
    "BOLD",
    "CYAN",
    "DIM",
    "GREEN",
    "RED",
    "RESET",
    "YELLOW",
    "cyan",
    "dim",
    "green",
    "red",
    "should_color",
    "yellow_bold",
]


def yellow_bold(text, color=False):
    """PID / TID: bold + yellow.  Identity when color is False."""
    return f"{BOLD}{YELLOW}{text}{RESET}" if color else text


def green(text, color=False):
    """Function / symbol names.  Identity when color is False."""
    return f"{GREEN}{text}{RESET}" if color else text


def cyan(text, color=False):
    """Filenames / module paths.  Identity when color is False."""
    return f"{CYAN}{text}{RESET}" if color else text


def dim(text, color=False):
    """Line numbers, PC values, (idle) markers.  Identity when color is False."""
    return f"{DIM}{text}{RESET}" if color else text


def red(text, color=False):
    """Error lines ([!] prefix).  Identity when color is False."""
    return f"{RED}{text}{RESET}" if color else text


def should_color(stream):
    """Decide whether ANSI color should be emitted to ``stream``.

    Priority (clicolors spec, 契约 CL3):
      CLICOLOR_FORCE != "0"      -> True (unconditional, beats NO_COLOR)
      NO_COLOR non-empty         -> False
      not stream.isatty()        -> False
      TERM == "dumb"             -> False
      CLICOLOR == "0"            -> False
      otherwise                  -> True
    """
    if os.environ.get("CLICOLOR_FORCE", "0") != "0":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if not stream.isatty():
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("CLICOLOR", "1") == "0":
        return False
    return True
