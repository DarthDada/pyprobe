"""Native stack unwinding via libdwfl (elfutils), on the unified ptrace
engine (ADR A2).

A2 迁移：线程停止经 PtraceEngine（SEIZE + INTERRUPT，options=0）而非旧树
的 PTRACE_ATTACH——dwfl 以 assume_ptrace_stopped=True 消费已停线程，
detach 经引擎幂等完成（与 design.md §14.2 相同的安全 detach 理由）。
native 行为因此有变，contracts.md §10.5-1 的 ``stack --native --json`` 旧新差异对比
为其强制验证项（批 7 出口）。

契约（contracts.md 批 5 表 NA1–NA10）。
"""

import ctypes
import os
from ctypes.util import find_library

from .. import dto
from ..errors import AttachFailed
from ..kernel.procfs import read_comm
from ..kernel.ptrace import PtraceEngine

#: Per-thread frame cap (契约 NA5, 复刻约束).
MAX_FRAMES = 256

#: Candidate libdw sonames probed in order (NA1 / ADR A9).
_LIBDW_NAMES = ("libdw.so.1", "libdw.so")

#: Optional libdw symbols gating P1/P2 features (NA10 / ADR A9, A12).
_OPTIONAL_SYMBOLS = ("dwarf_getscopes", "dwfl_module_getsrc",
                     "dwarf_cfi_addrframe", "dwfl_lineinfo")

#: libdw handle, populated by _init_libs (NA1).
libdw = None

#: Subset of _OPTIONAL_SYMBOLS the loaded libdw exports (NA10 / ADR A9).
available_symbols = frozenset()

#: Set only by the real _init_libs — the cache flag tracks "real load
#: happened", not "the global is non-None", so a test double installed at
#: the libdw seam never poisons the real loader's idempotence.
_libdw_loaded = False

# -- libdwfl C API types (elfutils libdwfl.h) --------------------------------

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
    ctypes.c_char_p, Dwarf_Addr,
    ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(Elf))
_find_debuginfo_t = ctypes.CFUNCTYPE(
    ctypes.c_int, Dwfl_Module, ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_char_p, Dwarf_Addr, ctypes.c_char_p, ctypes.c_char_p,
    GElf_Word, ctypes.POINTER(ctypes.c_char_p))
_section_address_t = ctypes.CFUNCTYPE(
    ctypes.c_int, Dwfl_Module, ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_char_p, Dwarf_Addr, ctypes.c_char_p, GElf_Word,
    ctypes.c_void_p, ctypes.POINTER(Dwarf_Addr))


class Dwfl_Callbacks(ctypes.Structure):
    """libdwfl callback table (NA2)."""

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


def _load_libdw(*, loader=ctypes.CDLL, finder=find_library):
    """Load libdw, probing candidate sonames in order (NA1 / ADR A9).

    ldconfig environments register either ``libdw.so.1`` or ``libdw.so``;
    both are tried, then the ``find_library("dw")`` result as a last
    resort. The first candidate that loads wins; if none does, the last
    ``OSError`` propagates (a load failure is never swallowed).
    """
    candidates = list(_LIBDW_NAMES)
    located = finder("dw")
    if located and located not in candidates:
        candidates.append(located)
    last_error = None
    for name in candidates:
        try:
            return loader(name, use_errno=True)
        except OSError as exc:
            last_error = exc
    raise last_error


def _probe_optional(lib) -> frozenset:
    """Probe optional P1/P2 symbols the loaded libdw exports (NA10 / ADR A9).

    Missing symbols are non-fatal: the core symbol-name + unwind path
    (NA4) does not depend on them, and future P1/P2 consumers gate on
    this capability set instead of crashing on old elfutils.
    """
    return frozenset(name for name in _OPTIONAL_SYMBOLS if hasattr(lib, name))


def _unwind_failed(fr: int, frames: list[dto.NativeFrame]) -> bool:
    """Whether dwfl failed to produce a usable backtrace (NA6 / ADR A10).

    True when the unwinder errored with nothing read, or when the result
    is a lone ``pc == 0`` sentinel frame — gdb's "Backtrace stopped:
    Cannot access memory at address 0x0" case (e.g. io_uring ``iou-sqp-*``
    kernel worker threads). A single real frame is *not* a failure even
    when ``fr`` reports an error.
    """
    if fr != 0 and not frames:
        return True
    return len(frames) == 1 and frames[0].pc == 0


