#!/usr/bin/env python3
"""Documentation contract checker (contracts.md ADR A14).

Doc rules used to live in prose ("改 X 时同步 Y") and drifted: dangling
references (AGENTS.md -> a removed TODO section; the §6 dispatch template ->
the deleted scripts/next.sh) and duplicated-then-divergent signature tables.
This checker lints the docs so those rules are mechanically enforced:

* DOC1  relative markdown links, including ``#anchors``, must resolve;
* DOC2  ``scripts/*.sh`` paths referenced from docs must exist on disk;
* DOC3  no bare ``TODO §<n>`` references (section numbers renumber; a
      historical reference must be written ``原 TODO §<n>``); scanned
      across md + py/sh/yml, with docs/red evidence archives exempt;
* DOC4  resident-doc budget ratchet (AGENTS.md / TODO.md / the design.md §2
      architecture map) — line counts may only shrink, never grow;
* DOC5  open TODO items (``- [ ]``) must carry a ``（YYYY-MM`` date anchor;
* DOC6  contract item ids back-referenced from tests/pyprobe (T12, NA11, WM1,
      ST-W1, …) must be defined as table rows in contracts.md.

Stdlib only. Prints every violation and exits non-zero when any is found.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "pyprobe"

# DOC4 ratchet baselines (line-count caps; only lower them, never raise).
# Raising a cap requires deleting equivalent content in the same change
# (ADR A14: residency budget — every line dilutes instruction-following).
# The design.md §2 map is resident too (Kiro structure.md / MEMO budget).
MAP_KEY = "design.md §2 架构地图"
BUDGET = {"AGENTS.md": 65, "TODO.md": 66, MAP_KEY: 23}

DOC_FILES = [REPO / name for name in ("README.md", "AGENTS.md", "TODO.md")]
DOC_FILES += sorted((REPO / "docs").rglob("*.md"))

# DOC3 scans code too: stale TODO-section refs lived in CI/hooks/scripts/tests
# (ADR A14). docs/red holds frozen evidence archives — exempt by design.
DOC3_SUFFIXES = {".md", ".py", ".sh", ".yml"}
DOC3_SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist",
                ".ruff_cache", ".pytest_cache", "pyprobe.egg-info"}
DOC3_SKIP_PREFIXES = ("docs/red/",)

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
SCHEME_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*:")
SCRIPT_RE = re.compile(r"scripts/[\w.-]+\.sh")
TODO_SECTION_RE = re.compile(r"(?<!原 )TODO(?:\.md)?\s*§\s*\d")
OPEN_ITEM_RE = re.compile(r"^\s*- \[ \]")
DATE_RE = re.compile(r"（\d{4}-\d{2}")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$")
CONTRACT_ROW_RE = re.compile(r"^\| ([A-Za-z][A-Za-z0-9-]*) \|")
# Prefix is letters/hyphens only: keeps ruff codes (F401) out of DOC6.
BACKLINK_RE = re.compile(
    r"(?<![A-Za-z0-9-])([A-Za-z][A-Za-z-]*\d{1,2})(?![A-Za-z0-9-])")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def _anchor(text: str) -> str:
    """GitHub-style heading anchor (lowercase, strip punctuation, spaces→-)."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    return {_anchor(m.group(1))
            for line in path.read_text(encoding="utf-8").splitlines()
            if (m := HEADING_RE.match(line))}


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def check_doc1_links(path: Path) -> list[str]:
    """D1: every relative markdown link (and its anchor) resolves."""
    out: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    for lineno, line in enumerate(_lines(path), 1):
        for m in LINK_RE.finditer(line):
            target = m.group(1)
            if SCHEME_RE.match(target):  # http(s):, mailto:, ...
                continue
            file_part, _, anchor = target.partition("#")
            dest = path if not file_part else (path.parent / file_part).resolve()
            if not dest.exists():
                out.append(f"{_rel(path)}:{lineno}: [DOC1] 死链接 -> {target}")
                continue
            if anchor and dest.suffix == ".md":
                if dest not in anchor_cache:
                    anchor_cache[dest] = _anchors(dest)
                if anchor not in anchor_cache[dest]:
                    out.append(f"{_rel(path)}:{lineno}: [DOC1] 锚点不存在 -> {target}")
    return out


