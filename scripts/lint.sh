#!/bin/bash
# Lint the codebase with ruff (config in pyproject.toml [tool.ruff]).
#
# Runs `ruff check` over pyprobe/ and tests/. Fails fast on the first
# violation so CI stays green only when the tree is clean.
#
# Usage:
#   scripts/lint.sh            # check (fail on any violation)
#   scripts/lint.sh --fix      # auto-fix safe violations, then re-check
#   scripts/lint.sh format     # apply ruff format (one-off; not enforced in CI)
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

if [ "$1" = "format" ]; then
    py_run -m ruff format pyprobe/ tests/
    exit 0
fi

if [ "$1" = "--fix" ]; then
    py_run -m ruff check --fix pyprobe/ tests/
fi

py_run -m ruff check pyprobe/ tests/
