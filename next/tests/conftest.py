"""Dual-tree import guard (TODO §10.4-0).

Both trees ship a package named ``pyprobe`` and share one venv. The root
project is installed into that venv as an *editable* install, and setuptools'
editable finder sits on ``sys.meta_path`` and maps ``pyprobe.<module>``
lookups back to the legacy tree — even when the parent package resolved to
``next/pyprobe``. Left in place, a missing next-side module would silently
fall back to frozen legacy code, defeating the dual-tree isolation.

Purging that finder here (conftest runs before any test module imports
``pyprobe``) makes the legacy tree unreachable from the next suite;
``test_scaffold.py`` pins the resulting isolation mechanically.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _purge_legacy_editable_finder() -> None:
    for finder in list(sys.meta_path):
        module = getattr(finder, "__module__", "") or ""
        if "editable" in module.lower() or "editable" in type(finder).__name__.lower():
            sys.meta_path.remove(finder)
    # A repo-root sys.path entry (e.g. from a non-strict editable .pth) would
    # also expose the legacy tree; drop it for the same reason.
    for entry in list(sys.path):
        if entry and Path(entry).resolve() == _REPO_ROOT:
            sys.path.remove(entry)
    # If anything imported pyprobe before this guard ran, force re-resolution.
    for name in [m for m in sys.modules if m == "pyprobe" or m.startswith("pyprobe.")]:
        del sys.modules[name]


_purge_legacy_editable_finder()
