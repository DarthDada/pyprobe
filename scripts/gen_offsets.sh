#!/bin/bash
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

# A real PYTHON binary path may be supplied via the environment to override the
# detected one; otherwise derive the include dir through the common runner.
if [ -n "${PYTHON:-}" ]; then
    PYTHON_INCLUDE=$("$PYTHON" -c "import sysconfig; print(sysconfig.get_path('include'))")
else
    PYTHON_INCLUDE=$(py_run -c "import sysconfig; print(sysconfig.get_path('include'))")
fi

echo "Generating offsets using $PYTHON_INCLUDE..."
cc -O2 -I"$PYTHON_INCLUDE" -DPy_BUILD_CORE -DPy_BUILD_CORE_BUILTIN \
    -o /tmp/gen_offsets tools/gen_offsets.c
/tmp/gen_offsets > pyprobe/offsets.json
echo "Written to pyprobe/offsets.json"
