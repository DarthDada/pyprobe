"""Unit tests for pyprobe.dict_iter — CPython 3.12 dict traversal."""

import struct

import pytest

from pyprobe import offsets
from pyprobe.dict_iter import DictIter
from tests.helpers import FakeReader

DICT_ADDR = 0x10000
KEYS_ADDR = 0x20000
VALUES_ADDR = 0x30000


def _build_dict(reader, kind, entries, values_addr=0, dk_log2=0):
    """Build a dict object + keys object + entries in the reader.

    *entries* is a list of (key, value) pairs for combined dicts (kind=0/1),
    or a list of keys for split dicts (values read from values_addr).
    """
    nentries = len(entries)
    indices_size = 1 << dk_log2
    keys_sz = offsets.get("dictkeysobject_size")

    # dictkeysobject
    keys_blob = bytearray(keys_sz)
    keys_blob[offsets.get("dictkeysobject.dk_log2_index_bytes")] = dk_log2
    keys_blob[offsets.get("dictkeysobject.dk_kind")] = kind
    struct.pack_into("<q", keys_blob, offsets.get("dictkeysobject.dk_nentries"),
                     nentries)
    reader.add(KEYS_ADDR, bytes(keys_blob))

    # entries
    if kind == 0:
        entry_sz = offsets.get("PyDictKeyEntry_size")  # 24
        key_off = 8
    else:
        entry_sz = offsets.get("PyDictUnicodeEntry_size")  # 16
        key_off = 0

    entries_blob = bytearray(nentries * entry_sz)
    for i, pair in enumerate(entries):
        key, value = pair
        struct.pack_into("<QQ", entries_blob, i * entry_sz + key_off, key, value)
    entries_addr = KEYS_ADDR + indices_size + keys_sz
    reader.add(entries_addr, bytes(entries_blob))

    # DictObject (ma_keys + ma_values)
    dict_blob = bytearray(offsets.get("DictObject.ma_values") + 8)
    struct.pack_into("<Q", dict_blob, offsets.get("DictObject.ma_keys"), KEYS_ADDR)
    struct.pack_into("<Q", dict_blob, offsets.get("DictObject.ma_values"), values_addr)
    reader.add(DICT_ADDR, bytes(dict_blob))


class TestFromDictCombined:
    """Combined dicts (kind=0): values stored inline in entries."""

    def test_iterate_two_entries(self):
        reader = FakeReader()
        _build_dict(reader, kind=0, entries=[(0xAAAA, 0xBBBB), (0xCCCC, 0xDDDD)])
        it = DictIter(reader)
        assert it.from_dict(DICT_ADDR) is True

        pair1 = it.next()
        assert pair1 == (0xAAAA, 0xBBBB)
        pair2 = it.next()
        assert pair2 == (0xCCCC, 0xDDDD)
        assert it.next() is None

    def test_empty_dict(self):
        reader = FakeReader()
        _build_dict(reader, kind=0, entries=[])
        it = DictIter(reader)
        assert it.from_dict(DICT_ADDR) is True
        assert it.next() is None

    def test_skip_holes(self):
        """Entries with key=0 are skipped."""
        reader = FakeReader()
        _build_dict(reader, kind=0, entries=[(0, 0), (0xAAAA, 0xBBBB)])
        it = DictIter(reader)
        assert it.from_dict(DICT_ADDR) is True
        assert it.next() == (0xAAAA, 0xBBBB)
        assert it.next() is None


class TestFromDictUnicode:
    """Unicode dicts (kind=1): PyDictUnicodeEntry (16 bytes, key_off=0)."""

    def test_iterate(self):
        reader = FakeReader()
        _build_dict(reader, kind=1, entries=[(0x1111, 0x2222), (0x3333, 0x4444)])
        it = DictIter(reader)
        assert it.from_dict(DICT_ADDR) is True
        assert it.next() == (0x1111, 0x2222)
        assert it.next() == (0x3333, 0x4444)
        assert it.next() is None


class TestFromDictSplit:
    """Split dicts: values read from a separate values array."""

    def test_split_values(self):
        reader = FakeReader()
        # entries store only keys; values in a separate array
        _build_dict(reader, kind=0, entries=[(0xAAAA, 0), (0xCCCC, 0)],
                    values_addr=VALUES_ADDR)
        # values array: ptr per entry
        reader.add(VALUES_ADDR, struct.pack("<QQ", 0xB111, 0xB222))

        it = DictIter(reader)
        assert it.from_dict(DICT_ADDR) is True
        pair1 = it.next()
        assert pair1[0] == 0xAAAA
        assert pair1[1] == 0xB111
        pair2 = it.next()
        assert pair2[0] == 0xCCCC
        assert pair2[1] == 0xB222
        assert it.next() is None


class TestFromDictFailures:
    def test_null_keys_returns_false(self):
        reader = FakeReader()
        dict_blob = bytearray(offsets.get("DictObject.ma_values") + 8)
        # ma_keys = 0
        reader.add(DICT_ADDR, bytes(dict_blob))
        it = DictIter(reader)
        assert it.from_dict(DICT_ADDR) is False

    def test_unreadable_dict_returns_false(self):
        reader = FakeReader()
        it = DictIter(reader)
        assert it.from_dict(0xDEAD) is False


class TestFromManagedValues:
    def test_from_managed_values(self):
        """Managed dict: values read from a separate values array, not entries."""
        reader = FakeReader()
        keys_sz = offsets.get("dictkeysobject_size")
        keys_blob = bytearray(keys_sz)
        keys_blob[offsets.get("dictkeysobject.dk_kind")] = 0
        struct.pack_into("<q", keys_blob, offsets.get("dictkeysobject.dk_nentries"), 1)
        reader.add(KEYS_ADDR, bytes(keys_blob))

        entry_sz = offsets.get("PyDictKeyEntry_size")
        entries_addr = KEYS_ADDR + (1 << 0) + keys_sz
        # entry: hash=0, key=0xAAAA, value=0 (value read from values array)
        reader.add(entries_addr, struct.pack("<QQQ", 0, 0xAAAA, 0))

        # values array: one pointer per entry
        reader.add_ptr(VALUES_ADDR, 0xBBBB)

        ht_off = offsets.get("HeapTypeObject.ht_cached_keys")
        type_addr = 0x40000
        reader.add_ptr(type_addr + ht_off, KEYS_ADDR)

        it = DictIter(reader)
        assert it.from_managed_values(VALUES_ADDR, type_addr) is True
        pair = it.next()
        assert pair is not None
        assert pair[0] == 0xAAAA
        assert pair[1] == 0xBBBB
        assert it.next() is None
