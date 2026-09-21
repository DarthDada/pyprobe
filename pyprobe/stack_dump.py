"""Python stack dump: walk _PyRuntime -> interp -> threads -> frames.

Layered design (issue #8):

* ``collect_python(pid)`` — pure data collection; returns
  ``(ProcessInfo, list[ThreadInfo])`` and raises ``PyProbeError`` subclasses
  on failure.  No printing.
* ``format_process(proc_info, threads)`` — turns collected data into the
  human-readable string used by the CLI.
* ``dump_python(pid)`` — thin CLI wrapper: collect + format + print, returns
  an exit code.

``collect_frames`` and ``collect_thread`` are exposed for unit testing with a
mock ``RemoteReader``.
"""

import os
import sys
from dataclasses import asdict
from typing import Optional

from .memory import RemoteReader, PTR_SIZE, MAX_STR_LEN
from . import offsets
from .colors import red, should_color
from .elf import find_symbol, read_const, read_cmdline, decode_py_version
from .pyobject import read_pyunicode
from .linetable import addr2line
from .thread_names import get_thread_names
from .types import FrameInfo, ThreadInfo, ProcessInfo
from .errors import (
    ProcessNotFound, SymbolNotFound, NoInterpreterState, NoThreadState,
)

MAX_FRAMES = 100
MAX_THREADS = 256


def collect_frames(reader, frame_addr, trampoline_addr):
    """Walk the InterpreterFrame chain and return a list of FrameInfo.

    ``reader`` is any object exposing ``read(addr, length) -> bytes|None``
    and ``read_ptr(addr) -> int|None`` (i.e. a ``RemoteReader`` or a test
    double).
    """
    frames = []

    fc_off = offsets.get("InterpreterFrame.f_code")
    prev_off = offsets.get("InterpreterFrame.previous")
    pi_off = offsets.get("InterpreterFrame.prev_instr")
    frame_sz = pi_off + PTR_SIZE

    co_firstlineno = offsets.get("CodeObject.co_firstlineno")
    co_qualname = offsets.get("CodeObject.co_qualname")
    co_filename = offsets.get("CodeObject.co_filename")
    co_name = offsets.get("CodeObject.co_name")
    co_code_adaptive = offsets.get("CodeObject.co_code_adaptive")

    co_lo = min(co_firstlineno, co_qualname)
    co_hi = max(co_firstlineno, co_qualname) + PTR_SIZE
    co_span = co_hi - co_lo

    for _ in range(MAX_FRAMES):
        if frame_addr == 0:
            break

        raw = reader.read(frame_addr, frame_sz)
        if raw is None:
            break

        f_code = int.from_bytes(raw[fc_off:fc_off + PTR_SIZE], "little")
        previous = int.from_bytes(raw[prev_off:prev_off + PTR_SIZE], "little")
        prev_instr = int.from_bytes(raw[pi_off:pi_off + PTR_SIZE], "little")

        if f_code == 0:
            frame_addr = previous
            continue

        if trampoline_addr != 0 and f_code == trampoline_addr:
            frame_addr = previous
            continue

        co_buf = reader.read(f_code + co_lo, co_span)
        if co_buf is None:
            break

        firstlineno = int.from_bytes(
            co_buf[co_firstlineno - co_lo:co_firstlineno - co_lo + 4],
            "little", signed=True)
        co_filename_addr = int.from_bytes(
            co_buf[co_filename - co_lo:co_filename - co_lo + PTR_SIZE],
            "little")
        co_name_addr = int.from_bytes(
            co_buf[co_name - co_lo:co_name - co_lo + PTR_SIZE],
            "little")

        filename = read_pyunicode(reader, co_filename_addr) if co_filename_addr else None
        name = read_pyunicode(reader, co_name_addr) if co_name_addr else None

        code_base = f_code + co_code_adaptive
        lasti = prev_instr - code_base
        if lasti < 0:
            lasti = 0

        line = addr2line(reader, f_code, lasti, firstlineno)

        if name is None and filename is None:
            # Stale entry at the end of the frame chain (e.g. 3.13 datastack
            # leftovers whose f_executable points at a recycled code object).
            break

        frames.append(FrameInfo(name=name, filename=filename, line=line))
        frame_addr = previous

    return frames


