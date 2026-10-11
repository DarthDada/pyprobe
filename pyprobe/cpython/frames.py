"""InterpreterFrame chain walking (A3: layout-bound, zero naked offsets).

契约（contracts.md 批 3 表 F1–F8）。
"""

from .. import dto
from ..kernel.views import PTR_SIZE
from .code import addr2line, read_code_header
from .objects import read_unicode

#: Circular-chain / runaway-frame guard (契约 F2, 复刻约束).
MAX_FRAMES = 100


def walk_frames(view, layout, frame_addr: int,
                trampoline_addr: int) -> list[dto.FrameInfo]:
    """Walk the InterpreterFrame chain, innermost first (F1–F8).

    Stops at: null frame address, unreadable frame, MAX_FRAMES cap, or a
    stale tail entry (name and filename both unreadable — 3.13 datastack
    leftovers whose f_executable points at a recycled code object, F6).
    """
    frames = []

    fc_off = layout.get("InterpreterFrame.f_code")
    prev_off = layout.get("InterpreterFrame.previous")
    pi_off = layout.get("InterpreterFrame.prev_instr")
    frame_sz = pi_off + PTR_SIZE  # F1 — frame field-cluster span

    co_code_adaptive = layout.get("CodeObject.co_code_adaptive")

    for _ in range(MAX_FRAMES):  # F2 — circular-chain cap
        if frame_addr == 0:
            break  # F2 — chain end

        # F1 — one merged read of f_code/previous/prev_instr.
        raw = view.read(frame_addr, frame_sz)
        if raw is None:
            break  # F1 — unreadable frame: truncate

        f_code = int.from_bytes(raw[fc_off:fc_off + PTR_SIZE], "little")
        previous = int.from_bytes(raw[prev_off:prev_off + PTR_SIZE], "little")
        prev_instr = int.from_bytes(raw[pi_off:pi_off + PTR_SIZE], "little")

        if f_code == 0:
            frame_addr = previous
            continue  # F3 — placeholder entry without a code object

        if trampoline_addr != 0 and f_code == trampoline_addr:
            frame_addr = previous
            continue  # F4 — 3.12 interpreter trampoline is not user stack

        header = read_code_header(view, layout, f_code)
        if header is None:
            break  # F5 — code header unreadable: truncate, keep collected

        filename = (read_unicode(view, layout, header.filename_addr)
                    if header.filename_addr else None)
        name = (read_unicode(view, layout, header.name_addr)
                if header.name_addr else None)

        # C6 — instruction offset into co_code_adaptive, clamped at 0.
        lasti = prev_instr - (f_code + co_code_adaptive)
        if lasti < 0:
            lasti = 0
        line = addr2line(view, layout, f_code, lasti,
                         header.firstlineno)  # F8

        if name is None and filename is None:
            # F6 — stale entry at the end of the frame chain (e.g. 3.13
            # datastack leftovers whose f_executable points at a recycled
            # code object).
            break

        frames.append(dto.FrameInfo(name=name, filename=filename, line=line))
        frame_addr = previous

    return frames  # F7 — innermost first
