"""Typed readers for scalar CPython objects (PyLong/PyBytes/PyUnicode).

All functions take an explicit ``view`` (kernel/views.py) and ``layout``
(target/layout.py) — version divergence is probed via ``layout.get_or``
(契约 O2), never via global state (ADR A3/A1).

契约（contracts.md 批 3 表 O1–O11）。
"""

import struct

#: Sanity bound for remote object sizes (契约 O6/O7; 从旧 memory.py 迁入——
#: 它是远程对象尺寸的合理性上界，属对象层策略而非传输层).
MAX_STR_LEN = 1 << 20

#: PyUnicode kind → (encoding, bytes per code unit), per CPython
#: unicodeobject.h (O10/O11).
_UNICODE_KINDS = {
    1: ("latin-1", 1),
    2: ("utf-16-le", 2),
    4: ("utf-32-le", 4),
}


def read_long(view, layout, addr: int) -> int | None:
    """Read a PyLong (0/1/2 digits supported; O1–O5). None on failure or
    unsupported size."""
    digit_off = layout.get_or("LongObject.long_value.ob_digit",
                              layout.get_or("LongObject.ob_digit"))
    lv_tag_off = layout.get_or("LongObject.long_value.lv_tag")
    if lv_tag_off is not None:
        # O1 — 3.12+: lv_tag packs the digit count in the high bits
        # (sign lives in the low bits; only non-negative values supported).
        tag = view.read_u64(addr + lv_tag_off)
        if tag is None:
            return None
        size = tag >> 3
    else:
        # O2 — 3.11: ob_size is a plain signed digit count.
        size = view.read_u64(addr + layout.get("LongObject.ob_size"))
        if size is None:
            return None
    if size == 0:
        return 0  # O3 — empty digit array is the legal value 0, not a failure
    if size == 1:
        # O3 — a failed digit read propagates None (never masquerades as 0).
        return view.read_u32(addr + digit_off)
    if size == 2:
        # O4 — 30-bit digits: d0 | d1<<30; either read failing → None.
        digit_sz = layout.get("digit_size")
        d0 = view.read_u32(addr + digit_off)
        d1 = view.read_u32(addr + digit_off + digit_sz)
        if d0 is None or d1 is None:
            return None
        return d0 | (d1 << 30)
    return None  # O5 — beyond 2 digits is unsupported; don't guess


def read_bytes(view, layout, addr: int) -> bytes | None:
    """Read a PyBytes payload (O6). None on failure/oversize."""
    size = view.read_u64(addr + layout.get("VarObject.ob_size"))
    # O6 — MAX_STR_LEN bounds damaged-memory sizes before issuing the read.
    # (A7 琐碎项: the old `size < 0` dead check is gone — read_u64 is unsigned.)
    if size is None or size > MAX_STR_LEN:
        return None
    data = view.read(addr + layout.get("BytesObject.ob_sval"), size)
    if data is None:
        return None
    return data


def read_unicode(view, layout, addr: int) -> str | None:
    """Read a PyUnicode (ascii/compact/non-compact variants; O7–O11).
    None on failure, undecodable bytes replaced."""
    ascii_sz = layout.get("PyASCIIObject_size")
    raw = view.read(addr, ascii_sz)
    if raw is None or len(raw) < ascii_sz:
        return None  # O7 — header unreadable/short

    # PyASCIIObject fixed prefix (no layout key — stable across 3.11–3.14):
    # length @16 (signed), state byte @32.
    length = struct.unpack_from("<q", raw, 16)[0]
    if length < 0 or length > MAX_STR_LEN:
        return None  # O7 — damaged length

    state = raw[32]
    compact = bool((state >> 5) & 1)   # O8 — bit5
    is_ascii = bool((state >> 6) & 1)  # O8 — bit6
    kind = (state >> 2) & 0x07         # O8 — bits2-4

    if compact and is_ascii:
        # O9 — data inline right after the ASCII header.
        data = view.read(addr + ascii_sz, length)
        if data is None:
            return None
        return data.decode("ascii", "replace")

    if compact:
        # O10 — data inline after the compact header, decoded per kind.
        data_addr = addr + layout.get("PyCompactUnicodeObject_size")
        return _decode_unicode_body(view, data_addr, kind, length)

    # O11 — non-compact: data lives behind the data_any pointer;
    # NULL means the string data was never materialized.
    data_ptr = view.read_ptr(addr + layout.get("PyUnicodeObject.data_any"))
    if data_ptr is None or data_ptr == 0:
        return None
    return _decode_unicode_body(view, data_ptr, kind, length)


def _decode_unicode_body(view, data_addr: int, kind: int,
                         length: int) -> str | None:
    """Decode ``length`` code units of the given unicode kind (O10/O11)."""
    info = _UNICODE_KINDS.get(kind)
    if info is None:
        return None  # O10 — reserved/corrupt kind; don't guess
    encoding, unit = info
    data = view.read(data_addr, length * unit)
    if data is None:
        return None
    return data.decode(encoding, "replace")

