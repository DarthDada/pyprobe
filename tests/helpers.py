"""Shared test helpers: FakeReader and CPython object memory builders.

These let unit tests exercise pyprobe's parsing logic (linetable, dict
iteration, object reading, frame walking) without a real remote process.

Memory model: FakeReader stores non-overlapping regions as
``{base_addr: bytes}``.  ``read(addr, length)`` finds the region that fully
contains ``[addr, addr+length)`` and returns the slice, or ``None``.
"""

import struct

from pyprobe import offsets
from pyprobe.memory import PAGE_MASK, PTR_SIZE, RemoteReader


class FakeReader:
    """Drop-in replacement for RemoteReader backed by an in-memory dict."""

    def __init__(self, regions=None):
        self.regions = dict(regions) if regions else {}

    def add(self, addr, data):
        """Place a contiguous byte blob at *addr*."""
        self.regions[addr] = bytes(data)

    def add_ptr(self, addr, value):
        """Convenience: write a single 8-byte little-endian pointer."""
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
# CPython object memory builders — each writes bytes into a FakeReader.
# Offsset values come from the configured offsets table (CPython 3.12).
# ---------------------------------------------------------------------------

def build_pyunicode(reader, addr, text):
    """Write a compact-ASCII PyUnicode object at *addr*."""
    data = text.encode("ascii")
    length = len(data)
    ascii_sz = offsets.get("PyASCIIObject_size")
    blob = bytearray(ascii_sz + length)
    struct.pack_into("<q", blob, 16, length)
    blob[32] = 0x60  # compact=1, is_ascii=1
    blob[ascii_sz:ascii_sz + length] = data
    reader.add(addr, bytes(blob))


def build_pyunicode_utf16(reader, addr, text):
    """Write a compact non-ASCII PyUnicode (kind=2, utf-16-le) at *addr*."""
    data = text.encode("utf-16-le")
    length = len(text)
    compact_sz = offsets.get("PyCompactUnicodeObject_size")
    blob = bytearray(compact_sz + len(data))
    struct.pack_into("<q", blob, 16, length)
    blob[32] = 0x28  # compact=1, is_ascii=0, kind=2
    blob[compact_sz:compact_sz + len(data)] = data
    reader.add(addr, bytes(blob))


def build_pyunicode_noncompact(reader, addr, text, data_addr):
    """Write a non-compact PyUnicode whose data lives at a separate *data_addr*."""
    data = text.encode("utf-16-le")
    length = len(text)
    full_sz = offsets.get("PyUnicodeObject.data_any") + PTR_SIZE
    blob = bytearray(full_sz)
    struct.pack_into("<q", blob, 16, length)
    blob[32] = 0x08  # compact=0, is_ascii=0, kind=2
    struct.pack_into("<Q", blob, offsets.get("PyUnicodeObject.data_any"), data_addr)
    reader.add(addr, bytes(blob))
    reader.add(data_addr, data)


def build_pybytes(reader, addr, data):
    """Write a PyBytes object with *data* as ob_sval."""
    sval_off = offsets.get("BytesObject.ob_sval")
    blob = bytearray(sval_off + len(data))
    struct.pack_into("<q", blob, offsets.get("VarObject.ob_size"), len(data))
    blob[sval_off:sval_off + len(data)] = data
    reader.add(addr, bytes(blob))


def build_pylong(reader, addr, value):
    """Write a PyLong object for a non-negative *value* (0 … 2**60-1)."""
    tag_off = offsets.get("LongObject.long_value.lv_tag")
    digit_off = offsets.get("LongObject.long_value.ob_digit")
    digit_sz = offsets.get("digit_size")
    if value == 0:
        blob = bytearray(digit_off)
        struct.pack_into("<Q", blob, tag_off, 0)
    elif value < (1 << 30):
        blob = bytearray(digit_off + digit_sz)
        struct.pack_into("<Q", blob, tag_off, 8)  # size=1
        struct.pack_into("<I", blob, digit_off, value)
    elif value < (1 << 60):
        blob = bytearray(digit_off + 2 * digit_sz)
        struct.pack_into("<Q", blob, tag_off, 16)  # size=2
        struct.pack_into("<I", blob, digit_off, value & 0x3FFFFFFF)
        struct.pack_into("<I", blob, digit_off + digit_sz, value >> 30)
    else:
        raise ValueError("value too large for test helper")
    reader.add(addr, bytes(blob))


