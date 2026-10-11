"""target/ — irreducible capabilities: symbol location and layout knowledge.

Owns everything known about the *target binary* rather than the kernel:
CPython process identification and version detection (``identity``),
per-version struct layouts (``layout``, ADR A1 — explicit ``Layout`` value
objects, no global singleton), and ELF symbol/constant location
(``symbols``). Sits above ``kernel/`` and below ``cpython/`` in the
dependency rule (ADR A8, enforced by test_structure.py).
"""
