"""Remote process memory reading via process_vm_readv (ctypes)."""

import ctypes
import struct

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

PTR_FMT = "<Q"  # little-endian 64-bit
PTR_SIZE = struct.calcsize(PTR_FMT)
INT_FMT = "<i"
INT_SIZE = struct.calcsize(INT_FMT)

MAX_STR_LEN = 1 << 20


class RemoteReader:
    def __init__(self, pid):
        self.pid = pid

    def read(self, addr, length):
        if length == 0:
            return b""
        buf = (ctypes.c_char * length)()
        local = _iovec(ctypes.cast(buf, ctypes.c_void_p), length)
        remote = _iovec(ctypes.c_void_p(addr), length)
        total = 0
        while total < length:
            local.iov_base = ctypes.c_void_p(
                ctypes.addressof(buf) + total
            )
            local.iov_len = length - total
            remote.iov_base = ctypes.c_void_p(addr + total)
            remote.iov_len = length - total
            n = _libc.process_vm_readv(
                self.pid,
                ctypes.byref(local), 1,
                ctypes.byref(remote), 1,
                0,
            )
            if n < 0:
                err = ctypes.get_errno()
                if err == 4:  # EINTR
                    continue
                return None
            if n == 0:
                break
            total += n
        if total == 0:
            return None
        return bytes(buf[:total])

    def read_ptr(self, addr):
        data = self.read(addr, PTR_SIZE)
        if data is None or len(data) < PTR_SIZE:
            return None
        return struct.unpack(PTR_FMT, data)[0]

    def read_int(self, addr):
        data = self.read(addr, INT_SIZE)
        if data is None or len(data) < INT_SIZE:
            return None
        return struct.unpack(INT_FMT, data)[0]

    def read_u32(self, addr):
        data = self.read(addr, 4)
        if data is None or len(data) < 4:
            return None
        return struct.unpack("<I", data)[0]

    def read_u64(self, addr):
        data = self.read(addr, 8)
        if data is None or len(data) < 8:
            return None
        return struct.unpack("<Q", data)[0]