def _resolve_line(mod, lookup):
    """Best-effort DWARF source position ``(filename, line)`` (NA11 / ADR A12).

    Uses libdwfl's ``dwfl_module_getsrc`` + ``dwfl_lineinfo`` when the loaded
    libdw exposes both (NA10 capability gate). Modules without DWARF line
    info (e.g. a stripped CPython binary) yield ``(None, None)``, matching
    gdb's fallback to ``from <module>``.
    """
    if not mod or "dwfl_module_getsrc" not in available_symbols \
            or "dwfl_lineinfo" not in available_symbols:
        return None, None
    src = libdw.dwfl_module_getsrc(mod, lookup)
    if not src:
        return None, None
    linep = ctypes.c_int(0)
    name = libdw.dwfl_lineinfo(src, None, ctypes.byref(linep), None, None,
                               None)
    if not name or linep.value <= 0:
        return None, None
    return name.decode("utf-8", "replace"), linep.value


def _init_libs() -> None:
    """Load libdw, probe optional symbols, wire every signature (NA1, NA10)."""
    global libdw, _libdw_loaded, available_symbols
    if _libdw_loaded:
        return
    libdw = _load_libdw()
    _libdw_loaded = True
    available_symbols = _probe_optional(libdw)

    libdw.dwfl_begin.restype = ctypes.POINTER(Dwfl)
    libdw.dwfl_begin.argtypes = [ctypes.POINTER(Dwfl_Callbacks)]
    libdw.dwfl_end.restype = None
    libdw.dwfl_end.argtypes = [ctypes.POINTER(Dwfl)]
    libdw.dwfl_errmsg.restype = ctypes.c_char_p
    libdw.dwfl_errmsg.argtypes = [ctypes.c_int]
    libdw.dwfl_linux_proc_report.restype = ctypes.c_int
    libdw.dwfl_linux_proc_report.argtypes = [ctypes.POINTER(Dwfl), pid_t]
    libdw.dwfl_report_end.restype = ctypes.c_int
    libdw.dwfl_report_end.argtypes = [ctypes.POINTER(Dwfl), ctypes.c_void_p,
                                      ctypes.c_void_p]
    libdw.dwfl_linux_proc_attach.restype = ctypes.c_int
    libdw.dwfl_linux_proc_attach.argtypes = [ctypes.POINTER(Dwfl), pid_t,
                                             ctypes.c_bool]
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
    libdw.dwfl_module_addrname.argtypes = [ctypes.POINTER(Dwfl_Module),
                                           GElf_Addr]
    libdw.dwfl_module_info.restype = ctypes.c_char_p
    libdw.dwfl_module_info.argtypes = [
        ctypes.POINTER(Dwfl_Module),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ctypes.POINTER(Dwarf_Addr), ctypes.POINTER(Dwarf_Addr),
        ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p),
        ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p),
    ]
    libdw.dwfl_getthreads.restype = ctypes.c_int
    libdw.dwfl_getthreads.argtypes = [ctypes.POINTER(Dwfl), _thread_cb_t,
                                      ctypes.c_void_p]
    libdw.dwfl_thread_getframes.restype = ctypes.c_int
    libdw.dwfl_thread_getframes.argtypes = [
        ctypes.POINTER(Dwfl_Thread), _frame_cb_t, ctypes.c_void_p]

    # Optional P1/P2 API (NA10): wire only the symbols the loaded libdw
    # exposes, so old elfutils stays usable (NA11 源码位置 gate).
    if "dwfl_module_getsrc" in available_symbols:
        libdw.dwfl_module_getsrc.restype = ctypes.c_void_p
        libdw.dwfl_module_getsrc.argtypes = [
            ctypes.POINTER(Dwfl_Module), Dwarf_Addr]
    if "dwfl_lineinfo" in available_symbols:
        libdw.dwfl_lineinfo.restype = ctypes.c_char_p
        libdw.dwfl_lineinfo.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(Dwarf_Addr),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64)]


def _default_callbacks() -> Dwfl_Callbacks:
    """Assemble the dwfl callback table from the standard helpers (NA2).

    Wired defensively: unit tests substitute a scripted libdw double that
    does not carry the find_* helpers (they are never invoked there).
    """
    debuginfo_path = ctypes.c_char_p(None)
    callbacks = Dwfl_Callbacks()
    find_elf = getattr(libdw, "dwfl_linux_proc_find_elf", None)
    if find_elf is not None:
        callbacks.find_elf = _find_elf_t(find_elf)
    find_debuginfo = getattr(libdw, "dwfl_standard_find_debuginfo", None)
    if find_debuginfo is not None:
        callbacks.find_debuginfo = _find_debuginfo_t(find_debuginfo)
    section_address = getattr(libdw, "dwfl_offline_section_address", None)
    if section_address is not None:
        callbacks.section_address = _section_address_t(section_address)
    callbacks.debuginfo_path = ctypes.pointer(debuginfo_path)
    return callbacks


