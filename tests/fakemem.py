"""FakeMemory + Layout-driven CPython object builders (契约 §10.2-2).

FakeMemory is a region-based view double for ``kernel/views`` consumers:
``{base_addr: bytes}``, ``read`` returns the slice of the region fully
containing ``[addr, addr+length)``, else ``None`` (mirrors short-read /
unmapped semantics of the real transport).

Builders are **driven by the Layout under test** — offsets are read from
the passed ``layout`` (target/layout.py), never copied from implementation
constants, so a layout/reader mismatch fails loudly instead of
tautologically confirming the implementation's own table (测试数据纪律:
构造器与被测代码不得共享同一事实来源的抄写路径).
"""

import struct

PTR_SIZE = 8  # 64-bit LP64 — same assumption as kernel/views.PTR_SIZE


class FakeMemory:
    """Region-based view double (read/read_ptr/read_int/read_u32/read_u64)."""

    def __init__(self, regions=None):
        self.regions = dict(regions) if regions else {}

    def add(self, addr, data):
        """Place a contiguous byte blob at *addr*."""
        self.regions[addr] = bytes(data)

    def add_ptr(self, addr, value):
        """Write a single 8-byte little-endian pointer."""
        self.add(addr, struct.pack("<Q", value))

    def read(self, addr, length):
        if length == 0:
            return b""
        for base, blob in self.regions.items():
            if base <= addr and addr + length <= base + len(blob):
                return blob[addr - base: addr - base + length]
        return None

    def read_ptr(self, addr):
        d = self.read(addr, PTR_SIZE)
        return None if d is None or len(d) < PTR_SIZE else struct.unpack("<Q", d)[0]

    def read_int(self, addr):
        d = self.read(addr, 4)
        return None if d is None or len(d) < 4 else struct.unpack("<i", d)[0]

    def read_u32(self, addr):
        d = self.read(addr, 4)
        return None if d is None or len(d) < 4 else struct.unpack("<I", d)[0]

    def read_u64(self, addr):
        d = self.read(addr, 8)
        return None if d is None or len(d) < 8 else struct.unpack("<Q", d)[0]


# ---------------------------------------------------------------------------
# Object builders — each writes bytes into a FakeMemory, offsets from layout
# ---------------------------------------------------------------------------

def build_unicode(mem, layout, addr, text):
    """Compact-ASCII PyUnicode (state: compact=1 bit5, ascii=1 bit6)."""
    data = text.encode("ascii")
    ascii_sz = layout.get("PyASCIIObject_size")
    blob = bytearray(ascii_sz + len(data))
    struct.pack_into("<q", blob, 16, len(data))  # PyASCIIObject.length @16
    blob[32] = 0x60  # state byte @32: compact(bit5) | ascii(bit6)
    blob[ascii_sz:ascii_sz + len(data)] = data
    mem.add(addr, bytes(blob))


def build_unicode_kind(mem, layout, addr, text, kind):
    """Compact non-ASCII PyUnicode of the given kind (1=latin-1, 2=utf-16-le,
    4=utf-32-le; encodings per CPython unicodeobject.h)."""
    encoding = {1: "latin-1", 2: "utf-16-le", 4: "utf-32-le"}[kind]
    data = text.encode(encoding)
    compact_sz = layout.get("PyCompactUnicodeObject_size")
    blob = bytearray(compact_sz + len(data))
    struct.pack_into("<q", blob, 16, len(text))
    blob[32] = 0x20 | (kind << 2)  # compact(bit5) | kind(bits2-4)
    blob[compact_sz:compact_sz + len(data)] = data
    mem.add(addr, bytes(blob))


def build_unicode_noncompact(mem, layout, addr, text, data_addr, kind=2):
    """Non-compact PyUnicode whose data lives at a separate *data_addr*
    (data pointer at layout's PyUnicodeObject.data_any)."""
    data = text.encode("utf-16-le")
    full_sz = layout.get("PyUnicodeObject.data_any") + PTR_SIZE
    blob = bytearray(full_sz)
    struct.pack_into("<q", blob, 16, len(text))
    blob[32] = kind << 2  # compact=0, kind in bits2-4
    struct.pack_into("<Q", blob, layout.get("PyUnicodeObject.data_any"),
                     data_addr)
    mem.add(addr, bytes(blob))
    mem.add(data_addr, data)


def build_bytes(mem, layout, addr, data):
    """PyBytes with *data* as ob_sval payload."""
    sval_off = layout.get("BytesObject.ob_sval")
    blob = bytearray(sval_off + len(data))
    struct.pack_into("<q", blob, layout.get("VarObject.ob_size"), len(data))
    blob[sval_off:sval_off + len(data)] = data
    mem.add(addr, bytes(blob))


def build_long(mem, layout, addr, value):
    """PyLong for non-negative *value* (0 … 2**60-1).

    Layout-driven version branch: 3.12+ packs the digit count into lv_tag's
    high bits; 3.11 stores a plain signed count in ob_size.
    """
    digit_sz = layout.get("digit_size")
    lv_tag_off = layout.get_or("LongObject.long_value.lv_tag")
    digit_off = layout.get_or("LongObject.long_value.ob_digit",
                              layout.get_or("LongObject.ob_digit"))
    if value == 0:
        size = 0
        blob = bytearray(digit_off)
    elif value < (1 << 30):
        size = 1
        blob = bytearray(digit_off + digit_sz)
        struct.pack_into("<I", blob, digit_off, value)
    elif value < (1 << 60):
        size = 2
        blob = bytearray(digit_off + 2 * digit_sz)
        struct.pack_into("<I", blob, digit_off, value & 0x3FFFFFFF)
        struct.pack_into("<I", blob, digit_off + digit_sz, value >> 30)
    else:
        raise ValueError("value too large for test builder")
    if lv_tag_off is not None:
        struct.pack_into("<Q", blob, lv_tag_off, size << 3)
    else:
        struct.pack_into("<q", blob, layout.get("LongObject.ob_size"), size)
    mem.add(addr, bytes(blob))


