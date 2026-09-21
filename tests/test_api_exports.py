"""Smoke tests for the public API surface (``__all__`` exports).

Guards against ``__all__`` entries that lack a matching import binding —
``from pyprobe import *`` must not raise ``ImportError``.
"""

import pyprobe


def test_star_import_resolves():
    # __all__ entries without a namespace binding would raise ImportError
    ns = {}
    exec("from pyprobe import *", ns)


def test_all_names_resolve_in_namespace():
    for name in pyprobe.__all__:
        assert hasattr(pyprobe, name), f"__all__ entry {name!r} has no binding"
