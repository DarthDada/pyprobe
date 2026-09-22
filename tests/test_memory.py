"""Unit tests for pyprobe.memory — RemoteReader page-cache behaviour."""

import struct

from pyprobe.memory import (
    BYPASS_CACHE_THRESHOLD,
    CACHE_MAX_PAGES,
    PAGE_SIZE,
    PTR_SIZE,
)
from tests.helpers import FakeRemoteReader


def make_page(page_base, fill=0x41):
    return bytes([fill] * PAGE_SIZE)


class TestReadBasic:
    def test_zero_length(self):
        reader = FakeRemoteReader({0x0: make_page(0)})
        assert reader.read(0, 0) == b""

    def test_single_page_hit(self):
        page = bytes(range(256)) * 16  # 4096 bytes
        reader = FakeRemoteReader({0x0: page})
        assert reader.read(10, 4) == page[10:14]

    def test_unreadable_page_returns_none(self):
        reader = FakeRemoteReader({})
        assert reader.read(0, 4) is None

    def test_partial_page_uncached(self):
        """Partial syscall returns (end-of-mapping) pass through uncached."""
        short = bytes(range(100))  # less than PAGE_SIZE
        reader = FakeRemoteReader({0x0: short})
        assert reader.read(0, 50) == short[:50]
        # second read should hit syscall again (not cached)
        count_before = reader.syscall_count
        reader.read(0, 50)
        assert reader.syscall_count > count_before


class TestCaching:
    def test_cached_page_no_second_syscall(self):
        page = make_page(0, fill=0x42)
        reader = FakeRemoteReader({0x0: page})
        reader.read(0, 8)
        count = reader.syscall_count
        reader.read(8, 8)
        assert reader.syscall_count == count  # served from cache

    def test_multi_page_read(self):
        p0 = bytes([0xA0] * PAGE_SIZE)
        p1 = bytes([0xB0] * PAGE_SIZE)
        reader = FakeRemoteReader({0x0: p0, PAGE_SIZE: p1})
        # read spanning the boundary
        start = PAGE_SIZE - 4
        data = reader.read(start, 8)
        assert data == p0[-4:] + p1[:4]

    def test_multi_page_missing_returns_none(self):
        p0 = make_page(0)
        reader = FakeRemoteReader({0x0: p0})  # page 1 missing
        assert reader.read(PAGE_SIZE - 4, 8) is None

    def test_large_read_bypasses_cache(self):
        """Reads >= BYPASS_CACHE_THRESHOLD go straight to syscall."""
        p0 = make_page(0, fill=0x55)
        reader = FakeRemoteReader({0x0: p0})
        reader.read(0, BYPASS_CACHE_THRESHOLD)
        count = reader.syscall_count
        # second identical large read should NOT be cached
        reader.read(0, BYPASS_CACHE_THRESHOLD)
        assert reader.syscall_count > count


class TestLRUEviction:
    def test_cache_evicts_oldest(self):
        pages = {i * PAGE_SIZE: make_page(i, fill=i % 256)
                 for i in range(CACHE_MAX_PAGES + 1)}
        reader = FakeRemoteReader(pages)
        # Fill cache beyond capacity
        for i in range(CACHE_MAX_PAGES + 1):
            reader.read(i * PAGE_SIZE, 1)
        # page 0 should have been evicted → re-read triggers syscall
        count = reader.syscall_count
        reader.read(0, 1)
        assert reader.syscall_count > count  # cache miss


class TestReadHelpers:
    def test_read_ptr(self):
        page = bytearray(PAGE_SIZE)
        struct.pack_into("<Q", page, 16, 0xDEADBEEF)
        reader = FakeRemoteReader({0x0: bytes(page)})
        assert reader.read_ptr(16) == 0xDEADBEEF

    def test_read_ptr_failure(self):
        reader = FakeRemoteReader({})
        assert reader.read_ptr(0) is None

    def test_read_u32(self):
        page = bytearray(PAGE_SIZE)
        struct.pack_into("<I", page, 32, 0x12345678)
        reader = FakeRemoteReader({0x0: bytes(page)})
        assert reader.read_u32(32) == 0x12345678

    def test_read_u64(self):
        page = bytearray(PAGE_SIZE)
        struct.pack_into("<Q", page, 40, 0xCAFEBABE)
        reader = FakeRemoteReader({0x0: bytes(page)})
        assert reader.read_u64(40) == 0xCAFEBABE

    def test_read_int_signed(self):
        page = bytearray(PAGE_SIZE)
        struct.pack_into("<i", page, 48, -42)
        reader = FakeRemoteReader({0x0: bytes(page)})
        assert reader.read_int(48) == -42


class TestConstants:
    def test_page_size_is_4096(self):
        assert PAGE_SIZE == 4096

    def test_ptr_size_is_8(self):
        assert PTR_SIZE == 8

    def test_bypass_threshold_equals_page_size(self):
        assert BYPASS_CACHE_THRESHOLD == PAGE_SIZE
