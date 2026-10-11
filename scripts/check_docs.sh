#!/bin/bash
# Documentation contract checker (contracts.md ADR A14).
#
# Enforces the doc rules mechanically (D1–D5; see scripts/check_docs.py).
# Wired into scripts/lint.sh so both CI and the pre-commit hook gate on it.
#
# Usage:
#   scripts/check_docs.sh
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

py_run scripts/check_docs.py
