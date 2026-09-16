"""PEP 626 line-table resolution."""


def addr2line(reader, code_addr, lasti, firstlineno):
    from . import offsets
    from .pyobject import read_pybytes

    lt_addr = reader.read_ptr(code_addr + offsets.get("CodeObject.co_linetable"))
    if lt_addr is None:
        return firstlineno

    linetable = read_pybytes(reader, lt_addr)
    if linetable is None:
        return firstlineno

    ar_start = -1
    ar_end = 0
    computed_line = firstlineno
    ar_line = -1

    pos = 0
    lt_len = len(linetable)

    while pos < lt_len and ar_end <= lasti:
        first_byte = linetable[pos]
        code = (first_byte >> 3) & 15

        if code == 15:
            ldelta = 0
        elif code in (13, 14):
            p = pos + 1
            if p >= lt_len:
                ldelta = 0
            else:
                uval = linetable[p] & 63
                shift = 0
                while p < lt_len and (linetable[p] & 64):
                    p += 1
                    shift += 6
                    if p < lt_len:
                        uval |= (linetable[p] & 63) << shift
                ldelta = -(uval >> 1) if (uval & 1) else (uval >> 1)
        elif code == 10:
            ldelta = 0
        elif code == 11:
            ldelta = 1
        elif code == 12:
            ldelta = 2
        else:
            ldelta = 0

        computed_line += ldelta

        if code == 15:
            ar_line = -1
        else:
            ar_line = computed_line

        ar_start = ar_end
        ar_end += ((first_byte & 7) + 1) * 2

        pos += 1
        while pos < lt_len and (linetable[pos] & 128) == 0:
            pos += 1

    return ar_line if ar_line != -1 else firstlineno
