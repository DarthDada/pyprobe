"""CodeObject header reading and PEP 626 line-table resolution.

Pure/impure split (契约 C1 vs C4): ``line_for_offset`` is a pure function
of the linetable bytes — that is what the spec-oracle test pins against
CPython's own ``co_lines()`` ground truth (C7). ``addr2line`` wraps it
with the remote reads and the firstlineno fallbacks.

契约（contracts.md 批 3 表 C1–C7）。
"""

from dataclasses import dataclass

from ..kernel.views import PTR_SIZE
from .objects import read_bytes


@dataclass(frozen=True)
class CodeHeader:
    """The fields frame walking needs from a CodeObject (契约 C5)."""

    firstlineno: int
    filename_addr: int
    name_addr: int


def read_code_header(view, layout, code_addr: int) -> CodeHeader | None:
    """Read the CodeObject header fields in one span (A3: the co_lo/co_hi
    span arithmetic lives here, not in frames.py). None on read failure."""
    co_firstlineno = layout.get("CodeObject.co_firstlineno")
    co_qualname = layout.get("CodeObject.co_qualname")
    co_filename = layout.get("CodeObject.co_filename")
    co_name = layout.get("CodeObject.co_name")

    # C5 — one span covering firstlineno … qualname also covers
    # filename/name (both sit between the two in every supported version).
    co_lo = min(co_firstlineno, co_qualname)
    co_hi = max(co_firstlineno, co_qualname) + PTR_SIZE
    buf = view.read(code_addr + co_lo, co_hi - co_lo)
    if buf is None:
        return None

    firstlineno = int.from_bytes(
        buf[co_firstlineno - co_lo: co_firstlineno - co_lo + 4],
        "little", signed=True)
    filename_addr = int.from_bytes(
        buf[co_filename - co_lo: co_filename - co_lo + PTR_SIZE], "little")
    name_addr = int.from_bytes(
        buf[co_name - co_lo: co_name - co_lo + PTR_SIZE], "little")
    return CodeHeader(firstlineno=firstlineno, filename_addr=filename_addr,
                      name_addr=name_addr)


def line_for_offset(linetable: bytes, lasti: int, firstlineno: int) -> int:
    """Pure PEP 626 decode: line number for bytecode offset ``lasti``
    (C1–C3). ``firstlineno`` is the fallback for no-line entries."""
    ar_end = 0
    computed_line = firstlineno
    ar_line = -1

    pos = 0
    lt_len = len(linetable)

    # C1 — entry first byte has bit7 set; code=(b>>3)&15; each entry covers
    # ((b&7)+1)*2 code-unit bytes. Continuation bytes (bit7 clear) are
    # skipped after each entry. The ar_end<=lasti loop condition also gives
    # C2: a lasti past the table end resolves to the last computed line.
    while pos < lt_len and ar_end <= lasti:
        first_byte = linetable[pos]
        code = (first_byte >> 3) & 15

        if code == 15:
            ldelta = 0  # no line info for this range
        elif code in (13, 14):
            # Signed line delta: 6-bit varint blocks, bit6 = continuation,
            # zigzag-decoded (C1).
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
            ar_line = -1  # C3 — no-line entry → firstlineno fallback
        else:
            ar_line = computed_line

        ar_end += ((first_byte & 7) + 1) * 2

        pos += 1
        while pos < lt_len and (linetable[pos] & 128) == 0:
            pos += 1  # continuation bytes of a multi-byte entry

    return ar_line if ar_line != -1 else firstlineno


def addr2line(view, layout, code_addr: int, lasti: int,
              firstlineno: int) -> int:
    """Remote wrapper: fetch co_linetable and resolve (C4 fallbacks)."""
    lt_addr = view.read_ptr(code_addr + layout.get("CodeObject.co_linetable"))
    if lt_addr is None:
        return firstlineno  # C4 — linetable pointer unreadable
    linetable = read_bytes(view, layout, lt_addr)
    if linetable is None:
        return firstlineno  # C4 — linetable bytes object unreadable
    return line_for_offset(linetable, lasti, firstlineno)
