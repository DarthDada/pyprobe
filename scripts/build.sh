#!/bin/bash
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

# The C reference Makefile runs from reference/c/, so any PYTHON path it receives
# must be absolute (or on PATH) — a relative .venv/bin/python3 would not resolve
# there. Prefer the uv-managed interpreter when present, else system python3.
if have_uv && [ -x .venv/bin/python3 ]; then
    PY="$(pwd)/.venv/bin/python3"
else
    PY=python3
fi

case "${1:-build}" in
    build)   make -C reference/c PYTHON="$PY" ;;
    clean)   make -C reference/c PYTHON="$PY" clean ;;
    rebuild) make -C reference/c PYTHON="$PY" clean && make -C reference/c PYTHON="$PY" ;;
    *)
        echo "usage: $0 [build|clean|rebuild]"
        exit 1
        ;;
esac
