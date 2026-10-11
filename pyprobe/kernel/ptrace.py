"""Unified ptrace engine (ADR A2) — one seize/interrupt/wait/GETREGS/detach
state machine shared by syscall tracing and native unwinding, replacing the
old tree's two attach implementations (``syscall_tracer`` + ``native_dump``).

Layering: this module owns *mechanics only* — stopping threads, classifying
waitpid statuses, forwarding signals, reading registers. Policy (syscall
entry/exit pairing, argument decoding, unwind invocation) belongs to the
``observe/`` consumers.

Constants are Linux ABI: values from ``include/uapi/linux/ptrace.h``,
``arch/x86/include/uapi/asm/ptrace-abi.h`` and ``include/linux/wait.h``
(cited per 测试数据纪律, contracts.md §5.3).

契约（contracts.md 批 1 表 T1–T17，含 A7 修复：回滚捕获所有异常 T2、
arch 检查下沉 T16、删除 wpid==0 死分支 T13）。
当前为骨架：常量/UserRegs/事件类型已锁定（ABI 与数据即契约），
``PtraceEngine`` 方法为哨兵，批次 1 实现填实。
"""

import ctypes
import os
import platform
import time
from dataclasses import dataclass

from ..errors import AttachFailed, UnsupportedArchitecture
from .procfs import list_tids

_libc = None


def _init_libc():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    return _libc


def _ptrace(request, tid, addr=0, data=0):
    """Thin libc.ptrace wrapper (test seam). Returns 0 or -errno."""
    libc = _init_libc()
    ctypes.set_errno(0)
    ret = libc.ptrace(ctypes.c_long(request), ctypes.c_int(tid),
                      ctypes.c_void_p(addr), ctypes.c_void_p(data))
    if ret == -1:
        return -ctypes.get_errno()
    return 0


def _waitpid(pid, flags):
    """os.waitpid wrapper (test seam)."""
    return os.waitpid(pid, flags)

PTRACE_GETREGS = 12
PTRACE_SYSCALL = 24
PTRACE_DETACH = 17
PTRACE_SEIZE = 0x4206
PTRACE_INTERRUPT = 0x4207

PTRACE_O_TRACESYSGOOD = 0x1
PTRACE_O_TRACECLONE = 0x8
PTRACE_O_TRACEEXEC = 0x10

PTRACE_EVENT_CLONE = 3
PTRACE_EVENT_EXEC = 4
PTRACE_EVENT_STOP = 128

SIGTRAP = 5
SIGSTOP = 19
SYSCALL_STOP_SIG = SIGTRAP | 0x80  # with TRACESYSGOOD

_WALL = 0x40000000
WNOHANG = 1


class UserRegs(ctypes.Structure):
    """x86-64 ``struct user_regs_struct`` (arch/x86/include/uapi/asm/user_64.h)."""

    _fields_ = [
        (name, ctypes.c_ulonglong) for name in (
            "r15", "r14", "r13", "r12", "rbp", "rbx", "r11", "r10", "r9",
            "r8", "rax", "rcx", "rdx", "rsi", "rdi", "orig_rax", "rip",
            "cs", "eflags", "rsp", "ss", "fs_base", "gs_base", "ds", "es",
            "fs", "gs",
        )
    ]


# x86-64 syscall argument registers, in order (kernel syscall calling convention).
ARG_REGS = ("rdi", "rsi", "rdx", "r10", "r8", "r9")


@dataclass
class SyscallStop:
    """A thread stopped at a syscall entry or exit (TRACESYSGOOD stop)."""

    tid: int


@dataclass
class ExecEvent:
    """A thread reported PTRACE_EVENT_EXEC (execve succeeded)."""

    tid: int


@dataclass
class Exited:
    """A traced thread exited or was killed (no resume possible)."""

    tid: int
    exit_code: int
    signaled: bool


