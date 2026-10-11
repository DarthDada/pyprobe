"""Exception hierarchy for pyprobe (cross-cutting layer, ADR A6 保留).

Library callers can catch ``PyProbeError`` to handle any pyprobe failure, or
catch a specific subclass to distinguish failure modes (process missing,
permission denied, symbol not found, version mismatch, etc.).

契约（contracts.md 批 1 表 E1/E2）：类名、属性、继承关系与旧树一致；
消息逐字复刻（CLI ``[!] {e}`` 用户可见，属复刻约束 §3）。
当前为骨架：属性与签名已锁定，消息文案为哨兵，批次 1 实现填实。
"""


class PyProbeError(Exception):
    """Base class for all pyprobe errors."""


class ProcessNotFound(PyProbeError):
    """Target process does not exist (``/proc/<pid>`` unreadable)."""

    def __init__(self, pid: int):
        super().__init__(f"Process {pid} not found")
        self.pid = pid


class PermissionDenied(PyProbeError):
    """Insufficient permissions to inspect the target process."""

    def __init__(self, pid: int):
        super().__init__(f"Permission denied reading process {pid}")
        self.pid = pid


class SymbolNotFound(PyProbeError):
    """A required ELF symbol was not found in the target executable."""

    def __init__(self, symbol: str, exe_path: str = ""):
        msg = f"cannot find symbol: {symbol}"
        if exe_path:
            msg += f" in {exe_path}"
        super().__init__(msg)
        self.symbol = symbol
        self.exe_path = exe_path


class NoInterpreterState(PyProbeError):
    """Failed to read the interpreter state from the target process."""

    def __init__(self, detail: str = "no interpreter state"):
        super().__init__(detail)


class NoThreadState(PyProbeError):
    """Failed to read the thread state list from the target process."""

    def __init__(self, detail: str = "failed to read threads.head"):
        super().__init__(detail)


class VersionNotSupported(PyProbeError):
    """CPython version is not in the verified offsets table.

    pyprobe falls back to a default version, but results may be incorrect.
    """

    def __init__(self, version: str, fallback: str):
        super().__init__(
            f"CPython {version} is not a verified version; "
            f"falling back to {fallback} offsets (output may be incorrect)")
        self.version = version
        self.fallback = fallback


class AttachFailed(PyProbeError):
    """ptrace attach failed."""

    def __init__(self, pid: int, detail: str = ""):
        msg = f"ptrace attach failed for process {pid}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
        self.pid = pid


class UnsupportedArchitecture(PyProbeError):
    """The current CPU architecture is not supported by this feature.

    ptrace register layouts and syscall numbers are architecture-specific;
    features like syscall tracing are only implemented on x86-64.
    """

    def __init__(self, feature: str, arch: str, supported: str = "x86-64"):
        super().__init__(
            f"{feature} is not supported on {arch} "
            f"(supported: {supported})")
        self.feature = feature
        self.arch = arch
        self.supported = supported


class ProcessExited(PyProbeError):
    """Target process exited while pyprobe was sampling it."""

    def __init__(self, pid: int):
        super().__init__(f"Process {pid} exited during sampling")
        self.pid = pid