def check_doc2_script_refs(path: Path) -> list[str]:
    """D2: scripts/*.sh mentioned in a doc must exist."""
    out = []
    for lineno, line in enumerate(_lines(path), 1):
        for m in SCRIPT_RE.finditer(line):
            if not (REPO / m.group(0)).is_file():
                out.append(f"{_rel(path)}:{lineno}: [DOC2] 不存在的脚本 {m.group(0)}")
    return out


def check_doc3_todo_refs(path: Path) -> list[str]:
    """D3: forbid bare `TODO §<n>` references (section numbers renumber)."""
    out = []
    for lineno, line in enumerate(_lines(path), 1):
        if TODO_SECTION_RE.search(line):
            out.append(
                f"{_rel(path)}:{lineno}: [DOC3] 禁止裸章节号引用 TODO"
                "（编号会漂移；历史指称写作「原 TODO §<n>」）")
    return out


def _doc3_files() -> list[Path]:
    """Files scanned for D3 (TODO section refs): md + py/sh/yml, no archives."""
    out = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in DOC3_SUFFIXES:
            continue
        rel = path.relative_to(REPO)
        if any(part in DOC3_SKIP_DIRS for part in rel.parts):
            continue
        if str(rel).startswith(DOC3_SKIP_PREFIXES):
            continue
        out.append(path)
    return sorted(out)


def _design_map_lines() -> int:
    """Line count of the design.md §2 architecture-map section."""
    text = (REPO / "docs" / "design.md").read_text(encoding="utf-8")
    section = text.split("\n## 2. ", 1)[1].split("\n## ", 1)[0]
    return len(section.splitlines())


def check_doc4_budget() -> list[str]:
    """D4: resident-doc budget ratchet (only shrink, never grow)."""
    counts = {
        "AGENTS.md": len(_lines(REPO / "AGENTS.md")),
        "TODO.md": len(_lines(REPO / "TODO.md")),
        MAP_KEY: _design_map_lines(),
    }
    out = []
    for name, cap in BUDGET.items():
        if counts[name] > cap:
            out.append(
                f"{name}: [DOC4] {counts[name]} 行 > 棘轮基线 {cap} 行（只降不升）")
    return out


def defined_contract_ids() -> set[str]:
    """D6 source of truth: ids defined as table-row first cells in contracts."""
    ids = set()
    for line in _lines(REPO / "docs" / "contracts.md"):
        row = CONTRACT_ROW_RE.match(line)
        name = row.group(1) if row else ""
        if name and re.search(r"\d", name) and len(name) <= 8:
            ids.add(name)
    return ids


def check_doc6_backlinks(path: Path, ids: set[str],
                       prefixes: set[str]) -> list[str]:
    """D6: contract ids back-referenced from code must exist in contracts."""
    out = []
    for lineno, line in enumerate(_lines(path), 1):
        for m in BACKLINK_RE.finditer(line):
            token = m.group(1)
            if re.sub(r"\d+$", "", token) in prefixes and token not in ids:
                out.append(
                    f"{_rel(path)}:{lineno}: [DOC6] 契约编号 {token} "
                    "未在 contracts.md 定义")
    return out


def check_doc5_todo_dates(path: Path) -> list[str]:
    """D5: open TODO items carry a （YYYY-MM) date anchor."""
    out = []
    for lineno, line in enumerate(_lines(path), 1):
        if OPEN_ITEM_RE.match(line) and not DATE_RE.search(line):
            out.append(
                f"{_rel(path)}:{lineno}: [DOC5] 未勾条目缺日期锚（需「（YYYY-MM」）")
    return out


def main() -> int:
    violations: list[str] = []
    for path in DOC_FILES:
        violations += check_doc1_links(path)
        violations += check_doc2_script_refs(path)
        if path.name == "TODO.md":
            violations += check_doc5_todo_dates(path)
    for path in _doc3_files():
        violations += check_doc3_todo_refs(path)
    violations += check_doc4_budget()
    ids = defined_contract_ids()
    prefixes = {re.sub(r"\d+$", "", i) for i in ids}
    for path in sorted(REPO.glob("tests/*.py")) + sorted(PKG.glob("**/*.py")):
        violations += check_doc6_backlinks(path, ids, prefixes)
    for violation in violations:
        print(violation)
    if violations:
        print(f"\n[!] check_docs: {len(violations)} violation(s)")
        return 1
    print(f"check_docs: OK ({len(DOC_FILES)} docs, DOC1–DOC6)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
