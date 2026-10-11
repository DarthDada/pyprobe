"""present/ — irreducible capability: presentation.

Everything that turns collected data into user-facing output: ``color``
(ANSI strategy, clicolors rules — the only module besides cli that may
know about color, ADR A8), ``text`` (all human-readable formatting, ADR
A5 — DTOs carry no format() methods), ``jsonout`` (machine-readable
JSON), and ``dumps`` (the dump-layer orchestration: collect + color
decision + print + exit code). Sits above ``observe/``; only ``cli``
sits above it in the dependency rule (ADR A8).
"""
