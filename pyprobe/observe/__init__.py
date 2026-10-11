"""observe/ — irreducible capability: the time dimension.

Everything that turns a resolved target into observations over time:
``session`` (process-lifetime-invariant resolution + view factory),
``snapshot`` (single-shot stack collection + idle heuristics),
``sampling`` (the periodic hot path), ``profile`` (folded-stack
aggregation), ``topstats`` (incremental top aggregation). Memory
consistency is declared per observation (ADR A4): snapshots and samples
build a fresh ``SnapshotView`` every call; long-running tracing (batch 5)
uses ``LiveView``. Sits above ``cpython/`` and below ``present/`` in the
dependency rule (ADR A8).
"""
