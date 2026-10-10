"""Compatibility facade over ``target/layout.py`` (§10.5-2, 契约 API3).

The old ``pyprobe.offsets`` module exposed ``get``/``get_or`` backed by a
mutable global singleton — the exact thing ADR A1 removed. This facade
keeps the *read-only* surface working for library users (backed by an
immutable default-version ``Layout`` resolved once, no ``configure`` and
no read-path side effects). New code inside pyprobe must NOT import this
module (it goes through ``target/layout.py`` explicitly); the ban is
enforced by test_structure.py.
"""

from .target.layout import (
    DEFAULT_VERSION,
    resolve_layout,
    supported_versions,
)

_default_layout = resolve_layout(DEFAULT_VERSION)


def get(name):
    """Offset ``name`` from the default-version layout (KeyError if absent)."""
    return _default_layout.get(name)


def get_or(name, default=None):
    """Like get(), but ``default`` for keys absent in this version."""
    return _default_layout.get_or(name, default)


__all__ = [
    "DEFAULT_VERSION",
    "get",
    "get_or",
    "resolve_layout",
    "supported_versions",
]
