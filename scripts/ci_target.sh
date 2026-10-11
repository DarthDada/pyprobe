#!/bin/bash
# CI cross-version stage (TODO §6-1): run the integration suite against a
# matrix TARGET_PYTHON (3.11–3.14). pyprobe itself stays on the pinned
# interpreter (.python-version); only the inspected target varies, so the
# suite validates cross-version end-to-end (the conftest TARGET_PYTHON
# mechanism, previously idle in CI). No bare build/test commands.
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

version="${1:?usage: ci_target.sh <x.y>}"

if command -v uv >/dev/null 2>&1; then
    uv python install "$version" >/dev/null
    target="$(uv python find "$version")"
else
    target="$(command -v "python$version")" || {
        echo "python$version not found and uv unavailable" >&2
        exit 2
    }
fi

echo "Cross-version integration: TARGET_PYTHON=$target"
scripts/sync.sh
TARGET_PYTHON="$target" scripts/run_tests.sh integration
