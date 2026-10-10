"""InterpreterState / ThreadState graph traversal (A3: layout-bound).

契约（contracts.md 批 3 表 R1–R6）。
"""

from dataclasses import dataclass

from ..errors import NoInterpreterState, NoThreadState

#: Runaway thread-chain guard (契约 R3, 复刻约束).
MAX_THREADS = 256


@dataclass(frozen=True)
class ThreadStateRef:
    """One node of the interpreter's tstate linked list (契约 R3)."""

    tstate_addr: int
    thread_id: int
    native_tid: int


def resolve_interpreter(view, layout, runtime_addr: int) -> int:
    """Interpreter address: main → head fallback (R5).

    Raises ``NoInterpreterState`` when both reads fail or return 0.
    """
    base = runtime_addr + layout.get("RuntimeState.interpreters")
    main = view.read_ptr(base + layout.get("pyinterpreters.main"))
    if main:
        return main  # R5 — main preferred (covers unreadable-main too)
    head = view.read_ptr(base + layout.get("pyinterpreters.head"))
    if head:
        return head  # R5 — head fallback (main unpublished/teardown window)
    raise NoInterpreterState()  # R5 — both failed or zero


def resolve_trampoline(view, layout, interp_addr: int) -> int:
    """Interpreter trampoline address — 3.12 only (R6).

    Returns 0 when the layout lacks the field (3.11/3.13+) or the read
    fails; 0 means "no trampoline frames to skip".
    """
    tramp_off = layout.get_or("InterpreterState.interpreter_trampoline")
    if tramp_off is None:
        return 0  # R6 — field only exists in 3.12
    addr = view.read_ptr(interp_addr + tramp_off)
    if addr is None:
        return 0  # R6 — read failure degrades to "no trampoline"
    return addr


def read_thread_chain(view, layout, interp_addr: int) -> list[ThreadStateRef]:
    """Walk the tstate linked list (R1–R3).

    Raises ``NoThreadState`` when threads.head is unreadable; truncates
    silently at the first unreadable node or the MAX_THREADS cap.
    """
    tstate_addr = view.read_ptr(
        interp_addr + layout.get("InterpreterState.threads")
        + layout.get("pythreads.head"))
    if tstate_addr is None:
        raise NoThreadState("failed to read threads.head")  # R1

    next_off = layout.get("ThreadState.next")
    tid_off = layout.get("ThreadState.thread_id")
    ntid_off = layout.get("ThreadState.native_thread_id")
    # R2 — one merged span per node covering all three fields
    # (thread_id sits between next and native_thread_id in every layout).
    ts_lo = min(next_off, ntid_off)
    ts_hi = max(next_off, ntid_off) + 8
    ts_span = ts_hi - ts_lo

    threads = []
    while tstate_addr != 0 and len(threads) < MAX_THREADS:  # R3 — cap
        buf = view.read(tstate_addr + ts_lo, ts_span)
        if buf is None:
            break  # R2 — truncate at the first unreadable node
        next_addr = int.from_bytes(
            buf[next_off - ts_lo: next_off - ts_lo + 8], "little")
        thread_id = int.from_bytes(
            buf[tid_off - ts_lo: tid_off - ts_lo + 8], "little")
        native_tid = int.from_bytes(
            buf[ntid_off - ts_lo: ntid_off - ts_lo + 8], "little")
        threads.append(ThreadStateRef(tstate_addr=tstate_addr,
                                      thread_id=thread_id,
                                      native_tid=native_tid))
        tstate_addr = next_addr
    return threads


def current_frame_of(view, layout, tstate_addr: int) -> int:
    """Top InterpreterFrame address of a thread (R4).

    Probes the layout: ``ThreadState.current_frame`` direct field
    (3.13/3.14) vs ``ThreadState.cframe`` → ``CFrame.current_frame``
    indirection (3.11/3.12). 0 when the thread has no frame or reads fail.
    """
    direct_off = layout.get_or("ThreadState.current_frame")
    if direct_off is not None:
        # R4 — 3.13 removed the cframe indirection: direct field.
        return view.read_ptr(tstate_addr + direct_off) or 0
    # R4 — 3.11/3.12: cframe → CFrame.current_frame indirection.
    cframe_addr = view.read_ptr(tstate_addr + layout.get("ThreadState.cframe"))
    if not cframe_addr:
        return 0  # unreadable or NULL cframe — thread has no frame
    return (view.read_ptr(
        cframe_addr + layout.get("CFrame.current_frame")) or 0)
