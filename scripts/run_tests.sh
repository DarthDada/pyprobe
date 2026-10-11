#!/bin/bash
# Run pyprobe tests with sensible defaults.
# Usage:
#   scripts/run_tests.sh                 # all tests (unit + integration)
#   scripts/run_tests.sh unit            # unit tests only (no child process)
#   scripts/run_tests.sh integration     # integration tests only
#   scripts/run_tests.sh watch           # re-run unit tests on every save (Ctrl-C to stop)
#   scripts/run_tests.sh watch all       # same, for the full suite
#   scripts/run_tests.sh --cov           # coverage report + fail-under baseline
#   scripts/run_tests.sh unit --cov      # combine mode with coverage
#   scripts/run_tests.sh -- -x           # pass extra args to pytest after --
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

mode="all"
watch=0
cov=0
extra=()

while [ $# -gt 0 ]; do
    case "$1" in
        unit)        mode="unit"; shift ;;
        integration) mode="integration"; shift ;;
        watch)       watch=1; shift
                     case "${1:-}" in
                         unit|integration|all) mode="$1"; shift ;;
                         *)                    mode="unit" ;;
                     esac ;;
        --cov)       cov=1; shift ;;
        --)          shift; extra+=("$@"); break ;;
        *)           extra+=("$1"); shift ;;
    esac
done

# Merge the mode marker with any `-m`/`--marker` passed after `--` into a single
# AND expression. pytest's last `-m` wins, so e.g. `run_tests.sh unit -- -m 'not
# slow'` would silently drop the unit selector; combining avoids the trap
# (contracts.md §10.2-7).
case "$mode" in
    all)         mode_expr="" ;;
    unit)        mode_expr="not integration" ;;
    integration) mode_expr="integration" ;;
esac
read_extra_expr=""
passthru=()
i=0
while [ "$i" -lt "${#extra[@]}" ]; do
    a="${extra[$i]}"
    case "$a" in
        -m|--marker)
            i=$((i + 1))
            read_extra_expr="${extra[$i]:-}" ;;
        -m=*|--marker=*)
            read_extra_expr="${a#*=}" ;;
        *)
            passthru+=("$a") ;;
    esac
    i=$((i + 1))
done
extra=("${passthru[@]}")

combined="$mode_expr"
if [ -n "$read_extra_expr" ]; then
    if [ -n "$combined" ]; then
        combined="$combined and $read_extra_expr"
    else
        combined="$read_extra_expr"
    fi
fi
selector=()
[ -n "$combined" ] && selector=(-m "$combined")
[ "$mode" = "integration" ] && selector+=(-v)

# Coverage baseline (ratchet): guard against coverage regressions. Raise it
# as coverage improves; never lower it without an explicit decision.
COV_FAIL_UNDER=79
cov_args=()
if [ "$cov" = 1 ]; then
    cov_args=(--cov=pyprobe --cov-report=term-missing "--cov-fail-under=$COV_FAIL_UNDER")
fi

if [ "$watch" = 1 ]; then
    py_run tools/watch_tests.py "${selector[@]}" "${cov_args[@]}" "${extra[@]}"
else
    py_run -m pytest tests/ "${selector[@]}" "${cov_args[@]}" "${extra[@]}"
fi
