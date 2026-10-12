#!/bin/bash
# Install/sync project dependencies into the active environment.
#
# This is the scripted equivalent of the bare `uv sync` command, so that CI and
# local setups follow a single, consistent path. Web extras (ADR A13) are
# included: the uv path passes --all-extras, the pip fallback installs the
# package as `.[web]` directly. When `uv` is unavailable the fallback also
# installs the dev tools (pytest, pytest-cov, watchfiles, ruff, httpx2 — ruff
# is required by scripts/lint.sh, which runs `python -m ruff` via the same
# fallback; httpx2 backs the web TestClient suite).
#
# Usage:
#   scripts/sync.sh
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

if have_uv; then
    uv sync --all-extras
else
    # No uv: [dependency-groups] is a uv/pip-PEP735 concept that plain pip does
    # not understand, so install the package itself (with web extras) plus the
    # dev tools.
    python3 -m pip install -e ".[web]"
    python3 -m pip install pytest pytest-cov watchfiles ruff httpx2
fi
