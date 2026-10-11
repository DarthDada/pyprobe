"""kernel/ — irreducible capabilities: byte transfer and dynamic control.

Owns everything that talks to the kernel directly: remote memory reads
(``mem``), memory-consistency views (``views``, ADR A4), /proc metadata
(``procfs``), and the unified ptrace engine (``ptrace``, ADR A2). This
package sits at the bottom of the dependency rule
(``kernel → target → cpython → observe → present → cli``) and must not
import from any upper layer (ADR A8, enforced by test_structure.py).
"""
