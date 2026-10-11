"""Remote process memory transfer via process_vm_readv (ctypes).

Pure byte transfer — no caching, no consistency policy. Caching semantics
live one level up in ``kernel/views.py`` (ADR A4); this layer answers one
question: "give me bytes at this address, or tell me it failed".

契约（contracts.md 批 1 表 M1–M6）：
- 读循环直至填满；部分成功以剩余区间继续（M1）
- 任何 syscall 错误返回 None，不抛异常（M2）
- 累计 0 字节返回 None（M3）；短读如实返回（M4）
- EINTR（errno 4）重试（M5）；length==0 返回 b"" 且不发起 syscall（M6）

当前为骨架：``Transport.read`` 返回哨兵 None，批次 1 实现填实。
"""

import ctypes

_libc = ctypes.CDLL("libc.so.6", use_errno=True)


class _iovec(ctypes.Structure):
    _fields_ = [
        ("iov_base", ctypes.c_void_p),
        ("iov_len", ctypes.c_size_t),
    ]


_libc.process_vm_readv.restype = ctypes.c_ssize_t
_libc.process_vm_readv.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(_iovec),
    ctypes.c_ulong,
    ctypes.POINTER(_iovec),
    ctypes.c_ulong,
    ctypes.c_ulong,
]


class Transport:
    """Byte transfer for one target process.

    ``syscall`` is injectable for tests: a callable
    ``(pid, addr, length) -> bytes | None`` performing a single read attempt
    (short bytes = partial read, None = failure). The default performs one
    ``process_vm_readv`` call with EINTR retry.
    """

    def __init__(self, pid: int, syscall=None):
        self.pid = pid
        self._syscall = syscall
        self._local_iov = _iovec()
        self._remote_iov = _iovec()

    def read(self, addr: int, length: int) -> bytes | None:
        """Read ``length`` bytes at ``addr``. See module docstring M1–M6."""
        if length == 0:
            return b""  # M6: no syscall for an empty read
        syscall = self._syscall or self._read_once
        out = bytearray()
        while len(out) < length:
            chunk = syscall(self.pid, addr + len(out), length - len(out))
            if chunk is None:
                return None  # M2: any error fails the whole read
            if not chunk:
                break  # zero-read: the kernel has no more to give (M4)
            out += chunk  # M1: partial success resumes at the remainder
        if not out:
            return None  # M3: nothing read at all — distinct from b""
        return bytes(out)

    def _read_once(self, pid: int, addr: int, length: int) -> bytes | None:
        """One ``process_vm_readv`` attempt (default syscall seam).

        Short return = partial read, ``b""`` = zero read, None = failure;
        EINTR is retried inside the attempt (M5).
        """
        buf = (ctypes.c_char * length)()
        local = self._local_iov
        remote = self._remote_iov
        local.iov_base = ctypes.c_void_p(ctypes.addressof(buf))
        local.iov_len = length
        remote.iov_base = ctypes.c_void_p(addr)
        remote.iov_len = length
        # ctypes.pointer (not byref): the object must stay subscriptable for
        # scripted test doubles that write through local[0].iov_base.
        local_p = ctypes.pointer(local)
        remote_p = ctypes.pointer(remote)
        while True:
            n = _libc.process_vm_readv(pid, local_p, 1, remote_p, 1, 0)
            if n >= 0:
                return bytes(buf[:n])  # n == 0 -> b"" zero read
            if ctypes.get_errno() != 4:  # EINTR
                return None
