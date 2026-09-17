"""Native stack dump via libdwfl (elfutils) using ctypes."""

import ctypes
import os

from .elf import read_cmdline

Dwarf_Addr = ctypes.c_uint64
pid_t = ctypes.c_int32
Dwfl = ctypes.c_void_p
Dwfl_Module = ctypes.c_void_p
Dwfl_Thread = ctypes.c_void_p
Dwfl_Frame = ctypes.c_void_p
Elf = ctypes.c_void_p
GElf_Word = ctypes.c_uint32

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
    libdw.dwfl_module_addrname.argtypes = [ctypes.POINTER(Dwfl_Module), GElf_Word]
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
_MAX_LINE = 512


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


def dump_native(pid):
    _init_libs()
    cmdline = read_cmdline(pid) or ""
    print(f"Process {pid}: {cmdline}\n")

    attached = _attach_all_threads(pid)
    if not attached:
        e = ctypes.get_errno()
        print(f"[!] PTRACE_ATTACH failed: {os.strerror(e)}")
        return 1

    try:
        return _do_dump(pid)
    finally:
        _detach_all(attached)


def _do_dump(pid):
    debuginfo_path = ctypes.c_char_p(None)
    callbacks = Dwfl_Callbacks()
    callbacks.find_elf = _find_elf_t(libdw.dwfl_linux_proc_find_elf)
    callbacks.find_debuginfo = _find_debuginfo_t(libdw.dwfl_standard_find_debuginfo)
    callbacks.section_address = _section_address_t(libdw.dwfl_offline_section_address)
    callbacks.debuginfo_path = ctypes.pointer(debuginfo_path)

    dwfl = libdw.dwfl_begin(ctypes.byref(callbacks))
    if not dwfl:
        print(f"[!] dwfl_begin failed: {libdw.dwfl_errmsg(-1)}")
        return 1

    try:
        if libdw.dwfl_linux_proc_report(dwfl, pid) != 0:
            e = ctypes.get_errno()
            print(f"[!] dwfl_linux_proc_report failed: {os.strerror(e)}")
            return 1

        libdw.dwfl_report_end(dwfl, None, None)

        if libdw.dwfl_linux_proc_attach(dwfl, pid, True) != 0:
            e = ctypes.get_errno()
            print(f"[!] dwfl_linux_proc_attach failed: {os.strerror(e)}")
            return 1

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
                    frames.append((pc.value, sym, modpath))
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
            results.append((tid, comm, frames, fr != 0 and len(frames) == 0))
            return 0

        libdw.dwfl_getthreads(dwfl, thread_cb, None)

        results.sort(key=lambda r: r[0], reverse=True)

        for i, (tid, comm, frames, unwind_err) in enumerate(results):
            print(f'Thread {i + 1} (LWP {tid}) "{comm}":')
            if not frames and unwind_err:
                print("  Backtrace stopped: Cannot access memory at address 0x0")
            else:
                for j, (pc_val, sym_str, modpath) in enumerate(frames):
                    if modpath:
                        print(f"  #{j}  0x{pc_val:016x} in {sym_str} () from {modpath}")
                    else:
                        print(f"  #{j}  0x{pc_val:016x} in {sym_str} ()")
            print()

        return 0
    finally:
        libdw.dwfl_end(dwfl)


def _read_comm(pid, tid):
    try:
        with open(f"/proc/{pid}/task/{tid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""
