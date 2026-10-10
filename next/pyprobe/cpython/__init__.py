"""cpython/ — irreducible capability: graph traversal of the CPython runtime.

Typed, layout-bound readers for CPython objects (ADR A3): every struct
offset comes from a ``target/layout.Layout`` passed in by the caller;
traversal code contains zero naked offset arithmetic, and all version
divergence lives in the Layout. Modules: ``objects`` (scalar object
readers), ``dicts`` (dictionary tables), ``code`` (CodeObject header +
PEP 626 linetable), ``frames`` (frame chain), ``runtime`` (interpreter /
thread graph), ``names`` (threading._active thread names). Sits above
``target/`` and below ``observe/`` in the dependency rule (ADR A8).
"""
