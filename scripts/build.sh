#!/bin/bash
set -e
cd "$(dirname "$0")/.."

case "${1:-build}" in
    build)   make -C native/c ;;
    clean)   make -C native/c clean ;;
    rebuild) make -C native/c clean && make -C native/c ;;
    *)
        echo "usage: $0 [build|clean|rebuild]"
        exit 1
        ;;
esac
