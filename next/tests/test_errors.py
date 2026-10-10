"""Contract tests for pyprobe.errors (contracts.md 批 1 表 E1/E2).

Messages are pinned verbatim — they surface to users via the CLI ``[!] {e}``
path, so they are 复刻约束 (contracts.md §3). Authoritative source: old
``pyprobe/errors.py`` (frozen tree), cited per 测试数据纪律.
"""

import pytest

from pyprobe.errors import (
    AttachFailed,
    NoInterpreterState,
    NoThreadState,
    PermissionDenied,
    ProcessExited,
    ProcessNotFound,
    PyProbeError,
    SymbolNotFound,
    UnsupportedArchitecture,
    VersionNotSupported,
)

ALL_SUBCLASSES = [
    ProcessNotFound,
    PermissionDenied,
    SymbolNotFound,
    NoInterpreterState,
    NoThreadState,
    VersionNotSupported,
    AttachFailed,
    UnsupportedArchitecture,
    ProcessExited,
]


def test_base_is_exception():
    assert issubclass(PyProbeError, Exception)


@pytest.mark.parametrize("cls", ALL_SUBCLASSES)
def test_subclass_of_pyprobe_error(cls):
    assert issubclass(cls, PyProbeError)


def test_process_not_found():
    e = ProcessNotFound(1234)
    assert e.pid == 1234
    assert str(e) == "Process 1234 not found"


def test_permission_denied():
    e = PermissionDenied(1234)
    assert e.pid == 1234
    assert str(e) == "Permission denied reading process 1234"


def test_symbol_not_found_with_path():
    e = SymbolNotFound("_PyRuntime", "/usr/bin/python3")
    assert e.symbol == "_PyRuntime"
    assert e.exe_path == "/usr/bin/python3"
    assert str(e) == "cannot find symbol: _PyRuntime in /usr/bin/python3"


def test_symbol_not_found_without_path_omits_in():
    e = SymbolNotFound("_PyRuntime")
    assert e.exe_path == ""
    assert str(e) == "cannot find symbol: _PyRuntime"


def test_no_interpreter_state_default_detail():
    assert str(NoInterpreterState()) == "no interpreter state"
    assert str(NoInterpreterState("custom")) == "custom"


def test_no_thread_state_default_detail():
    assert str(NoThreadState()) == "failed to read threads.head"


def test_version_not_supported():
    e = VersionNotSupported("3.15.0", "3.13")
    assert e.version == "3.15.0"
    assert e.fallback == "3.13"
    assert str(e) == (
        "CPython 3.15.0 is not a verified version; "
        "falling back to 3.13 offsets (output may be incorrect)"
    )


def test_attach_failed_with_detail():
    e = AttachFailed(1234, "Operation not permitted")
    assert e.pid == 1234
    assert str(e) == "ptrace attach failed for process 1234: Operation not permitted"


def test_attach_failed_without_detail_omits_colon():
    assert str(AttachFailed(1234)) == "ptrace attach failed for process 1234"


def test_unsupported_architecture():
    e = UnsupportedArchitecture("syscall tracing", "aarch64")
    assert e.feature == "syscall tracing"
    assert e.arch == "aarch64"
    assert e.supported == "x86-64"
    assert str(e) == (
        "syscall tracing is not supported on aarch64 (supported: x86-64)"
    )


def test_process_exited():
    e = ProcessExited(1234)
    assert e.pid == 1234
    assert str(e) == "Process 1234 exited during sampling"


def test_catch_all_as_base():
    for cls in ALL_SUBCLASSES:
        try:
            raise cls(*([0] if cls in (ProcessNotFound, PermissionDenied,
                                       AttachFailed, ProcessExited) else
                        ["x", "y"] if cls is VersionNotSupported else
                        ["f", "a", "s"] if cls is UnsupportedArchitecture else
                        ["s"]))
        except PyProbeError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError(f"{cls.__name__} not caught as PyProbeError")
