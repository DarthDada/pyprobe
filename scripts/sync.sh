#!/bin/bash
# Install/sync project dependencies into the active environment.
#
# This is the scripted equivalent of the bare `uv sync` command, so that CI and
# local setups follow a single, consistent path. When `uv` is unavailable it
# falls back to installing the package (editable) plus pytest via pip.
#
# Usage:
#   scripts/sync.sh
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

if have_uv; then
    uv sync
else
    # No uv: [dependency-groups] is a uv/pip-PEP735 concept that plain pip does
    # not understand, so install the package itself plus the test runner.
    python3 -m pip install -e .
    python3 -m pip install pytest
fi
