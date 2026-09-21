"""Process metadata helpers — procfs cmdline + CPython version decoding.

These read /proc/<pid> metadata or decode integer fields that have nothing
to do with ELF parsing. They used to live in ``elf.py``, which forced
``stack_dump`` / ``sampler`` / ``native_dump`` to depend on the ELF module
just to read a cmdline or decode a version (TODO §8.6).  Keeping them here
collapses the dependency graph: ELF-dependent callers no longer pull ELF
in merely to format a process header.
"""

import os


def read_cmdline(pid):
    """Read ``/proc/<pid>/cmdline`` as a space-joined UTF-8 string.

    Returns ``None`` on any ``OSError`` (no such process, permission, …).
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            data = f.read()
        return data.replace(b"\x00", b" ").rstrip(b" ").decode("utf-8", "replace")
    except OSError:
        return None


def decode_py_version(hexval):
    """Decode a CPython ``Py_Version`` uint into ``"major.minor.micro"``.

    ``Py_Version`` packs the version into the top three bytes of a 32-bit
    field: ``major`` at bits 24-31, ``minor`` at 16-23, ``micro`` at 8-15
    (low byte holds release-level flags).  Returned string is the dotted
    triple — release level is intentionally dropped, matching how
    ``stack_dump`` / ``sampler`` build the ``ProcessInfo.python_version``
    label (and how ``offsets._version_key`` derives its lookup key).
    """
    major = (hexval >> 24) & 0xFF
    minor = (hexval >> 16) & 0xFF
    micro = (hexval >> 8) & 0xFF
    return f"{major}.{minor}.{micro}"
