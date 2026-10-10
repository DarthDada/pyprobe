"""Contract tests for cpython/dicts.py (DI1–DI5).

Construction is Layout-driven (fakemem.build_dict); version matrix covers
every supported layout since dict table offsets differ across versions
(e.g. ht_cached_keys 872 on 3.11 vs 880 elsewhere).
"""

import pytest

from pyprobe.cpython.dicts import DictReader
from pyprobe.target.layout import resolve_layout
from tests.fakemem import FakeMemory, build_dict

LAYOUTS = ["3.11", "3.12", "3.13", "3.14"]
NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"
DICT_ADDR = 0x10000
KEYS_ADDR = 0x20000
VALUES_ADDR = 0x30000
TYPE_ADDR = 0x40000


@pytest.fixture(params=LAYOUTS, ids=LAYOUTS)
def layout(request):
    return resolve_layout(request.param, overrides_path=NO_OVERRIDE)


class TestFromDictCombined:
    """DI2：combined dict（kind=0，PyDictKeyEntry 24B，key_off=8）。"""

    def test_iterate_two_entries(self, layout):
        """DI4：两对完整遍历后耗尽 → None（基本迭代协议）。"""
        mem = FakeMemory()
        build_dict(mem, layout, DICT_ADDR, KEYS_ADDR,
                   [(0xAAAA, 0xBBBB), (0xCCCC, 0xDDDD)], kind=0)
        it = DictReader(mem, layout)
        assert it.from_dict(DICT_ADDR) is True
        assert it.next() == (0xAAAA, 0xBBBB)
        assert it.next() == (0xCCCC, 0xDDDD)
        assert it.next() is None

    def test_empty_dict(self, layout):
        """DI4 边界：nentries=0 → 立即耗尽（空 dict 合法，非失败）。"""
        mem = FakeMemory()
        build_dict(mem, layout, DICT_ADDR, KEYS_ADDR, [], kind=0)
        it = DictReader(mem, layout)
        assert it.from_dict(DICT_ADDR) is True
        assert it.next() is None

    def test_skip_holes(self, layout):
        """DI4：k==0 空洞条目跳过（删除后的 dict 常态；旧 dict_iter 语义）。"""
        mem = FakeMemory()
        build_dict(mem, layout, DICT_ADDR, KEYS_ADDR,
                   [(0, 0), (0xAAAA, 0xBBBB)], kind=0)
        it = DictReader(mem, layout)
        assert it.from_dict(DICT_ADDR) is True
        assert it.next() == (0xAAAA, 0xBBBB)
        assert it.next() is None


class TestFromDictUnicode:
    def test_iterate(self, layout):
        """DI2：unicode dict（kind=1，PyDictUnicodeEntry 16B，key_off=0）——
        条目布局选错则键值全错位。"""
        mem = FakeMemory()
        build_dict(mem, layout, DICT_ADDR, KEYS_ADDR,
                   [(0x1111, 0x2222), (0x3333, 0x4444)], kind=1)
        it = DictReader(mem, layout)
        assert it.from_dict(DICT_ADDR) is True
        assert it.next() == (0x1111, 0x2222)
        assert it.next() == (0x3333, 0x4444)
        assert it.next() is None


class TestFromDictSplit:
    def test_split_values(self, layout):
        """DI4：split dict 的值来自独立 values 数组而非条目内联（实例
        属性 dict 的常态形态，读错源则值全错）。"""
        mem = FakeMemory()
        build_dict(mem, layout, DICT_ADDR, KEYS_ADDR,
                   [(0xAAAA, 0), (0xCCCC, 0)], kind=0,
                   values_addr=VALUES_ADDR)
        mem.add(VALUES_ADDR, b"\x11\xb1" + bytes(6) + b"\x22\xb2" + bytes(6))
        it = DictReader(mem, layout)
        assert it.from_dict(DICT_ADDR) is True
        assert it.next() == (0xAAAA, 0xB111)
        assert it.next() == (0xCCCC, 0xB222)
        assert it.next() is None


class TestFromDictFailures:
    def test_null_keys_returns_false(self, layout):
        """DI1 失败模式：ma_keys==0 → False（空壳 dict 对象不崩遍历）。"""
        import struct as _st

        mem = FakeMemory()
        dict_blob = bytearray(layout.get("DictObject.ma_values") + 8)
        _st.pack_into("<Q", dict_blob, layout.get("DictObject.ma_keys"), 0)
        mem.add(DICT_ADDR, bytes(dict_blob))
        assert DictReader(mem, layout).from_dict(DICT_ADDR) is False

    def test_unreadable_dict_returns_false(self, layout):
        """DI1 失败模式：dict 头不可读 → False。"""
        assert DictReader(FakeMemory(), layout).from_dict(0xDEAD) is False


class TestFromManagedValues:
    def test_managed_values(self, layout):
        """DI5：managed dict——键经 heap type 的 ht_cached_keys，值经独立
        values 数组（3.12+ 实例属性的主路径；ht_cached_keys 偏移各版本
        不同，矩阵覆盖即守护）。"""
        import struct as _st

        mem = FakeMemory()
        keys_sz = layout.get("dictkeysobject_size")
        keys_blob = bytearray(keys_sz)
        keys_blob[layout.get("dictkeysobject.dk_kind")] = 0
        _st.pack_into("<q", keys_blob,
                      layout.get("dictkeysobject.dk_nentries"), 1)
        mem.add(KEYS_ADDR, bytes(keys_blob))
        # entry: hash=0, key=0xAAAA, value=0（值走 values 数组）
        mem.add(KEYS_ADDR + 1 + keys_sz,
                _st.pack("<QQQ", 0, 0xAAAA, 0))
        mem.add_ptr(VALUES_ADDR, 0xBBBB)
        mem.add_ptr(TYPE_ADDR + layout.get("HeapTypeObject.ht_cached_keys"),
                    KEYS_ADDR)

        it = DictReader(mem, layout)
        assert it.from_managed_values(VALUES_ADDR, TYPE_ADDR) is True
        assert it.next() == (0xAAAA, 0xBBBB)
        assert it.next() is None

    def test_null_cached_keys_returns_false(self, layout):
        """DI5 失败模式：ht_cached_keys 为 0 → False（类型尚无共享键）。"""
        mem = FakeMemory()
        mem.add_ptr(TYPE_ADDR + layout.get("HeapTypeObject.ht_cached_keys"), 0)
        it = DictReader(mem, layout)
        assert it.from_managed_values(VALUES_ADDR, TYPE_ADDR) is False
