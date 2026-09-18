"""ANSI color helpers for pyprobe terminal output.

Zero-dependency (stdlib only), Python 3.8+.  Color decision follows the
clicolors spec (https://bixense.com/clicolors/) used by py-spy's console
crate: CLICOLOR_FORCE forces color on; otherwise color requires a tty
without NO_COLOR, without TERM=dumb, and CLICOLOR != "0".

All helpers are pure functions: with ``color=False`` (default) they return
``text`` unchanged, so non-color call sites are byte-identical to the
pre-color output.
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
    "RESET", "BOLD", "DIM", "RED", "GREEN", "YELLOW", "CYAN",
    "yellow_bold", "green", "cyan", "dim", "red", "should_color",
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

    ``stream`` is a file-like object (sys.stdout / sys.stderr).

    Priority (clicolors spec):
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
