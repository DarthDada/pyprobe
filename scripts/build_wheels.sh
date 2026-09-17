#!/bin/bash
set -e
cd "$(dirname "$0")/.."
source "$(dirname "$0")/_common.sh"

for arch in linux_x86_64 linux_aarch64; do
    build_wheel "$arch"
done