def _is_thread_idle_by_stat(pid, native_tid):
    """Return True if the thread is not in 'R' (running) state.

    Reads ``/proc/<pid>/task/<tid>/stat``.  Returns False on any error
    (conservative: don't suppress a thread just because we couldn't read it).
    """
    try:
        with open(f"/proc/{pid}/task/{native_tid}/stat") as f:
            stat = f.read()
        comm_end = stat.rfind(")")
        state = stat[comm_end + 2]
        if state != "R":
            return True
    except (OSError, IndexError):
        pass
    return False


def _is_thread_idle_by_frames(frames):
    """Return True if the top frame looks like a known idle idiom.

    Pure function — no I/O — so it is directly unit-testable.
    """
    if not frames:
        return False

    top = frames[0]
    name = top.name
    filename = top.filename
    if not filename:
        return False
    if name == "wait" and filename.endswith("threading.py"):
        return True
    if name == "select" and filename.endswith("selectors.py"):
        return True
    if name == "poll" and (
        filename.endswith("asyncore.py")
        or "zmq" in filename
        or "gevent" in filename
        or "tornado" in filename
    ):
        return True
    return False


def _is_thread_idle(pid, native_tid, frames):
    return _is_thread_idle_by_stat(pid, native_tid) or _is_thread_idle_by_frames(frames)


def collect_thread(reader, pid, tstate_addr, native_tid, name, trampoline_addr,
                   idle_hint=False):
    """Build a ThreadInfo from a remote tstate address (no printing).

    ``idle_hint=True`` skips the frame walk entirely and returns an idle
    ThreadInfo with empty frames — the pruning entry point for samplers
    that already know the thread is idle (e.g. via /proc stat state).
    """
    if idle_hint:
        return ThreadInfo(
            native_tid=native_tid,
            name=name,
            frames=[],
            idle=True,
        )

    # 3.13 removed the cframe indirection: current_frame is a direct field.
    current_frame = 0
    cf_direct = offsets.get_or("ThreadState.current_frame")
    if cf_direct is not None:
        current_frame = reader.read_ptr(tstate_addr + cf_direct)
    else:
        cframe_addr = reader.read_ptr(
            tstate_addr + offsets.get("ThreadState.cframe"))
        if cframe_addr:
            current_frame = reader.read_ptr(
                cframe_addr + offsets.get("CFrame.current_frame"))

    frames = collect_frames(reader, current_frame, trampoline_addr) if current_frame else []
    idle = _is_thread_idle(pid, native_tid, frames)

    return ThreadInfo(
        native_tid=native_tid,
        name=name,
        frames=frames,
        idle=idle,
    )


def _read_thread_chain(reader, interp_addr):
    """Read the tstate linked list; return a list of raw thread dicts."""
    tstate_addr = reader.read_ptr(
        interp_addr + offsets.get("InterpreterState.threads")
        + offsets.get("pythreads.head"))
    if tstate_addr is None:
        raise NoThreadState("failed to read threads.head")

    threads = []
    ts_lo = min(offsets.get("ThreadState.next"),
                offsets.get("ThreadState.native_thread_id"))
    ts_hi = max(offsets.get("ThreadState.next"),
                offsets.get("ThreadState.native_thread_id")) + 8
    ts_span = ts_hi - ts_lo

    while tstate_addr != 0 and len(threads) < MAX_THREADS:
        ts_buf = reader.read(tstate_addr + ts_lo, ts_span)
        if ts_buf is None:
            break
        next_addr = int.from_bytes(
            ts_buf[offsets.get("ThreadState.next") - ts_lo:
                   offsets.get("ThreadState.next") - ts_lo + 8], "little")
        thread_id = int.from_bytes(
            ts_buf[offsets.get("ThreadState.thread_id") - ts_lo:
                   offsets.get("ThreadState.thread_id") - ts_lo + 8], "little")
        native_tid = int.from_bytes(
            ts_buf[offsets.get("ThreadState.native_thread_id") - ts_lo:
                   offsets.get("ThreadState.native_thread_id") - ts_lo + 8], "little")

        threads.append({
            "tstate_addr": tstate_addr,
            "thread_id": thread_id,
            "native_tid": native_tid,
            "name": "",
        })
        tstate_addr = next_addr
    return threads


