"""Contract tests for kernel/views.py — SnapshotView/LiveView (V1–V9, A4).

FakeTransport maps exact addresses to bytes (None = unreadable) and counts
calls, so cache behaviour is observable directly. Scenarios replicate the
old test_memory.py coverage (see contracts.md 批 1 行为点表 for the
mapping), redesigned against the two-view API instead of ported.
"""

import struct

import pytest

from pyprobe.kernel.views import (
    BYPASS_CACHE_THRESHOLD,
    CACHE_MAX_PAGES,
    PAGE_SIZE,
    PTR_SIZE,
    LiveView,
    SnapshotView,
)


class FakeTransport:
    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []

    def read(self, addr, length):
        self.calls.append((addr, length))
        data = self.pages.get(addr)
        if data is None:
            return None
        return data[:length]


def make_page(fill=0x41):
    return bytes([fill % 256] * PAGE_SIZE)


class TestSnapshotView:
    def test_zero_length_no_transport_call(self):
        """M6/V1 边界：length=0 → b"" 且不触 transport——防空读白耗一次
        syscall（栈遍历高频小读路径上的性能守护）。"""
        t = FakeTransport({0: make_page()})
        assert SnapshotView(t).read(0, 0) == b""
        assert t.calls == []

    def test_single_page_hit(self):
        """V1 快乐路径：页内偏移切片正确（帧链/对象字段小读是快照期
        最高频路径，切片错位即全盘皆错）。"""
        page = bytes(range(256)) * 16
        v = SnapshotView(FakeTransport({0: page}))
        assert v.read(10, 4) == page[10:14]

    def test_unreadable_page_returns_none(self):
        """V2 失败模式：整页不可读 → None（调用方以 None 判失败，不抛异常）。"""
        v = SnapshotView(FakeTransport({}))
        assert v.read(0, 4) is None

    def test_partial_page_not_cached(self):
        """V3: short page (end-of-mapping) passes through, stays uncached."""
        short = bytes(range(100))
        t = FakeTransport({0: short})
        v = SnapshotView(t)
        assert v.read(0, 50) == short[:50]
        v.read(0, 50)
        assert len(t.calls) == 2  # second read hit the transport again

    def test_cached_page_no_second_syscall(self):
        """V1: second read in the same page is served from cache."""
        t = FakeTransport({0: make_page(0x42)})
        v = SnapshotView(t)
        v.read(0, 8)
        assert len(t.calls) == 1
        v.read(8, 8)
        assert len(t.calls) == 1

    def test_multi_page_read(self):
        """V4: a read spanning pages assembles from both cached pages."""
        p0 = bytes([0xA0] * PAGE_SIZE)
        p1 = bytes([0xB0] * PAGE_SIZE)
        v = SnapshotView(FakeTransport({0: p0, PAGE_SIZE: p1}))
        assert v.read(PAGE_SIZE - 4, 8) == p0[-4:] + p1[:4]

    def test_multi_page_missing_returns_none(self):
        """V4: any missing page in the span fails the whole read."""
        v = SnapshotView(FakeTransport({0: make_page()}))
        assert v.read(PAGE_SIZE - 4, 8) is None

    def test_large_read_bypasses_cache(self):
        """V5: reads >= BYPASS_CACHE_THRESHOLD never populate the cache."""
        t = FakeTransport({0: make_page(0x55)})
        v = SnapshotView(t)
        v.read(0, BYPASS_CACHE_THRESHOLD)
        assert len(t.calls) == 1
        v.read(0, BYPASS_CACHE_THRESHOLD)
        assert len(t.calls) == 2  # not cached
        v.read(0, 8)
        assert len(t.calls) == 3  # and did not seed the page cache

    def test_lru_evicts_oldest(self):
        """V6: beyond max_pages, the least-recently-used page is evicted."""
        pages = {i * PAGE_SIZE: make_page(i) for i in range(4)}
        t = FakeTransport(pages)
        v = SnapshotView(t, max_pages=3)
        for i in range(4):
            v.read(i * PAGE_SIZE, 1)
        assert len(t.calls) == 4
        v.read(0, 1)  # page 0 was evicted -> cache miss
        assert len(t.calls) == 5

    def test_lru_hit_refreshes_position(self):
        """V6: a cache hit marks the page as recently used."""
        pages = {i * PAGE_SIZE: make_page(i) for i in range(4)}
        t = FakeTransport(pages)
        v = SnapshotView(t, max_pages=3)
        v.read(0, 1)
        v.read(PAGE_SIZE, 1)
        v.read(2 * PAGE_SIZE, 1)
        v.read(0, 1)  # refresh page 0 -> page 1 becomes oldest
        v.read(3 * PAGE_SIZE, 1)  # evicts page 1, not page 0
        calls = len(t.calls)
        v.read(0, 1)
        assert len(t.calls) == calls  # page 0 still cached


