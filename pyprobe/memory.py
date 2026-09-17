"""Remote process memory reading via process_vm_readv (ctypes).

Page-level LRU cache: stack walking exhibits high spatial locality
(frame chains, adjacent CodeObject fields, contiguous dict entries,
per-key unicode bodies). Caching whole pages collapses many small
4-8 byte reads into a single syscall per page.

Cache semantics: snapshot — valid for the lifetime of the RemoteReader
instance, appropriate for a single dump invocation. The remote process
may mutate memory concurrently; cached reads reflect a consistent-enough
snapshot, same approach used by gdb / py-spy attach mode.
"""

import ctypes
import struct
from collections import OrderedDict

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

PAGE_SIZE = 4096
PAGE_MASK = PAGE_SIZE - 1
CACHE_MAX_PAGES = 256  # 1 MB
BYPASS_CACHE_THRESHOLD = PAGE_SIZE  # reads >= one page go straight to syscall


class RemoteReader:
    def __init__(self, pid):
        self.pid = pid
        self._cache = OrderedDict()
        self._local_iov = _iovec()
        self._remote_iov = _iovec()

    def _read_syscall(self, addr, length):
        """Raw process_vm_readv loop, no caching. Returns None on failure."""
        buf = (ctypes.c_char * length)()
        local = self._local_iov
        remote = self._remote_iov
        total = 0
        while total < length:
            local.iov_base = ctypes.c_void_p(ctypes.addressof(buf) + total)
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

    def _get_page(self, page_base):
        """Fetch a single full page, cache it on success.

        Returns PAGE_SIZE bytes, or None if the page is unreadable.
        Partial page reads (rare, end-of-mapping) are passed through
        uncached so callers still observe short reads via length checks.
        """
        page = self._cache.get(page_base)
        if page is not None:
            self._cache.move_to_end(page_base)
            return page
        data = self._read_syscall(page_base, PAGE_SIZE)
        if data is None:
            return None
        if len(data) < PAGE_SIZE:
            return data  # partial — do not cache
        self._cache[page_base] = data
        if len(self._cache) > CACHE_MAX_PAGES:
            self._cache.popitem(last=False)
        return data

    def read(self, addr, length):
        if length == 0:
            return b""

        # Large reads bypass the cache to avoid evicting hot small-page data.
        if length >= BYPASS_CACHE_THRESHOLD:
            return self._read_syscall(addr, length)

        start_off = addr & PAGE_MASK
        first_page = addr - start_off
        end = addr + length
        last_page = (end - 1) & ~PAGE_MASK

        if first_page == last_page:
            # single page — the common case for ptr/u32/u64 reads
            page = self._get_page(first_page)
            if page is None:
                return None
            return page[start_off:start_off + length]

        # multi-page: assemble from cached pages
        out = bytearray()
        page_base = first_page
        while page_base <= last_page:
            page = self._get_page(page_base)
            if page is None:
                return None
            if page_base == first_page:
                out += page[start_off:]
            elif page_base == last_page:
                remaining = length - len(out)
                out += page[:remaining]
            else:
                out += page
            page_base += PAGE_SIZE
        return bytes(out)

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
