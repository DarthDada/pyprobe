"""Dictionary iterator for CPython 3.12 dicts (combined/split/unicode tables)."""

import struct

from .memory import RemoteReader, PTR_SIZE, MAX_STR_LEN
from . import offsets


class DictIter:
    def __init__(self, reader):
        self.reader = reader
        self.kind = 0
        self.index = 0
        self.nentries = 0
        self.values = 0
        self.entries = None
        self.entry_size = 0

    def _init_from_keys(self, keys_addr, values_addr):
        keys_sz = offsets.get("dictkeysobject_size")
        raw = self.reader.read(keys_addr, keys_sz)
        if raw is None or len(raw) < keys_sz:
            return False

        dk_log2 = raw[offsets.get("dictkeysobject.dk_log2_index_bytes")]
        self.kind = raw[offsets.get("dictkeysobject.dk_kind")]
        self.nentries = struct.unpack_from("<q", raw,
                                           offsets.get("dictkeysobject.dk_nentries"))[0]
        self.values = values_addr

        if self.kind == 0:
            self.entry_size = offsets.get("PyDictKeyEntry_size")
        else:
            self.entry_size = offsets.get("PyDictUnicodeEntry_size")

        indices_size = 1 << dk_log2
        entries_addr = keys_addr + indices_size + keys_sz

        total = self.nentries * self.entry_size
        if total > 0 and total <= MAX_STR_LEN:
            self.entries = self.reader.read(entries_addr, total)
            if self.entries is None:
                self.entries = b""
        else:
            self.entries = b""
        return True

    def from_dict(self, dict_addr):
        raw = self.reader.read(dict_addr,
                               offsets.get("DictObject.ma_values") + PTR_SIZE)
        if raw is None:
            return False
        keys_addr = struct.unpack_from("<Q", raw,
                                       offsets.get("DictObject.ma_keys"))[0]
        if keys_addr == 0:
            return False
        values_addr = struct.unpack_from("<Q", raw,
                                         offsets.get("DictObject.ma_values"))[0]
        return self._init_from_keys(keys_addr, values_addr)

    def from_managed_values(self, values_addr, type_addr):
        ht_off = offsets.get("HeapTypeObject.ht_cached_keys")
        keys_addr = self.reader.read_ptr(type_addr + ht_off)
        if keys_addr is None or keys_addr == 0:
            return False
        return self._init_from_keys(keys_addr, values_addr)

    def next(self):
        while self.index < self.nentries:
            idx = self.index
            self.index += 1

            if not self.entries:
                return None

            base = idx * self.entry_size
            k, v = struct.unpack_from("<QQ", self.entries, base)

            if k == 0:
                continue

            if self.values != 0:
                v = self.reader.read_ptr(self.values + idx * PTR_SIZE)
                if v is None:
                    continue

            return (k, v)
        return None
