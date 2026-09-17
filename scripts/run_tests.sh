#!/bin/bash
# Run pyprobe tests with sensible defaults.
# Usage:
#   scripts/run_tests.sh              # all tests (unit + integration)
#   scripts/run_tests.sh unit         # unit tests only (no child process)
#   scripts/run_tests.sh integration  # integration tests only
#   scripts/run_tests.sh -v           # verbose
#   scripts/run_tests.sh -- -x        # pass extra args to pytest after --
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

mode="all"
extra=()

while [ $# -gt 0 ]; do
    case "$1" in
        unit)        mode="unit"; shift ;;
        integration) mode="integration"; shift ;;
        --)          shift; extra+=("$@"); break ;;
        *)           extra+=("$1"); shift ;;
    esac
done

case "$mode" in
    all)         selector=() ;;
    unit)        selector=(-m "not integration") ;;
    integration) selector=(-m "integration" -v) ;;
esac

py_run -m pytest tests/ "${selector[@]}" "${extra[@]}"
