#!/bin/bash
# Lint the codebase with ruff (config in pyproject.toml [tool.ruff]) and check
# the documentation contract (scripts/check_docs.sh).
#
# Runs `ruff check` over pyprobe/ and tests/, then the docs checker (DOC1/2/3/
# 5/6: links/anchors, script refs, TODO section refs, TODO dates, contract-id
# backlinks). Fails fast on the first violation so CI stays green only when the
# tree is clean.
#
# Usage:
#   scripts/lint.sh            # check (fail on any violation)
#   scripts/lint.sh --fix      # auto-fix safe violations, then re-check
#   scripts/lint.sh format     # apply ruff format (one-off; not enforced in CI)
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

targets=(pyprobe/ tests/)

if [ "$1" = "format" ]; then
    py_run -m ruff format "${targets[@]}"
    exit 0
fi

if [ "$1" = "--fix" ]; then
    py_run -m ruff check --fix "${targets[@]}"
fi

py_run -m ruff check "${targets[@]}"

scripts/check_docs.sh
