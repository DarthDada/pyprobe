"""Python stack dump: walk _PyRuntime -> interp -> threads -> frames."""

import os

from .memory import RemoteReader, PTR_SIZE, MAX_STR_LEN
from . import offsets
from .elf import find_symbol, read_const, read_cmdline, decode_py_version
from .pyobject import read_pyunicode
from .linetable import addr2line
from .thread_names import get_thread_names
from .dict_iter import DictIter

MAX_FRAMES = 100
MAX_THREADS = 256


def dump_frames(reader, frame_addr, trampoline_addr):
    idx = 0
    for _ in range(MAX_FRAMES):
        if frame_addr == 0:
            break

        frame_sz = (offsets.get("InterpreterFrame.prev_instr") + PTR_SIZE)
        raw = reader.read(frame_addr, frame_sz)
        if raw is None:
            break

        f_code = int.from_bytes(raw[0:8], "little")
        previous = int.from_bytes(raw[8:16], "little")
        prev_instr = int.from_bytes(raw[56:64], "little")

        if f_code == 0:
            frame_addr = previous
            continue

        if trampoline_addr != 0 and f_code == trampoline_addr:
            frame_addr = previous
            continue

        co_lo = min(offsets.get("CodeObject.co_firstlineno"),
                    offsets.get("CodeObject.co_qualname"))
        co_hi = max(offsets.get("CodeObject.co_firstlineno"),
                    offsets.get("CodeObject.co_qualname")) + PTR_SIZE
        co_span = co_hi - co_lo
        co_buf = reader.read(f_code + co_lo, co_span)
        if co_buf is None:
            break

        firstlineno = int.from_bytes(
            co_buf[offsets.get("CodeObject.co_firstlineno") - co_lo:
                   offsets.get("CodeObject.co_firstlineno") - co_lo + 4],
            "little", signed=True)
        co_qualname_addr = int.from_bytes(
            co_buf[offsets.get("CodeObject.co_qualname") - co_lo:
                   offsets.get("CodeObject.co_qualname") - co_lo + 8],
            "little")
        co_filename_addr = int.from_bytes(
            co_buf[offsets.get("CodeObject.co_filename") - co_lo:
                   offsets.get("CodeObject.co_filename") - co_lo + 8],
            "little")
        co_name_addr = int.from_bytes(
            co_buf[offsets.get("CodeObject.co_name") - co_lo:
                   offsets.get("CodeObject.co_name") - co_lo + 8],
            "little")

        qualname = read_pyunicode(reader, co_qualname_addr) if co_qualname_addr else None
        filename = read_pyunicode(reader, co_filename_addr) if co_filename_addr else None
        name = read_pyunicode(reader, co_name_addr) if co_name_addr else None

        code_base = f_code + offsets.get("CodeObject.co_code_adaptive")
        lasti = prev_instr - code_base
        if lasti < 0:
            lasti = 0

        line = addr2line(reader, f_code, lasti, firstlineno)

        print(f"  #{idx} {name or '?'} ({filename or '?'}:{line})  [qualname={qualname or '?'}]")
        idx += 1
        frame_addr = previous


def dump_thread(reader, tstate_addr, native_tid, thread_id, name, trampoline_addr):
    cframe_addr = reader.read_ptr(tstate_addr + offsets.get("ThreadState.cframe"))
    current_frame = 0
    if cframe_addr:
        current_frame = reader.read_ptr(cframe_addr + offsets.get("CFrame.current_frame"))

    if name:
        print(f'Thread {native_tid}: "{name}"')
    else:
        print(f"Thread {native_tid}")

    if current_frame == 0:
        print("  (no Python frame — thread may be in C code or idle)")
    else:
        dump_frames(reader, current_frame, trampoline_addr)
    print()


def dump_python(pid):
    exe_path = os.readlink(f"/proc/{pid}/exe")

    runtime_addr = find_symbol(exe_path, "_PyRuntime", pid)
    if runtime_addr == 0:
        print("[!] cannot find _PyRuntime symbol")
        return 1

    version_str = "?"
    py_version = read_const(exe_path, "Py_Version", 8)
    if py_version is not None:
        version_str = decode_py_version(int.from_bytes(py_version, "little"))

    cmdline = read_cmdline(pid)
    if cmdline:
        print(f"Process {pid}: {cmdline}")
    else:
        print(f"Process {pid}: {exe_path}")
    print(f"Python v{version_str} ({exe_path})\n")

    reader = RemoteReader(pid)

    interp_addr = reader.read_ptr(
        runtime_addr + offsets.get("RuntimeState.interpreters")
        + offsets.get("pyinterpreters.main"))
    if interp_addr is None or interp_addr == 0:
        interp_addr = reader.read_ptr(
            runtime_addr + offsets.get("RuntimeState.interpreters")
            + offsets.get("pyinterpreters.head"))
    if interp_addr is None or interp_addr == 0:
        print("[!] no interpreter state")
        return 1

    trampoline_addr = reader.read_ptr(
        interp_addr + offsets.get("InterpreterState.interpreter_trampoline"))
    if trampoline_addr is None:
        trampoline_addr = 0

    tstate_addr = reader.read_ptr(
        interp_addr + offsets.get("InterpreterState.threads")
        + offsets.get("pythreads.head"))
    if tstate_addr is None:
        print("[!] failed to read threads.head")
        return 1

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

    names = get_thread_names(reader, interp_addr)
    for t in threads:
        if t["thread_id"] in names:
            t["name"] = names[t["thread_id"]]

    threads.sort(key=lambda t: t["native_tid"])

    for t in threads:
        dump_thread(reader, t["tstate_addr"], t["native_tid"],
                    t["thread_id"], t["name"], trampoline_addr)

    return 0
