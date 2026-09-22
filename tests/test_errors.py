"""Unit tests for pyprobe.errors — exception hierarchy."""

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
    VersionNotSupported,
)

SUBCLASSES = [
    ProcessNotFound, PermissionDenied, SymbolNotFound,
    NoInterpreterState, NoThreadState, VersionNotSupported, AttachFailed,
    ProcessExited,
]


@pytest.mark.parametrize("exc_cls", SUBCLASSES)
def test_subclass_of_pyprobe_error(exc_cls):
    assert issubclass(exc_cls, PyProbeError)


def test_base_is_exception():
    assert issubclass(PyProbeError, Exception)


def test_process_not_found_attributes():
    e = ProcessNotFound(123)
    assert e.pid == 123
    assert "123" in str(e)


def test_permission_denied_attributes():
    e = PermissionDenied(456)
    assert e.pid == 456
    assert "456" in str(e)


def test_symbol_not_found_attributes():
    e = SymbolNotFound("_PyRuntime", "/usr/bin/python3.12")
    assert e.symbol == "_PyRuntime"
    assert e.exe_path == "/usr/bin/python3.12"
    assert "_PyRuntime" in str(e)


def test_symbol_not_found_no_path():
    e = SymbolNotFound("foo")
    assert e.exe_path == ""
    assert "foo" in str(e)


def test_version_not_supported_attributes():
    e = VersionNotSupported("3.9", "3.12")
    assert e.version == "3.9"
    assert e.fallback == "3.12"
    assert "3.9" in str(e)


def test_attach_failed_attributes():
    e = AttachFailed(789, "no such process")
    assert e.pid == 789
    assert "789" in str(e)


def test_process_exited_attributes():
    e = ProcessExited(321)
    assert e.pid == 321
    assert "321" in str(e)


def test_can_catch_all_as_base():
    for exc_cls in SUBCLASSES:
        with pytest.raises(PyProbeError):
            raise exc_cls.__new__(exc_cls)


class TestUnsupportedArchitecture:
    def test_subclass_of_pyprobe_error(self):
        from pyprobe.errors import UnsupportedArchitecture
        assert issubclass(UnsupportedArchitecture, PyProbeError)

    def test_message_and_attributes(self):
        from pyprobe.errors import UnsupportedArchitecture
        e = UnsupportedArchitecture("syscall tracing", "aarch64")
        assert e.feature == "syscall tracing"
        assert e.arch == "aarch64"
        assert e.supported == "x86-64"
        assert "syscall tracing" in str(e)
        assert "aarch64" in str(e)
        assert "x86-64" in str(e)
