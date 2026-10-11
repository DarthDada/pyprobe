"""Dictionary table traversal for CPython dicts (combined/unicode/split/
managed-values), layout-bound (ADR A3).

契约（contracts.md 批 3 表 DI1–DI5）。
"""

import struct

from ..kernel.views import PTR_SIZE
from .objects import MAX_STR_LEN


class DictReader:
    """Iterate (key_addr, value_addr) pairs of a remote dict."""

    def __init__(self, view, layout):
        self.view = view
        self.layout = layout
        self.index = 0
        self.nentries = 0
        self.values = 0
        self.entries = b""
        self.entry_size = 0
        self.key_off = 0

    def from_dict(self, dict_addr: int) -> bool:
        """Bind to a PyDictObject (DI1–DI3). False on unreadable/null keys."""
        layout = self.layout
        raw = self.view.read(dict_addr,
                             layout.get("DictObject.ma_values") + PTR_SIZE)
        if raw is None:
            return False  # DI1 — unreadable dict header
        keys_addr = struct.unpack_from(
            "<Q", raw, layout.get("DictObject.ma_keys"))[0]
        if keys_addr == 0:
            return False  # DI1 — null ma_keys
        values_addr = struct.unpack_from(
            "<Q", raw, layout.get("DictObject.ma_values"))[0]
        return self._init_from_keys(keys_addr, values_addr)

    def from_managed_values(self, values_addr: int, type_addr: int) -> bool:
        """Bind to a split managed-values array + its heap type's cached
        keys (DI5). False when the cached keys are unavailable."""
        keys_addr = self.view.read_ptr(
            type_addr + self.layout.get("HeapTypeObject.ht_cached_keys"))
        if keys_addr is None or keys_addr == 0:
            return False  # DI5 — no cached keys on the type
        return self._init_from_keys(keys_addr, values_addr)

    def next(self) -> tuple[int, int] | None:
        """Next (key, value) pair, skipping holes (DI4); None when done."""
        while self.index < self.nentries:
            idx = self.index
            self.index += 1

            if not self.entries:
                return None  # entries block unavailable/oversize → stop

            base = idx * self.entry_size
            k, v = struct.unpack_from("<QQ", self.entries,
                                      base + self.key_off)

            if k == 0:
                continue  # DI4 — deleted-entry hole

            if self.values != 0:
                # DI4 — split dict: the value lives in the separate values
                # array, not inline in the entry.
                v = self.view.read_ptr(self.values + idx * PTR_SIZE)
                if v is None:
                    continue

            return (k, v)
        return None  # DI4 — exhausted

    def _init_from_keys(self, keys_addr: int, values_addr: int) -> bool:
        """Parse the dictkeysobject header and pre-read the entries block
        (DI2/DI3 — shared by from_dict and from_managed_values)."""
        layout = self.layout
        keys_sz = layout.get("dictkeysobject_size")
        raw = self.view.read(keys_addr, keys_sz)
        if raw is None or len(raw) < keys_sz:
            return False

        dk_log2 = raw[layout.get("dictkeysobject.dk_log2_index_bytes")]
        dk_kind = raw[layout.get("dictkeysobject.dk_kind")]
        self.nentries = struct.unpack_from(
            "<q", raw, layout.get("dictkeysobject.dk_nentries"))[0]
        self.values = values_addr

        if dk_kind == 0:
            # DI2 — combined PyDictKeyEntry: hash(8) then key/value.
            self.entry_size = layout.get("PyDictKeyEntry_size")
            self.key_off = 8
        else:
            # DI2 — PyDictUnicodeEntry: key/value only.
            self.entry_size = layout.get("PyDictUnicodeEntry_size")
            self.key_off = 0

        # DI3 — entries live past the dk_indices array and the keys header.
        entries_addr = keys_addr + (1 << dk_log2) + keys_sz
        total = self.nentries * self.entry_size
        if 0 < total <= MAX_STR_LEN:
            block = self.view.read(entries_addr, total)
            self.entries = block if block is not None else b""
        else:
            self.entries = b""
        return True