class TestLiveView:
    def test_every_read_hits_transport(self):
        """V8: no caching — long-running observation sees fresh bytes."""
        t = FakeTransport({0: make_page()})
        v = LiveView(t)
        v.read(0, 8)
        v.read(0, 8)
        assert len(t.calls) == 2

    def test_zero_length_no_transport_call(self):
        """V8 边界：LiveView 同样空读短路——两视图共享的 M6 语义。"""
        t = FakeTransport({})
        assert LiveView(t).read(0, 0) == b""
        assert t.calls == []

    def test_unreadable_returns_none(self):
        """V8 失败模式：LiveView 不可读 → None（与 SnapshotView 一致的
        失败约定，消费方无需分辨视图种类）。"""
        assert LiveView(FakeTransport({})).read(0, 4) is None


class TestTypedHelpers:
    """V7: little-endian decode; short/unreadable -> None. Both views."""

    @staticmethod
    def _page_with(fmt, offset, value):
        page = bytearray(PAGE_SIZE)
        struct.pack_into(fmt, page, offset, value)
        return bytes(page)

    @pytest.mark.parametrize("view_cls", [SnapshotView, LiveView])
    def test_read_ptr(self, view_cls):
        """V7：8 字节指针小端解码（遍历代码最频繁的读原语，解码错位即
        全盘地址错误）。"""
        v = view_cls(FakeTransport({0: self._page_with("<Q", 16, 0xDEADBEEF)}))
        assert v.read_ptr(16) == 0xDEADBEEF

    @pytest.mark.parametrize("view_cls", [SnapshotView, LiveView])
    def test_read_u32(self, view_cls):
        """V7：4 字节无符号解码（u32 字段读取，如 flags/版本号）。"""
        v = view_cls(FakeTransport({0: self._page_with("<I", 32, 0x12345678)}))
        assert v.read_u32(32) == 0x12345678

    @pytest.mark.parametrize("view_cls", [SnapshotView, LiveView])
    def test_read_u64(self, view_cls):
        """V7：8 字节无符号解码（与 read_ptr 同宽但语义为值而非地址）。"""
        v = view_cls(FakeTransport({0: self._page_with("<Q", 40, 0xCAFEBABE)}))
        assert v.read_u64(40) == 0xCAFEBABE

    @pytest.mark.parametrize("view_cls", [SnapshotView, LiveView])
    def test_read_int_signed(self, view_cls):
        """V7：4 字节有符号解码——负数符号位不丢（refcount 类字段允许
        负值语义，无符号化会静默错值）。"""
        v = view_cls(FakeTransport({0: self._page_with("<i", 48, -42)}))
        assert v.read_int(48) == -42

    @pytest.mark.parametrize("view_cls", [SnapshotView, LiveView])
    def test_unreadable_returns_none(self, view_cls):
        """V7 失败模式：四个类型助手在不可读地址一律 None——调用方统一
        以 None 判失败，防某个助手漏判长度退化成 struct.error 异常。"""
        v = view_cls(FakeTransport({}))
        assert v.read_ptr(0) is None
        assert v.read_u32(0) is None
        assert v.read_u64(0) is None
        assert v.read_int(0) is None

    @pytest.mark.parametrize("view_cls", [SnapshotView, LiveView])
    def test_short_read_returns_none(self, view_cls):
        """V7 边界：映射末尾部分页导致短读 → None（长度校验契约——短读
        硬解会产出垃圾值，比失败更糟）。4 字节页上读 8 字节指针。"""
        v = view_cls(FakeTransport({0: b"\x01\x02\x03\x04"}))
        assert v.read_ptr(0) is None


class TestConstants:
    """V9: values are 复刻约束 (old memory.py; page size from getconf)."""

    def test_page_size_is_4096(self):
        """V9：页大小 pin（缓存正确性的地基常数，改动即缓存键全错）。"""
        assert PAGE_SIZE == 4096

    def test_ptr_size_is_8(self):
        """V9：指针宽 pin（64 位 LP64 假设，aarch64 验证时同样成立）。"""
        assert PTR_SIZE == 8

    def test_bypass_threshold_equals_page_size(self):
        """V9：绕过阈值 == 页大小（大读不挤占热页缓存的策略不变量）。"""
        assert BYPASS_CACHE_THRESHOLD == PAGE_SIZE

    def test_cache_max_pages_is_256(self):
        """V9：缓存上限 256 页 = 1MB（内存占用预算的复刻约束）。"""
        assert CACHE_MAX_PAGES == 256
