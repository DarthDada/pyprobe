#!/bin/bash
set -e
cd "$(dirname "$0")/.."

case "${1:-build}" in
    build)   make -C reference/c ;;
    clean)   make -C reference/c clean ;;
    rebuild) make -C reference/c clean && make -C reference/c ;;
    *)
        echo "usage: $0 [build|clean|rebuild]"
        exit 1
        ;;
esac