class PtraceEngine:
    """ptrace state machine for one process (all threads).

    Uses PTRACE_SEIZE (not ATTACH): options are passed with the seize and
    inherited by new threads, and an interrupted tracee can always be
    DETACHed cleanly (ATTACH would leave it stopped if we exit early) —
    the same safe-detach rationale as old design.md §14.2.

    ``feature`` names the consumer for error messages (e.g. "syscall
    tracing") — the architecture check lives here (契约 T16, A7 P0-2) so
    every entry path fails with ``UnsupportedArchitecture`` instead of
    reading registers with the wrong layout.
    """

    def __init__(self, pid: int, *, feature: str = "ptrace",
                 restart_op: int = PTRACE_SYSCALL):
        # T16 (A7 P0-2): the check lives here so every entry path (library
        # or CLI) fails with UnsupportedArchitecture instead of reading
        # registers with the wrong layout.
        machine = platform.machine()
        if machine not in ("x86_64", "AMD64"):
            raise UnsupportedArchitecture(feature, machine)
        self.pid = pid
        self.feature = feature
        self.restart_op = restart_op
        self.tids: set[int] = set()

    def seize(self, options: int = 0) -> None:
        """SEIZE all threads (re-scan loop for racers), options per T1.

        Rolls back (DETACH every seized thread) on *any* exception (T2,
        A7 P1-4). Main-thread seize failure raises ``AttachFailed``; other
        threads dying mid-scan are skipped (T3). ``self.tids`` is updated
        incrementally so a later ``detach()`` stays consistent.
        """
        seized: list[int] = []
        self.tids.clear()
        try:
            pending = list_tids(self.pid)
            while pending:  # T1: re-scan until no new threads appear
                for tid in pending:
                    rc = _ptrace(PTRACE_SEIZE, tid, 0, options)
                    if rc != 0:
                        if tid == self.pid or not seized:
                            raise AttachFailed(self.pid, os.strerror(-rc))
                        continue  # T3: non-main thread died mid-scan
                    seized.append(tid)
                    self.tids.add(tid)  # T2: incremental for rollback/detach
                pending = [t for t in list_tids(self.pid) if t not in self.tids]
        except BaseException:
            # T2 (A7 P1-4): roll back on *any* exception, not just
            # AttachFailed — the old tree leaked seized threads otherwise.
            for tid in seized:
                _ptrace(PTRACE_DETACH, tid)
            self.tids.clear()
            raise

    def stop_all(self) -> None:
        """INTERRUPT every seized thread and collect its stop (T4)."""
        for tid in sorted(self.tids):
            _ptrace(PTRACE_INTERRUPT, tid)
        for tid in sorted(self.tids):
            try:
                _waitpid(tid, 0)
            except OSError:
                pass  # thread died between seize and interrupt

    def restart_all(self) -> None:
        """Restart every seized thread with ``self.restart_op``."""
        for tid in sorted(self.tids):
            _ptrace(self.restart_op, tid)

    def resume(self, tid: int, sig: int = 0) -> None:
        """Restart one thread with ``self.restart_op``, forwarding ``sig``."""
        _ptrace(self.restart_op, tid, 0, sig)

    def wait_event(self) -> SyscallStop | ExecEvent | Exited | None:
        """Wait for the next actionable event (T5–T13).

        Mechanics handled internally (loop continues, no event returned):
        group-stop swallow (T6), real-signal forward (T7), fresh TRACECLONE
        child SIGSTOP swallow + tid registration (T8), non-event SIGTRAP
        forward (T9), CLONE parent restart without event (T11),
        InterruptedError retry (T12). Returns ``None`` when no tracees
        remain (ECHILD, T12).
        """
        while True:
            try:
                wpid, status = _waitpid(-1, _WALL)
            except ChildProcessError:
                return None  # T12: no tracees remain
            except InterruptedError:
                continue  # T12
            if os.WIFSTOPPED(status):
                ev = self._handle_stop(wpid, status)
                if ev is not None:
                    return ev
            elif os.WIFEXITED(status) or os.WIFSIGNALED(status):
                # T12: deregister and report immediately — one stop, one
                # event; only mechanics stops are swallowed inside a call.
                self.tids.discard(wpid)
                if os.WIFEXITED(status):
                    return Exited(wpid, os.WEXITSTATUS(status), False)
                return Exited(wpid, os.WTERMSIG(status), True)

    def _handle_stop(self, tid: int, status: int) -> SyscallStop | ExecEvent | None:
        """Classify one stop; restart internally or hand the event over."""
        sig = os.WSTOPSIG(status)
        event = status >> 16

        if sig == SYSCALL_STOP_SIG:
            return SyscallStop(tid)  # T5: consumer owns resume

        if event == PTRACE_EVENT_STOP:
            # T6: group-stop — swallow, restart without forwarding
            _ptrace(self.restart_op, tid)
            return None

        if sig == SIGTRAP:
            if event == PTRACE_EVENT_CLONE:
                # T11: the child reports its own SIGSTOP delivery-stop (T8)
                _ptrace(self.restart_op, tid)
            elif event == PTRACE_EVENT_EXEC:
                return ExecEvent(tid)  # T10: consumer owns resume
            else:
                # T9: other trap (breakpoint etc.) — forward
                _ptrace(self.restart_op, tid, 0, sig)
            return None

        if tid not in self.tids:
            # T8: SIGSTOP delivery-stop of a fresh TRACECLONE child —
            # register and swallow
            self.tids.add(tid)
            _ptrace(self.restart_op, tid)
            return None

        # T7: signal-delivery-stop — forward the signal
        _ptrace(self.restart_op, tid, 0, sig)
        return None

    def getregs(self, tid: int) -> UserRegs | None:
        """Read general-purpose registers of ``tid``; None on failure (T15)."""
        regs = UserRegs()
        rc = _ptrace(PTRACE_GETREGS, tid, 0, ctypes.addressof(regs))
        if rc != 0:
            return None
        return regs

    def detach(self) -> None:
        """Idempotent detach (T14): INTERRUPT + bounded WNOHANG collect +
        DETACH every thread, then clear state. Safe to call repeatedly or
        after a partially-failed ``seize``.
        """
        for tid in sorted(self.tids):
            _ptrace(PTRACE_INTERRUPT, tid)
            # collect the interrupt stop (bounded retries)
            for _ in range(20):
                try:
                    wpid, _status = _waitpid(tid, WNOHANG | _WALL)
                except OSError:
                    break
                if wpid == tid:
                    break
                time.sleep(0.002)
            _ptrace(PTRACE_DETACH, tid)
        self.tids.clear()
