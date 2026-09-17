#!/bin/bash
# Build a single wheel for the native platform.
#
# This is the scripted equivalent of the bare `uv build --wheel` command, used
# by the smoke stage and any local one-off native build. For the dual-arch
# release wheels use scripts/build_wheels.sh instead.
#
# Usage:
#   scripts/build_wheel.sh
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

build_wheel

echo "wheel(s) in dist/:"
ls dist/*.whl