def build_dict(mem, layout, dict_addr, keys_addr, entries, *, kind=1,
               values_addr=0, dk_log2=0):
    """PyDict + dictkeysobject + entries.

    *entries* is a list of (key_addr, value_addr) pairs; for split dicts
    (*values_addr* != 0) entry values are ignored and read from the values
    array instead. kind: 0=combined PyDictKeyEntry, 1=PyDictUnicodeEntry.
    """
    nentries = len(entries)
    keys_sz = layout.get("dictkeysobject_size")
    keys_blob = bytearray(keys_sz)
    keys_blob[layout.get("dictkeysobject.dk_log2_index_bytes")] = dk_log2
    keys_blob[layout.get("dictkeysobject.dk_kind")] = kind
    struct.pack_into("<q", keys_blob,
                     layout.get("dictkeysobject.dk_nentries"), nentries)
    mem.add(keys_addr, bytes(keys_blob))

    if kind == 0:
        entry_sz = layout.get("PyDictKeyEntry_size")
        key_off = 8  # PyDictKeyEntry: hash(8) then key/value
    else:
        entry_sz = layout.get("PyDictUnicodeEntry_size")
        key_off = 0
    entries_blob = bytearray(nentries * entry_sz)
    for i, (k, v) in enumerate(entries):
        struct.pack_into("<QQ", entries_blob, i * entry_sz + key_off, k, v)
    mem.add(keys_addr + (1 << dk_log2) + keys_sz, bytes(entries_blob))

    dict_blob = bytearray(layout.get("DictObject.ma_values") + PTR_SIZE)
    struct.pack_into("<Q", dict_blob, layout.get("DictObject.ma_keys"),
                     keys_addr)
    struct.pack_into("<Q", dict_blob, layout.get("DictObject.ma_values"),
                     values_addr)
    mem.add(dict_addr, bytes(dict_blob))


def build_code(mem, layout, addr, name_addr, filename_addr, firstlineno,
               linetable_addr):
    """Minimal CodeObject: name/filename/firstlineno/linetable pointers."""
    end = layout.get("CodeObject.co_code_adaptive") + 8
    blob = bytearray(end)
    struct.pack_into("<i", blob, layout.get("CodeObject.co_firstlineno"),
                     firstlineno)
    struct.pack_into("<Q", blob, layout.get("CodeObject.co_filename"),
                     filename_addr)
    struct.pack_into("<Q", blob, layout.get("CodeObject.co_name"), name_addr)
    struct.pack_into("<Q", blob, layout.get("CodeObject.co_qualname"),
                     name_addr)
    struct.pack_into("<Q", blob, layout.get("CodeObject.co_linetable"),
                     linetable_addr)
    mem.add(addr, bytes(blob))


def build_frame(mem, layout, addr, code_addr, previous_addr, prev_instr):
    """InterpreterFrame with f_code/previous/prev_instr."""
    frame_sz = layout.get("InterpreterFrame.prev_instr") + PTR_SIZE
    blob = bytearray(frame_sz)
    struct.pack_into("<Q", blob, layout.get("InterpreterFrame.f_code"),
                     code_addr)
    struct.pack_into("<Q", blob, layout.get("InterpreterFrame.previous"),
                     previous_addr)
    struct.pack_into("<Q", blob, layout.get("InterpreterFrame.prev_instr"),
                     prev_instr)
    mem.add(addr, bytes(blob))


def build_tstate(mem, layout, addr, next_addr, thread_id, native_tid):
    """ThreadState with next/thread_id/native_thread_id."""
    end = max(layout.get("ThreadState.next"),
              layout.get("ThreadState.thread_id"),
              layout.get("ThreadState.native_thread_id")) + PTR_SIZE
    blob = bytearray(end)
    struct.pack_into("<Q", blob, layout.get("ThreadState.next"), next_addr)
    struct.pack_into("<Q", blob, layout.get("ThreadState.thread_id"),
                     thread_id)
    struct.pack_into("<Q", blob, layout.get("ThreadState.native_thread_id"),
                     native_tid)
    mem.add(addr, bytes(blob))


# ---------------------------------------------------------------------------
# Linetable byte builders (PEP 626; first byte bit7 = entry marker)
# ---------------------------------------------------------------------------

def linetable_no_line(num_entries=8):
    """Every entry code=15 (no line info) → firstlineno fallback for any
    lasti. 0xFF: code=15, len_code=7, bit7 set."""
    return bytes([0xFF] * num_entries)


def linetable_simple_increments():
    """Consecutive 2-byte ranges with increasing lines (firstlineno=10):
    [0,2)→11 (+1), [2,4)→13 (+2), [4,6)→13 (+0), [6,8)→13 (+0)."""
    return bytes([
        (11 << 3) | 0x80,  # code=11 → +1
        (12 << 3) | 0x80,  # code=12 → +2
        (10 << 3) | 0x80,  # code=10 → +0
        (10 << 3) | 0x80,  # code=10 → +0
    ])


def linetable_with_no_line():
    """A code=15 entry in the middle (firstlineno=10):
    [0,2)→11 (+1), [2,4)→no line → fallback, [4,6)→12 (+1)."""
    return bytes([
        (11 << 3) | 0x80,
        (15 << 3) | 0x80,
        (11 << 3) | 0x80,
    ])
