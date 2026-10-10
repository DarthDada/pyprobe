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
    """E1: PyProbeError must stay an Exception subclass — CLI/库调用方按
    标准异常语义捕获，退化（如改继承 BaseException）会破坏 except Exception 网。"""
    assert issubclass(PyProbeError, Exception)


@pytest.mark.parametrize("cls", ALL_SUBCLASSES)
def test_subclass_of_pyprobe_error(cls):
    """E1: 全部子类挂在 PyProbeError 下——库 API 承诺"catch PyProbeError 即
    捕获所有 pyprobe 失败"，漏挂一个子类就破坏该承诺。"""
    assert issubclass(cls, PyProbeError)


def test_process_not_found():
    """E2: pin 属性与消息（CLI `[!]` 输出，复刻约束）。"""
    e = ProcessNotFound(1234)
    assert e.pid == 1234
    assert str(e) == "Process 1234 not found"


def test_permission_denied():
    """E2: pin 属性与消息。"""
    e = PermissionDenied(1234)
    assert e.pid == 1234
    assert str(e) == "Permission denied reading process 1234"


def test_symbol_not_found_with_path():
    """E2: 带 exe_path 时消息追加 " in <path>"（定位符号来源二进制的诊断信息）。"""
    e = SymbolNotFound("_PyRuntime", "/usr/bin/python3")
    assert e.symbol == "_PyRuntime"
    assert e.exe_path == "/usr/bin/python3"
    assert str(e) == "cannot find symbol: _PyRuntime in /usr/bin/python3"


def test_symbol_not_found_without_path_omits_in():
    """E2: 无 exe_path 时消息省略 " in"——防格式化分支回归出裸 "in" 尾巴。"""
    e = SymbolNotFound("_PyRuntime")
    assert e.exe_path == ""
    assert str(e) == "cannot find symbol: _PyRuntime"


def test_no_interpreter_state_default_detail():
    """E2: 默认 detail 文案与自定义覆盖（默认文案是复刻约束）。"""
    assert str(NoInterpreterState()) == "no interpreter state"
    assert str(NoInterpreterState("custom")) == "custom"


def test_no_thread_state_default_detail():
    """E2: 默认 detail 文案（指向 threads.head 读取失败的诊断语义）。"""
    assert str(NoThreadState()) == "failed to read threads.head"


def test_version_not_supported():
    """E2: pin 属性与消息（未验证版本回退告警，用户可见）。"""
    e = VersionNotSupported("3.15.0", "3.13")
    assert e.version == "3.15.0"
    assert e.fallback == "3.13"
    assert str(e) == (
        "CPython 3.15.0 is not a verified version; "
        "falling back to 3.13 offsets (output may be incorrect)"
    )


def test_attach_failed_with_detail():
    """E2: 带 detail 时消息追加 ": <detail>"（strerror 诊断，复刻约束）。"""
    e = AttachFailed(1234, "Operation not permitted")
    assert e.pid == 1234
    assert str(e) == "ptrace attach failed for process 1234: Operation not permitted"


def test_attach_failed_without_detail_omits_colon():
    """E2: 无 detail 时消息省略冒号——防格式化分支回归出悬空虚冒号。"""
    assert str(AttachFailed(1234)) == "ptrace attach failed for process 1234"


def test_unsupported_architecture():
    """E2: pin 三属性与消息（arch 检查下沉引擎后，该错误是 aarch64 用户的
    唯一承诺输出——契约 T16/A7 P0-2 的用户可见面）。"""
    e = UnsupportedArchitecture("syscall tracing", "aarch64")
    assert e.feature == "syscall tracing"
    assert e.arch == "aarch64"
    assert e.supported == "x86-64"
    assert str(e) == (
        "syscall tracing is not supported on aarch64 (supported: x86-64)"
    )


def test_process_exited():
    """E2: pin 属性与消息。"""
    e = ProcessExited(1234)
    assert e.pid == 1234
    assert str(e) == "Process 1234 exited during sampling"


def test_catch_all_as_base():
    """E1 实例级验证：每个子类的实例都能被 except PyProbeError 捕获——
    类关系（test_subclass_of_pyprobe_error）之外的运行时兜底。"""
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
