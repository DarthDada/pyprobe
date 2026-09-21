"""Readers for CPython object types from remote process memory."""

import struct

from .memory import PTR_SIZE, MAX_STR_LEN
from . import offsets

_UNICODE_KINDS = {
    1: ("latin-1", 1),
    2: ("utf-16-le", 2),
    4: ("utf-32-le", 4),
}


def _decode_unicode_body(reader, data_addr, kind, length):
    info = _UNICODE_KINDS.get(kind)
    if info is None:
        return None
    encoding, unit = info
    data = reader.read(data_addr, length * unit)
    if data is None:
        return None
    return data.decode(encoding, "replace")


def read_pylong(reader, addr):
    digit_off = offsets.get_or("LongObject.long_value.ob_digit",
                               offsets.get_or("LongObject.ob_digit"))
    lv_tag_off = offsets.get_or("LongObject.long_value.lv_tag")
    if lv_tag_off is not None:
        # 3.12+: lv_tag packs the digit count in the high bits, sign in low
        tag = reader.read_u64(addr + lv_tag_off)
        if tag is None:
            return None
        size = tag >> 3
    else:
        # 3.11: ob_size is a plain signed digit count (sign = int sign)
        size = reader.read_u64(addr + offsets.get("LongObject.ob_size"))
        if size is None:
            return None
    if size == 0:
        return 0
    if size == 1:
        d = reader.read_u32(addr + digit_off)
        return d
    if size == 2:
        digit_sz = offsets.get("digit_size")
        d0 = reader.read_u32(addr + digit_off)
        d1 = reader.read_u32(addr + digit_off + digit_sz)
        if d0 is None or d1 is None:
            return None
        return d0 | (d1 << 30)
    return None


def read_pybytes(reader, addr):
    ob_size_off = offsets.get("VarObject.ob_size")
    size = reader.read_u64(addr + ob_size_off)
    if size is None or size < 0 or size > MAX_STR_LEN:
        return None
    sval_off = offsets.get("BytesObject.ob_sval")
    data = reader.read(addr + sval_off, size)
    if data is None:
        return None
    return data


def read_pyunicode(reader, addr):
    ascii_sz = offsets.get("PyASCIIObject_size")
    raw = reader.read(addr, ascii_sz)
    if raw is None or len(raw) < ascii_sz:
        return None

    length = struct.unpack_from("<q", raw, 16)[0]
    if length < 0 or length > MAX_STR_LEN:
        return None

    state_byte = raw[32]
    compact = bool((state_byte >> 5) & 1)
    is_ascii = bool((state_byte >> 6) & 1)
    kind = (state_byte >> 2) & 0x07

    if compact and is_ascii:
        data_addr = addr + ascii_sz
        data = reader.read(data_addr, length)
        if data is None:
            return None
        return data.decode("ascii", "replace")

    if compact:
        compact_sz = offsets.get("PyCompactUnicodeObject_size")
        data_addr = addr + compact_sz
        return _decode_unicode_body(reader, data_addr, kind, length)

    raw_full = reader.read(addr, offsets.get("PyUnicodeObject.data_any") + PTR_SIZE)
    if raw_full is None:
        return None
    data_ptr = struct.unpack_from("<Q", raw_full,
                                  offsets.get("PyUnicodeObject.data_any"))[0]
    if data_ptr == 0:
        return None
    return _decode_unicode_body(reader, data_ptr, kind, length)
