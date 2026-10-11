"""Single-shot stack collection + idle heuristics.

One ``collect_snapshot`` call = one observation: a fresh ``SnapshotView``
(ADR A4), the thread chain, and a frame walk per thread. ``NoThreadState``
propagates unchanged (SN1) — translating it into ``ProcessExited`` is the
sampler's policy (SA2), not the snapshot's.

契约（contracts.md 批 4 表 SN1–SN5）。当前为骨架：函数返回哨兵，
批次 4 实现填实。
"""

from .. import dto
from ..cpython.frames import walk_frames
from ..cpython.runtime import (
    ThreadStateRef,
    current_frame_of,
    read_thread_chain,
)
from ..kernel.procfs import read_stat_state


def collect_snapshot(session) -> list[dto.ThreadInfo]:
    """Snapshot all threads of the session's target (SN1/SN2).

    Names are applied by thread_id; threads are sorted by native_tid
    (复刻约束 线程升序).
    """
    # SN1 (A4) — a fresh view per call: one observation, one snapshot of
    # target memory. NoThreadState propagates unchanged to the caller.
    view = session.view_factory(session.pid)
    trefs = read_thread_chain(view, session.layout, session.interp_addr)
    threads = [build_thread(session, view, tref) for tref in trefs]
    threads.sort(key=lambda t: t.native_tid)  # SN2 — 复刻约束 线程升序
    return threads


def build_thread(session, view, tref: ThreadStateRef, *,
                 idle_hint: bool = False) -> dto.ThreadInfo:
    """Build a ThreadInfo from one tstate reference (SN3).

    ``idle_hint=True`` skips the frame walk entirely (the pruning entry
    point for samplers that already know the thread is idle).
    """
    name = session.names.get(tref.thread_id, "")  # SN2 — names by thread_id
    if idle_hint:
        # SN3 — pruned: no frame walk, thread reported idle with no frames.
        return dto.ThreadInfo(native_tid=tref.native_tid,
                              thread_id=tref.thread_id, name=name,
                              frames=[], idle=True)

    frame_addr = current_frame_of(view, session.layout, tref.tstate_addr)
    frames = (walk_frames(view, session.layout, frame_addr,
                          session.trampoline_addr)
              if frame_addr else [])
    # SN4 — idle double heuristic: /proc stat state OR top-frame idiom.
    idle = (is_thread_idle_by_stat(session.pid, tref.native_tid)
            or is_thread_idle_by_frames(frames))
    return dto.ThreadInfo(native_tid=tref.native_tid,
                          thread_id=tref.thread_id, name=name,
                          frames=frames, idle=idle)


def is_thread_idle_by_stat(pid: int, native_tid: int) -> bool:
    """True when the thread is not in 'R' state (SN4).

    Reads ``/proc/<pid>/task/<tid>/stat`` via procfs.read_stat_state.
    Returns False on any error (conservative: never suppress a thread we
    could not check).
    """
    state = read_stat_state(pid, native_tid)  # SN5 — P6 parsing lives in procfs
    if state is None:
        return False  # SN4 — conservative on read/parse failure
    return state != "R"


def is_thread_idle_by_frames(frames: list[dto.FrameInfo]) -> bool:
    """True when the top frame matches a known idle idiom (SN4).

    Pure function — no I/O. wait/threading.py, select/selectors.py,
    poll/(asyncore|zmq|gevent|tornado) (复刻约束 空闲双启发式).
    """
    if not frames:
        return False  # SN4 — no evidence, not idle

    top = frames[0]  # SN4 — only the top frame is consulted
    name = top.name
    filename = top.filename
    if not filename:
        return False  # SN4 — no evidence, not idle
    if name == "wait" and filename.endswith("threading.py"):
        return True  # lock wait
    if name == "select" and filename.endswith("selectors.py"):
        return True  # event-loop wait
    if name == "poll" and (
        filename.endswith("asyncore.py")
        or "zmq" in filename
        or "gevent" in filename
        or "tornado" in filename
    ):
        return True  # async framework poll
    return False