def build_code_object(reader, addr, name_addr, filename_addr,
                      firstlineno, linetable_addr):
    """Write a minimal CodeObject with name/filename/firstlineno/linetable."""
    end = offsets.get("CodeObject.co_code_adaptive") + 8
    blob = bytearray(end)
    struct.pack_into("<i", blob, offsets.get("CodeObject.co_firstlineno"), firstlineno)
    struct.pack_into("<Q", blob, offsets.get("CodeObject.co_filename"), filename_addr)
    struct.pack_into("<Q", blob, offsets.get("CodeObject.co_name"), name_addr)
    struct.pack_into("<Q", blob, offsets.get("CodeObject.co_qualname"), name_addr)
    struct.pack_into("<Q", blob, offsets.get("CodeObject.co_linetable"), linetable_addr)
    reader.add(addr, bytes(blob))


def build_frame(reader, addr, code_addr, previous_addr, prev_instr):
    """Write an InterpreterFrame with f_code/previous/prev_instr."""
    frame_sz = offsets.get("InterpreterFrame.prev_instr") + PTR_SIZE
    blob = bytearray(frame_sz)
    struct.pack_into("<Q", blob, offsets.get("InterpreterFrame.f_code"), code_addr)
    struct.pack_into("<Q", blob, offsets.get("InterpreterFrame.previous"), previous_addr)
    struct.pack_into("<Q", blob, offsets.get("InterpreterFrame.prev_instr"), prev_instr)
    reader.add(addr, bytes(blob))


# ---------------------------------------------------------------------------
# Linetable byte builders (PEP 626)
# ---------------------------------------------------------------------------

def linetable_no_line(num_entries=8):
    """A linetable where every entry is code=15 (no line info).

    ``addr2line`` will fall back to *firstlineno* for any lasti.
    Each first byte has bit 7 set (PEP 626 entry marker).
    """
    return bytes([0xFF] * num_entries)  # code=15, len_code=7, bit7 set → 16 bytes each


def linetable_simple_increments():
    """A linetable mapping consecutive 2-byte ranges to increasing lines.

    Entries (firstlineno assumed 10):
      lasti [0,2)  → line 11  (code=11, +1)
      lasti [2,4)  → line 13  (code=12, +2)
      lasti [4,6)  → line 13  (code=10, +0)
      lasti [6,8)  → line 13  (code=10, +0)
    """
    return bytes([
        (11 << 3) | 0x80,  # code=11, len_code=0 → 2 bytes, +1
        (12 << 3) | 0x80,  # code=12, len_code=0 → 2 bytes, +2
        (10 << 3) | 0x80,  # code=10, len_code=0 → 2 bytes, +0
        (10 << 3) | 0x80,  # code=10, len_code=0 → 2 bytes, +0
    ])


def linetable_with_no_line():
    """A linetable containing a code=15 (no line) entry in the middle.

    Entries (firstlineno assumed 10):
      lasti [0,2)  → line 11  (code=11, +1)
      lasti [2,4)  → no line  (code=15) → falls back to firstlineno
      lasti [4,6)  → line 12  (code=11, +1)
    """
    return bytes([
        (11 << 3) | 0x80,  # code=11, +1
        (15 << 3) | 0x80,  # code=15, no line
        (11 << 3) | 0x80,  # code=11, +1
    ])


# ---------------------------------------------------------------------------
# FakeRemoteReader for memory-cache tests
# ---------------------------------------------------------------------------

class FakeRemoteReader(RemoteReader):
    """Real RemoteReader with _read_syscall stubbed to serve canned pages."""

    def __init__(self, pages=None):
        super().__init__(pid=0)
        self._pages = dict(pages) if pages else {}
        self.syscall_count = 0

    def _read_syscall(self, addr, length):
        self.syscall_count += 1
        out = bytearray()
        pos = addr
        end = addr + length
        while pos < end:
            page_base = pos & ~PAGE_MASK
            page = self._pages.get(page_base)
            if page is None:
                return None if not out else bytes(out)
            off = pos - page_base
            chunk_end = min(end, page_base + len(page))
            n = chunk_end - pos
            if n <= 0:
                # page exhausted (short read) — stop like a real syscall
                return None if not out else bytes(out)
            out += page[off: off + n]
            pos = chunk_end
        return bytes(out)
