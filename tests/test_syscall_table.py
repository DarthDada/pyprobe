"""Unit tests for the x86-64 syscall table (pyprobe/syscall_table.py)."""

from pyprobe.syscall_table import (
    SYSCALL_NAMES, SYSCALL_NRS, DECODE, TRACE_GROUPS,
    OPEN_FLAGS, MAP_FLAGS, PROT_FLAGS,
)


class TestSyscallNames:
    def test_read_is_0(self):
        assert SYSCALL_NAMES[0] == "read"

    def test_write_is_1(self):
        assert SYSCALL_NAMES[1] == "write"

    def test_openat_is_257(self):
        assert SYSCALL_NAMES[257] == "openat"

    def test_reverse_lookup(self):
        assert SYSCALL_NRS["read"] == 0
        assert SYSCALL_NRS["openat"] == 257

    def test_table_is_dense_enough(self):
        # kernel 6.x x86-64 defines ~375 syscalls
        assert len(SYSCALL_NAMES) >= 360

    def test_names_unique(self):
        assert len(SYSCALL_NAMES) == len(SYSCALL_NRS)

    def test_notable_entries(self):
        assert SYSCALL_NAMES[9] == "mmap"
        assert SYSCALL_NAMES[57] == "fork"
        assert SYSCALL_NAMES[59] == "execve"
        assert SYSCALL_NAMES[231] == "exit_group"
        assert SYSCALL_NAMES[230] == "clock_nanosleep"


class TestDecode:
    def test_decode_keys_are_real_syscalls(self):
        bad = [name for name in DECODE if name not in SYSCALL_NRS]
        assert bad == []

    def test_read_shape(self):
        assert DECODE["read"] == ["fd", "buf_in", "count"]

    def test_openat_shape(self):
        assert DECODE["openat"] == ["dirfd", "path", "open_flags", "mode"]

    def test_mmap_shape(self):
        assert DECODE["mmap"] == \
            ["addr", "length", "prot", "map_flags", "fd", "offset"]

    def test_no_args_syscalls(self):
        assert DECODE["getpid"] == []
        assert DECODE["gettid"] == []


class TestTraceGroups:
    def test_group_members_are_real_syscalls(self):
        for group, names in TRACE_GROUPS.items():
            bad = [n for n in names if n not in SYSCALL_NRS]
            assert bad == [], f"{group}: {bad}"

    def test_file_group_contains_open_close(self):
        assert "openat" in TRACE_GROUPS["file"]
        assert "close" in TRACE_GROUPS["file"]
        assert "read" not in TRACE_GROUPS["file"]

    def test_network_group_contains_connect(self):
        assert "connect" in TRACE_GROUPS["network"]
        assert "accept4" in TRACE_GROUPS["network"]

    def test_known_groups_exist(self):
        assert {"file", "network", "process"} <= set(TRACE_GROUPS)


class TestFlagTables:
    def test_open_flags_accmodes(self):
        assert OPEN_FLAGS[0] == "O_RDONLY"
        assert OPEN_FLAGS[1] == "O_WRONLY"
        assert OPEN_FLAGS[2] == "O_RDWR"
        assert OPEN_FLAGS[0x40] == "O_CREAT"

    def test_map_flags(self):
        assert MAP_FLAGS[0x02] == "MAP_PRIVATE"
        assert MAP_FLAGS[0x20] == "MAP_ANONYMOUS"

    def test_prot_flags(self):
        assert PROT_FLAGS[0x1] == "PROT_READ"
        assert PROT_FLAGS[0x2] == "PROT_WRITE"
        assert PROT_FLAGS[0x4] == "PROT_EXEC"
