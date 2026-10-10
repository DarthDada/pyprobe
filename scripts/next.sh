#!/bin/bash
# Drive the next/ rebuild tree (TODO §10) without touching the frozen legacy
# tree. Mirror of scripts/run_tests.sh for the new architecture: same modes,
# same no-bare-command rule — during the rebuild all next-side work goes
# through this script.
#
# Isolation model: both trees ship a package named `pyprobe`. This script runs
# pytest with cwd=next/ but the *root* project environment (uv run --project),
# so the two trees share one venv while imports resolve to next/pyprobe via
# cwd + `pythonpath = ["."]` in next/pyproject.toml. Old↔new behaviour
# comparison (§10.5-1) always goes through CLI subprocesses, never imports.
#
# Usage:
#   scripts/next.sh test                 # all next tests (unit + integration)
#   scripts/next.sh test unit            # unit tests only (no child process)
#   scripts/next.sh test integration     # integration tests only
#   scripts/next.sh test --cov           # coverage report (ratchet below)
#   scripts/next.sh test unit -- --x     # extra pytest args after --
#   scripts/next.sh lint [--fix]         # ruff check next/ only
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

# Run Python with cwd=next/ and the root project environment.
py_run_next() {
    if have_uv; then
        (cd next && uv run --project .. python "$@")
    else
        (cd next && python3 "$@")
    fi
}

cmd="${1:-test}"
[ $# -gt 0 ] && shift

case "$cmd" in
    test)
        mode="all"
        cov=0
        extra=()
        while [ $# -gt 0 ]; do
            case "$1" in
                unit)        mode="unit"; shift ;;
                integration) mode="integration"; shift ;;
                --cov)       cov=1; shift ;;
                --)          shift; extra+=("$@"); break ;;
                *)           extra+=("$1"); shift ;;
            esac
        done
        case "$mode" in
            all)         selector=() ;;
            unit)        selector=(-m "not integration") ;;
            integration) selector=(-m "integration" -v) ;;
        esac
        # Coverage ratchet for the next tree: batch 0 started at 0, batch 1
        # reached 96% — ratchet straight to the §10.5-3 exit-gate value.
        # Never lower it without an explicit decision.
        COV_FAIL_UNDER=80
        cov_args=()
        if [ "$cov" = 1 ]; then
            cov_args=(--cov=pyprobe --cov-report=term-missing "--cov-fail-under=$COV_FAIL_UNDER")
        fi
        py_run_next -m pytest tests/ "${selector[@]}" "${cov_args[@]}" "${extra[@]}"
        ;;
    lint)
        if [ "${1:-}" = "--fix" ]; then
            py_run -m ruff check --fix next/
        fi
        py_run -m ruff check next/
        ;;
    *)
        echo "unknown command: $cmd (valid: test lint)" >&2
        exit 2
        ;;
esac