def collect_native(pid: int) -> list[dto.NativeThreadInfo]:
    """Unwind native stacks of all threads of ``pid`` (NA2–NA8).

    Returns threads sorted by tid (NA7). Raises ``AttachFailed`` on
    engine/dwfl setup failure. No printing (format/dump live in present/).
    """
    _init_libs()

    # NA8 (A2 迁移): SEIZE + INTERRUPT via the shared engine, options=0;
    # dwfl consumes the already-stopped threads (assume_ptrace_stopped).
    engine = PtraceEngine(pid, feature="native stack dump")
    engine.seize(0)
    try:
        engine.stop_all()
        callbacks = _default_callbacks()

        dwfl = libdw.dwfl_begin(ctypes.byref(callbacks))
        if not dwfl:
            # NA2 失败模式（复刻旧文案）
            raise AttachFailed(
                pid, f"dwfl_begin failed: {libdw.dwfl_errmsg(-1)}")
        try:
            if libdw.dwfl_linux_proc_report(dwfl, pid) != 0:
                # NA3 失败模式
                e = ctypes.get_errno()
                raise AttachFailed(
                    pid,
                    f"dwfl_linux_proc_report failed: {os.strerror(e)}")
            libdw.dwfl_report_end(dwfl, None, None)
            if libdw.dwfl_linux_proc_attach(dwfl, pid, True) != 0:
                # NA3 失败模式；True == assume_ptrace_stopped (NA8)
                e = ctypes.get_errno()
                raise AttachFailed(
                    pid,
                    f"dwfl_linux_proc_attach failed: {os.strerror(e)}")

            results: list[dto.NativeThreadInfo] = []
            # Frames land in the list the enclosing thread_cb is currently
            # filling (closure state, not py_object arg marshaling).
            current: dict[str, list | None] = {"frames": None}

            @_frame_cb_t
            def frame_cb(state, arg):
                pc = Dwarf_Addr(0)
                isact = ctypes.c_bool(False)
                if not libdw.dwfl_frame_pc(state, ctypes.byref(pc),
                                           ctypes.byref(isact)):
                    return -1  # NA4: no pc — stop unwinding this thread
                # NA4: non-activated frames hold a return address — step
                # back into the calling instruction for the lookup
                lookup = pc.value if isact.value else pc.value - 1
                dw = libdw.dwfl_thread_dwfl(libdw.dwfl_frame_thread(state))
                mod = libdw.dwfl_addrmodule(dw, lookup)

                sym = "??"  # NA4: stripped binary placeholder
                modpath = None
                if mod:
                    s = libdw.dwfl_module_addrname(mod, lookup)
                    if s:
                        sym = s.decode("utf-8", "replace")
                    mainfile = ctypes.c_char_p()
                    libdw.dwfl_module_info(mod, None, None, None, None,
                                           None, ctypes.byref(mainfile),
                                           None)
                    if mainfile.value:
                        modpath = mainfile.value.decode("utf-8", "replace")

                filename, lineno = _resolve_line(mod, lookup)

                frames = current["frames"]
                if frames is not None and len(frames) < MAX_FRAMES:  # NA5
                    frames.append(dto.NativeFrame(pc=pc.value, symbol=sym,
                                                  module=modpath,
                                                  filename=filename,
                                                  line=lineno))
                return 0

            @_thread_cb_t
            def thread_cb(thread, arg):
                tid = libdw.dwfl_thread_tid(thread)
                frames: list[dto.NativeFrame] = []
                current["frames"] = frames
                fr = libdw.dwfl_thread_getframes(thread, frame_cb, None)
                current["frames"] = None
                results.append(dto.NativeThreadInfo(
                    tid=tid, comm=read_comm(pid, tid), frames=frames,
                    # NA6: ADR A10 — dwfl error with no frames, or a lone
                    # pc=0 sentinel frame (gdb "Backtrace stopped").
                    unwind_failed=_unwind_failed(fr, frames),
                ))
                return 0

            libdw.dwfl_getthreads(dwfl, thread_cb, None)

            results.sort(key=lambda r: r.tid)  # NA7
            return results
        finally:
            libdw.dwfl_end(dwfl)
    finally:
        engine.detach()  # NA8: never leave the tracee stopped
