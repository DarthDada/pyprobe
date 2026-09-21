"""Native stack dump via libdwfl (elfutils) using ctypes.

Layered design (issue #8):

* ``collect_native(pid)`` — ptrace attach + DWARF unwind; returns
  ``list[NativeThreadInfo]``.  Raises ``AttachFailed`` on ptrace failure.
* ``format_native(cmdline, threads)`` — render the human-readable output.
* ``dump_native(pid)`` — thin CLI wrapper: collect + format + print.
"""

import ctypes
import json
import os
import sys
from dataclasses import asdict
from typing import Optional

from .colors import red, should_color, yellow_bold
from .procmeta import read_cmdline
from .types import NativeFrame, NativeThreadInfo
from .errors import AttachFailed

Dwarf_Addr = ctypes.c_uint64
pid_t = ctypes.c_int32
Dwfl = ctypes.c_void_p
Dwfl_Module = ctypes.c_void_p
Dwfl_Thread = ctypes.c_void_p
Dwfl_Frame = ctypes.c_void_p
Elf = ctypes.c_void_p
GElf_Word = ctypes.c_uint32
GElf_Addr = ctypes.c_uint64

_find_elf_t = ctypes.CFUNCTYPE(
    ctypes.c_int, Dwfl_Module, ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_char_p, Dwarf_Addr, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(Elf))
_find_debuginfo_t = ctypes.CFUNCTYPE(
    ctypes.c_int, Dwfl_Module, ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_char_p, Dwarf_Addr, ctypes.c_char_p, ctypes.c_char_p, GElf_Word,
    ctypes.POINTER(ctypes.c_char_p))
_section_address_t = ctypes.CFUNCTYPE(
    ctypes.c_int, Dwfl_Module, ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_char_p, Dwarf_Addr, ctypes.c_char_p, GElf_Word,
    ctypes.c_void_p, ctypes.POINTER(Dwarf_Addr))


class Dwfl_Callbacks(ctypes.Structure):
    _fields_ = [
        ("find_elf", _find_elf_t),
        ("find_debuginfo", _find_debuginfo_t),
        ("section_address", _section_address_t),
        ("debuginfo_path", ctypes.POINTER(ctypes.c_char_p)),
    ]


_thread_cb_t = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.POINTER(Dwfl_Thread), ctypes.c_void_p)
_frame_cb_t = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.POINTER(Dwfl_Frame), ctypes.c_void_p)

libdw = None
libc = None


def _init_libs():
    global libdw, libc
    if libdw is not None:
        return
    libdw = ctypes.CDLL("libdw.so.1", use_errno=True)
    libc = ctypes.CDLL("libc.so.6", use_errno=True)

    libdw.dwfl_begin.restype = ctypes.POINTER(Dwfl)
    libdw.dwfl_begin.argtypes = [ctypes.POINTER(Dwfl_Callbacks)]
    libdw.dwfl_end.restype = None
    libdw.dwfl_end.argtypes = [ctypes.POINTER(Dwfl)]
    libdw.dwfl_errmsg.restype = ctypes.c_char_p
    libdw.dwfl_errmsg.argtypes = [ctypes.c_int]
    libdw.dwfl_linux_proc_report.restype = ctypes.c_int
    libdw.dwfl_linux_proc_report.argtypes = [ctypes.POINTER(Dwfl), pid_t]
    libdw.dwfl_report_end.restype = ctypes.c_int
    libdw.dwfl_report_end.argtypes = [ctypes.POINTER(Dwfl), ctypes.c_void_p, ctypes.c_void_p]
    libdw.dwfl_linux_proc_attach.restype = ctypes.c_int
    libdw.dwfl_linux_proc_attach.argtypes = [ctypes.POINTER(Dwfl), pid_t, ctypes.c_bool]
    libdw.dwfl_thread_tid.restype = pid_t
    libdw.dwfl_thread_tid.argtypes = [ctypes.POINTER(Dwfl_Thread)]
    libdw.dwfl_frame_thread.restype = ctypes.POINTER(Dwfl_Thread)
    libdw.dwfl_frame_thread.argtypes = [ctypes.POINTER(Dwfl_Frame)]
    libdw.dwfl_thread_dwfl.restype = ctypes.POINTER(Dwfl)
    libdw.dwfl_thread_dwfl.argtypes = [ctypes.POINTER(Dwfl_Thread)]
    libdw.dwfl_frame_pc.restype = ctypes.c_bool
    libdw.dwfl_frame_pc.argtypes = [ctypes.POINTER(Dwfl_Frame),
                                     ctypes.POINTER(Dwarf_Addr),
                                     ctypes.POINTER(ctypes.c_bool)]
    libdw.dwfl_addrmodule.restype = ctypes.POINTER(Dwfl_Module)
    libdw.dwfl_addrmodule.argtypes = [ctypes.POINTER(Dwfl), Dwarf_Addr]
    libdw.dwfl_module_addrname.restype = ctypes.c_char_p
    libdw.dwfl_module_addrname.argtypes = [ctypes.POINTER(Dwfl_Module), GElf_Addr]
    libdw.dwfl_module_info.restype = ctypes.c_char_p
    libdw.dwfl_module_info.argtypes = [
        ctypes.POINTER(Dwfl_Module),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ctypes.POINTER(Dwarf_Addr), ctypes.POINTER(Dwarf_Addr),
        ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p),
        ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p),
    ]
    libdw.dwfl_getthreads.restype = ctypes.c_int
    libdw.dwfl_getthreads.argtypes = [ctypes.POINTER(Dwfl), _thread_cb_t, ctypes.c_void_p]
    libdw.dwfl_thread_getframes.restype = ctypes.c_int
    libdw.dwfl_thread_getframes.argtypes = [
        ctypes.POINTER(Dwfl_Thread), _frame_cb_t, ctypes.c_void_p]

