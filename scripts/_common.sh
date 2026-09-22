# Shared helpers for pyprobe scripts.
#
# Source this file from a sibling script (after `cd` to the project root):
#
#   set -e
#   cd "$(dirname "$0")/.."
#   source "$(dirname "$0")/_common.sh"
#
# This file defines functions only; it must not execute commands on its own so
# that sourcing it has no side effects beyond defining the helpers.

# True if a `uv` executable is on PATH.
have_uv() { command -v uv >/dev/null 2>&1; }

# Run Python with the given args, preferring the uv-managed environment when
# available and falling back to the system `python3`.
py_run() {
    if have_uv; then
        uv run python "$@"
    else
        python3 "$@"
    fi
}

# Build a wheel for the given platform tag (e.g. linux_x86_64, linux_aarch64),
# or for the native platform when no tag is supplied. Extra args are forwarded
# to the underlying backend (uv build / pip wheel).
#
# The no-uv fallback mirrors the historical behaviour of build_wheels.sh: when
# setuptools is importable we skip build isolation (--no-build-isolation) for a
# faster build against the already-installed setuptools, otherwise we let pip
# create an isolated build environment.
build_wheel() {
    local plat="$1"
    [ $# -gt 0 ] && shift
    local uv_plat=() pip_plat=()
    if [ -n "$plat" ]; then
        uv_plat=(-C--build-option=--plat-name="$plat")
        pip_plat=(--config-settings=--build-option=--plat-name="$plat")
    fi
    # Purge stale setuptools intermediates (build/lib + build/bdist.*) so
    # source files removed since the last build don't leak into the wheel.
    # build/pyprobe (C reference binary, from scripts/build.sh) is untouched.
    rm -rf build/lib build/bdist.*
    if have_uv; then
        uv build --wheel "${uv_plat[@]}" "$@"
    elif python3 -c "import setuptools" >/dev/null 2>&1; then
        python3 -m pip wheel . --no-deps -w dist --no-build-isolation \
            "${pip_plat[@]}" "$@"
    else
        python3 -m pip wheel . --no-deps -w dist "${pip_plat[@]}" "$@"
    fi
}
