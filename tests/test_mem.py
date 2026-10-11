"""Contract tests for kernel/mem.py — Transport byte transfer (M1–M6).

The syscall seam is injected: ``syscall(pid, addr, length) -> bytes | None``
performs one read attempt (short bytes = partial read, None = failure, b""
= EOF-style zero read), letting tests script exact kernel behaviour without
ptrace or ctypes trickery.
"""

import ctypes

import pytest

from pyprobe.kernel import mem
from pyprobe.kernel.mem import Transport

PID = 4242  # 哨兵：可辨识假 PID，永不与真实进程混淆


class ScriptedSyscall:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, pid, addr, length):
        self.calls.append((pid, addr, length))
        return self.script.pop(0)


def test_full_read_single_call():
    """M1 快乐路径：一次填满即返回，syscall 恰好一次、地址/长度原样透传——
    防读循环在快乐路径上多余的二次调用（性能回归）。"""
    sc = ScriptedSyscall([b"abcdefgh"])
    t = Transport(PID, syscall=sc)
    assert t.read(0x1000, 8) == b"abcdefgh"
    assert sc.calls == [(PID, 0x1000, 8)]


def test_partial_then_complete():
    """M1: a short read resumes at addr+got with length-got."""
    sc = ScriptedSyscall([b"ab", b"cd"])
    t = Transport(PID, syscall=sc)
    assert t.read(0x1000, 4) == b"abcd"
    assert sc.calls == [(PID, 0x1000, 4), (PID, 0x1002, 2)]


def test_short_read_returned_when_kernel_stops():
    """M4: zero-read terminates the loop; accumulated bytes are returned."""
    sc = ScriptedSyscall([b"ab", b""])
    t = Transport(PID, syscall=sc)
    assert t.read(0x1000, 8) == b"ab"


def test_zero_bytes_returns_none():
    """M3: nothing read at all -> None (distinguishes failure from b"")."""
    sc = ScriptedSyscall([b""])
    t = Transport(PID, syscall=sc)
    assert t.read(0x1000, 8) is None


def test_error_returns_none():
    """M2: syscall failure -> None, never an exception."""
    sc = ScriptedSyscall([None])
    t = Transport(PID, syscall=sc)
    assert t.read(0x1000, 8) is None


def test_error_after_partial_fails_whole_read():
    """M2: an error mid-loop invalidates the whole read (old semantics)."""
    sc = ScriptedSyscall([b"ab", None])
    t = Transport(PID, syscall=sc)
    assert t.read(0x1000, 8) is None


def test_zero_length_no_syscall():
    """M6: length 0 -> b"" without touching the kernel."""
    sc = ScriptedSyscall([])
    t = Transport(PID, syscall=sc)
    assert t.read(0x1000, 0) == b""
    assert sc.calls == []


class FakeLibc:
    """Scripted process_vm_readv for the default syscall path (M5).

    Each script entry is (data: bytes, errno: int); errno != 0 models a -1
    return with that errno. Copies ``data`` into the caller's iovec like the
    real syscall would.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def process_vm_readv(self, pid, local, liocnt, remote, riocnt, flags):
        self.calls += 1
        data, err = self.script.pop(0)
        if err:
            ctypes.set_errno(err)
            return -1
        ctypes.memmove(local[0].iov_base, data, len(data))
        return len(data)


def test_default_syscall_eintr_retried(monkeypatch):
    """M5: EINTR (4) inside one read attempt is retried, not an error."""
    fake = FakeLibc([(b"", 4), (b"xy", 0)])
    monkeypatch.setattr(mem, "_libc", fake)
    t = Transport(PID)
    assert t.read(0x1000, 2) == b"xy"
    assert fake.calls == 2


def test_default_syscall_real_error_returns_none(monkeypatch):
    """M2: a non-EINTR errno (EFAULT=14) ends the read as None."""
    fake = FakeLibc([(b"", 14)])
    monkeypatch.setattr(mem, "_libc", fake)
    t = Transport(PID)
    assert t.read(0xDEAD0000, 8) is None


@pytest.mark.integration
def test_transport_reads_own_process_memory():
    """Real kernel path: process_vm_readv against our own pid (no child)."""
    import os

    sentinel = b"pyprobe-kernel-mem"  # 18 bytes
    buf = (ctypes.c_char * len(sentinel))(*sentinel)
    t = Transport(os.getpid())
    got = t.read(ctypes.addressof(buf), len(sentinel))
    assert got == sentinel