_MAX_FRAMES = 256

PTRACE_ATTACH = 16
PTRACE_DETACH = 17


def _attach_all_threads(pid):
    tids = []
    for name in os.listdir(f"/proc/{pid}/task"):
        tids.append(int(name))
    tids.sort()
    attached = []
    for tid in tids:
        ret = libc.ptrace(PTRACE_ATTACH, tid, 0, 0)
        if ret == -1:
            continue
        try:
            os.waitpid(tid, 0)
            attached.append(tid)
        except OSError:
            libc.ptrace(PTRACE_DETACH, tid, 0, 0)
    return attached


def _detach_all(tids):
    for tid in tids:
        libc.ptrace(PTRACE_DETACH, tid, 0, 0)


def _read_comm(pid, tid):
    try:
        with open(f"/proc/{pid}/task/{tid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


def _collect_frames(pid):
    """Run the DWARF unwind and return a list of NativeThreadInfo."""
    debuginfo_path = ctypes.c_char_p(None)
    callbacks = Dwfl_Callbacks()
    callbacks.find_elf = _find_elf_t(libdw.dwfl_linux_proc_find_elf)
    callbacks.find_debuginfo = _find_debuginfo_t(libdw.dwfl_standard_find_debuginfo)
    callbacks.section_address = _section_address_t(libdw.dwfl_offline_section_address)
    callbacks.debuginfo_path = ctypes.pointer(debuginfo_path)

    dwfl = libdw.dwfl_begin(ctypes.byref(callbacks))
    if not dwfl:
        raise AttachFailed(pid, f"dwfl_begin failed: {libdw.dwfl_errmsg(-1)}")

    try:
        if libdw.dwfl_linux_proc_report(dwfl, pid) != 0:
            e = ctypes.get_errno()
            raise AttachFailed(pid, f"dwfl_linux_proc_report failed: {os.strerror(e)}")

        libdw.dwfl_report_end(dwfl, None, None)

        if libdw.dwfl_linux_proc_attach(dwfl, pid, True) != 0:
            e = ctypes.get_errno()
            raise AttachFailed(pid, f"dwfl_linux_proc_attach failed: {os.strerror(e)}")

        results = []

        @_frame_cb_t
        def frame_cb(state, arg):
            pc = Dwarf_Addr(0)
            isact = ctypes.c_bool(False)
            if not libdw.dwfl_frame_pc(state, ctypes.byref(pc),
                                        ctypes.byref(isact)):
                return -1
            lookup = pc.value if isact.value else pc.value - 1
            dw = libdw.dwfl_thread_dwfl(libdw.dwfl_frame_thread(state))
            mod = libdw.dwfl_addrmodule(dw, lookup)

            sym = "??"
            modpath = None
            if mod:
                s = libdw.dwfl_module_addrname(mod, lookup)
                if s:
                    sym = s.decode("utf-8", "replace")
                mainfile = ctypes.c_char_p()
                libdw.dwfl_module_info(mod, None, None, None, None, None,
                                        ctypes.byref(mainfile), None)
                if mainfile.value:
                    modpath = mainfile.value.decode("utf-8", "replace")

            r = arg and ctypes.cast(arg, ctypes.POINTER(ctypes.py_object)).contents
            if r is not None:
                frames = r.value
                if len(frames) < _MAX_FRAMES:
                    frames.append(NativeFrame(pc=pc.value, symbol=sym, module=modpath))
            return 0

        @_thread_cb_t
        def thread_cb(thread, arg):
            tid = libdw.dwfl_thread_tid(thread)
            frames = []
            obj = ctypes.py_object(frames)
            fr = libdw.dwfl_thread_getframes(thread, frame_cb,
                                               ctypes.cast(ctypes.pointer(obj),
                                                            ctypes.c_void_p))
            comm = _read_comm(pid, tid)
            results.append(NativeThreadInfo(
                tid=tid, comm=comm, frames=frames,
                unwind_failed=(fr != 0 and len(frames) == 0),
            ))
            return 0

        libdw.dwfl_getthreads(dwfl, thread_cb, None)

        results.sort(key=lambda r: r.tid)
        return results
    finally:
        libdw.dwfl_end(dwfl)


def collect_native(pid):
    """Attach to all threads of ``pid`` and collect native stacks.

    Returns ``list[NativeThreadInfo]``.  Raises ``AttachFailed`` on ptrace
    failure.  No printing is performed.
    """
    _init_libs()

    attached = _attach_all_threads(pid)
    if not attached:
        e = ctypes.get_errno()
        raise AttachFailed(pid, os.strerror(e))

    try:
        return _collect_frames(pid)
    finally:
        _detach_all(attached)


def format_native(cmdline, threads, *, color: bool = False,
                  verbose: bool = False):
    """Render collected native stacks as the human-readable CLI output.

    ``verbose=False`` shorts frame modules to their basename;
    ``verbose=True`` keeps full paths.
    """
    parts = [f"Process: {cmdline}\n"] if cmdline is not None else []
    for i, t in enumerate(threads):
        parts.append(t.format(i + 1, color=color, verbose=verbose))
        parts.append("")
    return "\n".join(parts)


def format_native_json(pid, cmdline, threads) -> str:
    """Render collected native stacks as JSON (machine-readable output).

    The native path has no CPython version/exe metadata (those are Python
    stack concepts) — the process object carries pid and cmdline only.
    """
    return json.dumps(
        {
            "process": {"pid": pid, "cmdline": cmdline},
            "threads": [asdict(t) for t in threads],
        },
        indent=2, ensure_ascii=False) + "\n"


def dump_native(pid, color: Optional[bool] = None, verbose: bool = False,
                json_output: bool = False):
    """CLI entry point: collect + format + print. Returns exit code.

    ``color``: None (default) auto-detect per stream via clicolors rules;
    True/False force color on/off for both stdout and stderr (ignored
    when ``json_output`` is set — JSON is never colored).
    ``verbose``: keep full frame module paths instead of basenames.
    """
    use_color = should_color(sys.stdout) if color is None else color
    err_color = should_color(sys.stderr) if color is None else color
    _init_libs()
    cmdline = read_cmdline(pid) or ""
    if not json_output:
        print(f"Process {yellow_bold(str(pid), use_color)}: {cmdline}\n")

    try:
        threads = collect_native(pid)
    except (AttachFailed, OSError) as e:
        print(red(f"[!] {e}", err_color), file=sys.stderr)
        return 1

    if json_output:
        print(format_native_json(pid, cmdline, threads))
    else:
        print(format_native(cmdline, threads, color=use_color,
                            verbose=verbose))
    return 0
