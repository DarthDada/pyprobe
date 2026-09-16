#!/bin/bash
set -e
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-.venv/bin/python3}
PYTHON_INCLUDE=$($PYTHON -c "import sysconfig; print(sysconfig.get_path('include'))")

echo "Generating offsets using $PYTHON_INCLUDE..."
cc -O2 -I"$PYTHON_INCLUDE" -DPy_BUILD_CORE -DPy_BUILD_CORE_BUILTIN \
    -o /tmp/gen_offsets native/c/gen_offsets.c
/tmp/gen_offsets > pyprobe/offsets.json
echo "Written to pyprobe/offsets.json"
