"""Structural contract tests (contracts.md §1, ADR A8).

Mechanically enforce the dependency rule
``kernel → target → cpython → observe → present → cli`` by scanning imports
in next/pyprobe with ast (no imports of the modules under test, so the
guard also works when a layer is broken mid-development).

Rules:
* a layer module may import cross-cutting modules (errors, dto) and modules
  of its own or lower layers — never a higher layer;
* cross-cutting modules must not import any layer module;
* cli (pyprobe.cli / pyprobe.__main__) may import everything.
"""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "pyprobe"

LAYERS = ["kernel", "target", "cpython", "observe", "present"]
CROSS_CUTTING = {"errors", "dto"}
TOP_LEVELS = set(LAYERS) | CROSS_CUTTING | {"cli", "__main__", "offsets",
                                            "colors"}


def _rank(name: str) -> int | None:
    """Layer rank of a dotted pyprobe name; None if not a layer module."""
    top = name.split(".")[0]
    if top in CROSS_CUTTING:
        return -1
    if top in LAYERS:
        return LAYERS.index(top)
    return None  # cli / __main__ / package root


def _pyprobe_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel_parent = path.parent.relative_to(PKG)
    pkg_parts = [] if str(rel_parent) == "." else str(rel_parent).split("/")
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("pyprobe"):
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: anchor at this file's package
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                module = ".".join(["pyprobe", *base])
                if node.module:
                    module += "." + node.module
            else:
                module = node.module or ""
            if module.startswith("pyprobe"):
                out.add(module)
    return out


def _iter_modules():
    yield from sorted(PKG.rglob("*.py"))


def test_no_layer_imports_upwards():
    """A8 核心守护：层模块不得 import 更高层（kernel→…→cli 单向）——反向
    依赖会悄然腐蚀分层，code review 难以肉眼发现，必须机械钉死。"""
    violations = []
    for path in _iter_modules():
        rel = path.relative_to(PKG)
        if rel.name == "__init__.py" and len(rel.parts) == 1:
            continue  # package root: no layer
        src_rank = _rank(str(rel.with_suffix("")).replace("/", "."))
        if src_rank is None or src_rank < 0:
            continue
        for imp in _pyprobe_imports(path):
            imp_rank = _rank(imp.removeprefix("pyprobe."))
            if imp_rank is not None and imp_rank > src_rank:
                violations.append(f"{rel}: imports upward {imp}")
    assert violations == []


def test_cross_cutting_imports_no_layer():
    """A8 横切约束：errors/dto 不得 import 任何层模块——横切层一旦被层
    依赖反向咬住，"可被任意层引用"的前提即告破裂。"""
    violations = []
    for path in _iter_modules():
        rel = path.relative_to(PKG)
        name = str(rel.with_suffix("")).replace("/", ".")
        if _rank(name) != -1:
            continue
        for imp in _pyprobe_imports(path):
            imp_rank = _rank(imp.removeprefix("pyprobe."))
            if imp_rank is not None and imp_rank >= 0:
                violations.append(f"{rel}: cross-cutting imports layer {imp}")
    assert violations == []


def test_every_import_target_is_known():
    """Guard against typos creating phantom subpackages outside the rule."""
    violations = []
    for path in _iter_modules():
        for imp in _pyprobe_imports(path):
            top = imp.removeprefix("pyprobe").lstrip(".").split(".")[0]
            if top and top not in TOP_LEVELS:
                violations.append(f"{path.relative_to(PKG)}: unknown {imp}")
    assert violations == []


def test_layers_do_not_import_offsets_facade():
    """API3 守护：除包根 __init__.py（facade 的再出口点）外，任何模块
    不得 import pyprobe.offsets 兼容门面——新代码必须经 target/layout.py
    显式取 Layout，防全局单例借 facade 还魂（A1 的根治对象）。"""
    violations = []
    for path in _iter_modules():
        rel = path.relative_to(PKG)
        if rel.name == "__init__.py" and len(rel.parts) == 1:
            continue  # 包根：facade 的唯一合法再出口点
        for imp in _pyprobe_imports(path):
            if imp == "pyprobe.offsets" or imp.startswith("pyprobe.offsets."):
                violations.append(f"{rel}: imports offsets facade")
    assert violations == []
