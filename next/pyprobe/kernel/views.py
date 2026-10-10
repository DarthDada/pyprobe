"""Memory-consistency views over a byte transport (ADR A4).

Two views replace the old tree's four scattered mechanisms (RemoteReader
page cache + ``read_uncached`` + ``_UncachedReader`` + sampler's per-sample
reader construction). Every observation declares its consistency need by
picking a view:

* ``SnapshotView`` — page-level LRU cache, valid for one observation
  lifetime. Stack walking exhibits high spatial locality (frame chains,
  adjacent CodeObject fields, contiguous dict entries, per-key unicode
  bodies); caching whole pages collapses many small 4–8 byte reads into one
  syscall per page. The remote process may mutate concurrently; cached reads
  reflect a consistent-enough snapshot (same approach as gdb / py-spy
  attach mode).
* ``LiveView`` — no cache; every read is a fresh syscall. For long-running
  observation (syscall tracing, sampling) where the target mutates the same
  memory between observations.

契约（contracts.md 批 1 表 V1–V9）。当前为骨架：方法返回哨兵 None，
常量与类型签名已锁定，批次 1 实现填实。
"""

import struct
from collections import OrderedDict

PTR_FMT = "<Q"  # little-endian 64-bit
PTR_SIZE = struct.calcsize(PTR_FMT)
INT_FMT = "<i"
INT_SIZE = struct.calcsize(INT_FMT)

PAGE_SIZE = 4096
PAGE_MASK = PAGE_SIZE - 1
CACHE_MAX_PAGES = 256  # 1 MB
BYPASS_CACHE_THRESHOLD = PAGE_SIZE  # reads >= one page go straight to syscall


class _ViewBase:
    """Shared paged reads + typed-read helpers.

    Subclasses implement ``_fetch_page``: ``SnapshotView`` routes it
    through the LRU cache, ``LiveView`` re-reads every time.
    """

    def __init__(self, transport):
        self.transport = transport

    def read(self, addr: int, length: int) -> bytes | None:
        if length == 0:
            return b""  # view layer intercepts; no transport call
        # V5: large reads bypass the page machinery (and never populate
        # the cache) to avoid evicting hot small-page data.
        if length >= BYPASS_CACHE_THRESHOLD:
            return self.transport.read(addr, length)
        return self._read_paged(addr, length)

    def _fetch_page(self, page_base: int) -> bytes | None:
        raise NotImplementedError

    def _read_paged(self, addr: int, length: int) -> bytes | None:
        """Assemble a sub-page-threshold read from whole pages.

        Any missing page fails the whole read (V4).
        """
        start_off = addr & PAGE_MASK
        first_page = addr - start_off
        last_page = (addr + length - 1) & ~PAGE_MASK

        if first_page == last_page:
            # single page — the common case for ptr/u32/u64 reads
            page = self._fetch_page(first_page)
            if page is None:
                return None  # V2
            return page[start_off:start_off + length]

        out = bytearray()
        page_base = first_page
        while page_base <= last_page:
            page = self._fetch_page(page_base)
            if page is None:
                return None
            if page_base == first_page:
                out += page[start_off:]
            elif page_base == last_page:
                out += page[:length - len(out)]
            else:
                out += page
            page_base += PAGE_SIZE
        return bytes(out)

    def _read_scalar(self, addr: int, fmt: str, size: int) -> int | None:
        """Little-endian typed read; short/unreadable -> None (V7)."""
        data = self.read(addr, size)
        if data is None or len(data) < size:
            return None
        return struct.unpack(fmt, data)[0]

    def read_ptr(self, addr: int) -> int | None:
        return self._read_scalar(addr, PTR_FMT, PTR_SIZE)

    def read_int(self, addr: int) -> int | None:
        return self._read_scalar(addr, INT_FMT, INT_SIZE)

    def read_u32(self, addr: int) -> int | None:
        return self._read_scalar(addr, "<I", 4)

    def read_u64(self, addr: int) -> int | None:
        return self._read_scalar(addr, "<Q", 8)


class SnapshotView(_ViewBase):
    """Page-caching view for single-shot observation (契约 V1–V7, V9)."""

    def __init__(self, transport, *, max_pages: int = CACHE_MAX_PAGES):
        super().__init__(transport)
        self.max_pages = max_pages
        self._cache = OrderedDict()

    def _fetch_page(self, page_base: int) -> bytes | None:
        """Fetch one page through the LRU cache (V1/V3/V6).

        Partial pages (rare, end-of-mapping) pass through uncached so
        callers still observe short reads via length checks.
        """
        page = self._cache.get(page_base)
        if page is not None:
            self._cache.move_to_end(page_base)  # V6: hit refreshes position
            return page
        data = self.transport.read(page_base, PAGE_SIZE)
        if data is None:
            return None
        if len(data) < PAGE_SIZE:
            return data  # V3: partial — do not cache
        self._cache[page_base] = data
        if len(self._cache) > self.max_pages:
            self._cache.popitem(last=False)  # V6: evict least-recently-used
        return data


class LiveView(_ViewBase):
    """Cache-free view for long-running observation (契约 V7, V8)."""

    def _fetch_page(self, page_base: int) -> bytes | None:
        # V8: no caching — every read is a fresh transport call, so a
        # long-running observation sees the target's current bytes.
        return self.transport.read(page_base, PAGE_SIZE)
