#!/bin/bash
# CI/CD orchestrator: run pipeline stages by delegating to the individual
# scripts under scripts/, so that every key command has a single, scripted
# source of truth and CI never hand-types a command.
#
# Stages:
#   sync          install/sync dependencies          (scripts/sync.sh)
#   gen-offsets   regenerate pyprobe/offsets.json     (scripts/gen_offsets.sh)
#   build-c       build the C reference implementation (scripts/build.sh)
#   test          run the test suite + coverage baseline   (scripts/run_tests.sh)
#   smoke         build wheel + import/CLI smoke test (scripts/smoke.sh)
#
# Usage:
#   scripts/ci.sh                       default pipeline (sync gen-offsets test smoke)
#   scripts/ci.sh all                   same as the default pipeline
#   scripts/ci.sh full                   default + build-c (needs libdw/libelf/zlib)
#   scripts/ci.sh sync                   a single stage
#   scripts/ci.sh gen-offsets test       selected stages, in the given order
set -e
cd "$(dirname "$0")/.."

run_stage() {
    case "$1" in
        sync)        echo "==> [sync]";          scripts/sync.sh ;;
        gen-offsets) echo "==> [gen-offsets]";   scripts/gen_offsets.sh ;;
        build-c)     echo "==> [build-c]";       scripts/build.sh ;;
        test)        echo "==> [test]";          scripts/run_tests.sh --cov ;;
        smoke)       echo "==> [smoke]";         scripts/smoke.sh ;;
        *) echo "unknown stage: $1 (valid: sync gen-offsets build-c test smoke all full)" >&2; exit 2 ;;
    esac
}

stages=("$@")
if [ $# -eq 0 ] || [ "$1" = "all" ]; then
    stages=(sync gen-offsets test smoke)
elif [ "$1" = "full" ]; then
    stages=(sync gen-offsets build-c test smoke)
fi

for stage in "${stages[@]}"; do
    run_stage "$stage"
done

echo "==> ci OK: ${stages[*]}"
