"""pyprobe — CPython out-of-process inspection toolset (next/ rebuild tree).

This package is the first-principles rebuild of the legacy ``pyprobe/`` tree
(TODO §10). The legacy tree is frozen; all new code lands here under the
layering ``kernel → target → cpython → observe → present → cli`` (ADR A8, see
``next/docs/contracts.md``). The package keeps the name ``pyprobe`` so the
eventual replacement (§10.6) is a pure directory swap with no renames.

Public API is intentionally empty until batch 1–6 contracts land; ``__all__``
is diffed against the legacy tree at the §10.5 alignment gate.
"""

__all__: list[str] = []