def collect_python(pid):
    """Collect Python stack data from a running process.

    Returns ``(ProcessInfo, list[ThreadInfo])``.  Raises a ``PyProbeError``
    subclass on failure.

    No output is written to stdout — callers format the result via
    ``format_process`` or inspect the structured data directly.
    """
    try:
        exe_path = os.readlink(f"/proc/{pid}/exe")
    except OSError as e:
        raise ProcessNotFound(pid) from e

    runtime_addr = find_symbol(exe_path, "_PyRuntime", pid)
    if runtime_addr == 0:
        raise SymbolNotFound("_PyRuntime", exe_path)

    version_str = "?"
    py_version = read_const(exe_path, "Py_Version", 8)
    if py_version is not None:
        version_str = decode_py_version(int.from_bytes(py_version, "little"))

    if version_str != "?":
        offsets.configure(version_str)
    else:
        offsets.configure(offsets._DEFAULT_VERSION)
        print(red("[!] Warning: cannot determine target CPython version, "
                  "using default offsets — output may be incorrect.",
                  should_color(sys.stderr)),
              file=sys.stderr)

    cmdline = read_cmdline(pid) or exe_path
    proc_info = ProcessInfo(
        pid=pid, cmdline=cmdline, exe_path=exe_path, python_version=version_str,
    )

    reader = RemoteReader(pid)

    interp_addr = reader.read_ptr(
        runtime_addr + offsets.get("RuntimeState.interpreters")
        + offsets.get("pyinterpreters.main"))
    if interp_addr is None or interp_addr == 0:
        interp_addr = reader.read_ptr(
            runtime_addr + offsets.get("RuntimeState.interpreters")
            + offsets.get("pyinterpreters.head"))
    if interp_addr is None or interp_addr == 0:
        raise NoInterpreterState()

    # The interpreter trampoline only exists in 3.12 (introduced there,
    # removed in 3.13); when absent there are no trampoline frames to skip.
    trampoline_addr = 0
    tramp_off = offsets.get_or("InterpreterState.interpreter_trampoline")
    if tramp_off is not None:
        trampoline_addr = reader.read_ptr(interp_addr + tramp_off)
        if trampoline_addr is None:
            trampoline_addr = 0

    raw_threads = _read_thread_chain(reader, interp_addr)

    names = get_thread_names(reader, interp_addr)
    for t in raw_threads:
        if t["thread_id"] in names:
            t["name"] = names[t["thread_id"]]

    raw_threads.sort(key=lambda t: t["native_tid"])

    threads = [
        collect_thread(reader, pid, t["tstate_addr"], t["native_tid"],
                       t["name"], trampoline_addr)
        for t in raw_threads
    ]
    return proc_info, threads


def format_process(proc_info, threads, *, color: bool = False,
                   verbose: bool = False):
    """Render collected data as the human-readable CLI output string.

    ``verbose=False`` shorts frame filenames to their last two components;
    ``verbose=True`` keeps full paths.
    """
    parts = [proc_info.format_header(color=color)]
    for t in threads:
        parts.append(t.format(color=color, verbose=verbose))
        parts.append("")
    return "\n".join(parts)


def format_process_json(proc_info, threads) -> str:
    """Render collected data as JSON (machine-readable output for --json).

    Data fields always hold full paths (shortening is a display-layer
    concern of ``format_process``), and JSON output is never colored.
    """
    import json
    return json.dumps(
        {"process": asdict(proc_info), "threads": [asdict(t) for t in threads]},
        indent=2, ensure_ascii=False) + "\n"


def dump_python(pid, color: Optional[bool] = None, verbose: bool = False,
                json_output: bool = False):
    """CLI entry point: collect + format + print. Returns exit code.

    ``color``: None (default) auto-detect per stream via clicolors rules;
    True/False force color on/off for both stdout and stderr (ignored
    when ``json_output`` is set — JSON is never colored).
    ``verbose``: keep full frame filename paths instead of shortened ones.
    """
    use_color = should_color(sys.stdout) if color is None else color
    err_color = should_color(sys.stderr) if color is None else color
    try:
        proc_info, threads = collect_python(pid)
    except (ProcessNotFound, SymbolNotFound,
            NoInterpreterState, NoThreadState, OSError) as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    if json_output:
        print(format_process_json(proc_info, threads))
    else:
        print(format_process(proc_info, threads, color=use_color,
                             verbose=verbose))
    return 0
