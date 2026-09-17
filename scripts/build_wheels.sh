#!/bin/bash
set -e
cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1; then
    build() { uv build --wheel -C--build-option=--plat-name="$1"; }
elif command -v python3 >/dev/null 2>&1; then
    if python3 -c "import setuptools" >/dev/null 2>&1; then
        build() { python3 -m pip wheel . --no-deps -w dist --no-build-isolation --config-settings=--build-option=--plat-name="$1"; }
    else
        build() { python3 -m pip wheel . --no-deps -w dist --config-settings=--build-option=--plat-name="$1"; }
    fi
else
    echo "error: need uv or python3" >&2
    exit 1
fi

for arch in linux_x86_64 linux_aarch64; do
    build "$arch"
done
