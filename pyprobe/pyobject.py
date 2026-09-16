"""Readers for CPython object types from remote process memory."""

import struct

from .memory import RemoteReader, PTR_SIZE, MAX_STR_LEN
from . import offsets


def read_pylong(reader, addr):
    tag = reader.read_u64(addr + offsets.get("LongObject.long_value.lv_tag"))
    if tag is None:
        return None
    size = tag >> 3
    if size == 0:
        return 0
    if size == 1:
        digit_off = offsets.get("LongObject.long_value.ob_digit")
        d = reader.read_u32(addr + digit_off)
        return d
    if size == 2:
        digit_off = offsets.get("LongObject.long_value.ob_digit")
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
        if kind == 1:
            data = reader.read(data_addr, length)
            if data is None:
                return None
            return data.decode("latin-1", "replace")
        elif kind == 2:
            data = reader.read(data_addr, length * 2)
            if data is None:
                return None
            return data.decode("utf-16-le", "replace")
        elif kind == 4:
            data = reader.read(data_addr, length * 4)
            if data is None:
                return None
            return data.decode("utf-32-le", "replace")
        return None

    full_sz = offsets.get("PyCompactUnicodeObject_size")
    raw_full = reader.read(addr, offsets.get("PyUnicodeObject.data_any") + PTR_SIZE)
    if raw_full is None:
        return None
    data_ptr = struct.unpack_from("<Q", raw_full,
                                  offsets.get("PyUnicodeObject.data_any"))[0]
    if data_ptr == 0:
        return None
    if kind == 1:
        data = reader.read(data_ptr, length)
        if data is None:
            return None
        return data.decode("latin-1", "replace")
    elif kind == 2:
        data = reader.read(data_ptr, length * 2)
        if data is None:
            return None
        return data.decode("utf-16-le", "replace")
    elif kind == 4:
        data = reader.read(data_ptr, length * 4)
        if data is None:
            return None
        return data.decode("utf-32-le", "replace")
    return None
