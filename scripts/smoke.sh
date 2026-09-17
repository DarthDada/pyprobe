#!/bin/bash
# Wheel smoke test: build the native wheel, install it into a throwaway venv
# independent of the project environment, and verify that the package imports,
# the packaged offsets.json is present, and the `pyprobe` console script runs.
#
# This catches packaging regressions (missing package-data, broken console
# script, unimportable modules) that unit tests run against the source tree
# would miss.
#
# Usage:
#   scripts/smoke.sh
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

echo "== [smoke] building native wheel =="
rm -f dist/*.whl
build_wheel
WHEEL=$(ls dist/*.whl | head -n1)
echo "wheel: $WHEEL"

echo "== [smoke] installing into throwaway venv =="
VENV=$(mktemp -d)
trap 'rm -rf "$VENV"' EXIT
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet "$WHEEL"

echo "== [smoke] verifying import + offsets.json + CLI =="
"$VENV/bin/python" - <<'PY'
import os
import pyprobe
import pyprobe.offsets as offsets
assert pyprobe.__version__, "no __version__"
offsets_path = os.path.join(os.path.dirname(offsets.__file__), "offsets.json")
assert os.path.exists(offsets_path), "offsets.json missing from wheel"
print("import OK, version", pyprobe.__version__)
PY
"$VENV/bin/pyprobe" --version
"$VENV/bin/pyprobe" --help >/dev/null

echo "smoke OK: $WHEEL"
