"""Structural contract tests (contracts.md §1, ADR A8).

Mechanically enforce the dependency rule
``kernel → target → cpython → observe → present → cli`` by scanning imports
in pyprobe with ast (no imports of the modules under test, so the
guard also works when a layer is broken mid-development).

Rules:
* a layer module may import cross-cutting modules (errors, dto) and modules
  of its own or lower layers — never a higher layer;
* cross-cutting modules must not import any layer module;
* cli (pyprobe.cli / pyprobe.__main__) may import everything.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "pyprobe"

MAP_PACKAGES = ["kernel", "target", "cpython", "observe", "present"]
MAP_FOOTER_RE = re.compile(r"以代码为准，验证于 \d{4}-\d{2}-\d{2}")

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


def _map_section():
    """design.md §2 架构地图正文（标题到下一个 §）。"""
    text = (REPO / "docs" / "design.md").read_text(encoding="utf-8")
    return text.split("\n## 2. ", 1)[1].split("\n## ", 1)[0]


def _map_rows():
    """地图表的数据行（跳过表头与分隔行），每行返回单元格列表。"""
    rows = []
    for line in _map_section().splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or set(cells[0]) <= set("-: ") or cells[0] == "路径":
            continue
        rows.append(cells)
    return rows


def test_architecture_map_matches_filesystem():
    """A14 守护（contracts.md）：design.md §2 架构地图必须与文件系统一致——
    未落地结构画进现状地图曾误导定位（A14 决策背景）；地图只写已落地
    结构，「入口」列指向真实文件与符号，函数级细节由代码提供。"""
    rows = _map_rows()
    assert rows, "design.md §2 架构地图缺失（四字段表）"

    joined_paths = " ".join(r[0] for r in rows)
    for pkg in MAP_PACKAGES:
        assert f"pyprobe/{pkg}/" in joined_paths, f"地图缺包：pyprobe/{pkg}/"

    for path_cell, _duty, entry_cell, _ban in rows:
        paths = re.findall(r"`([^`]+)`", path_cell) or [path_cell]
        for path in paths:
            assert (REPO / path).exists(), f"地图条目指向不存在的路径：{path}"
        for file_rel, symbol in re.findall(r"`([\w./-]+\.py):(\w+)`", entry_cell):
            base = REPO / paths[0]
            base_dir = base if base.is_dir() else base.parent
            candidates = [base_dir / file_rel, REPO / file_rel]
            target = next((c for c in candidates if c.is_file()), None)
            assert target is not None, f"入口文件不存在：{file_rel}"
            assert symbol in target.read_text(encoding="utf-8"), (
                f"入口符号不存在：{file_rel}:{symbol}")

    assert MAP_FOOTER_RE.search(_map_section()), (
        "架构地图缺末行「以代码为准，验证于 YYYY-MM-DD」")
