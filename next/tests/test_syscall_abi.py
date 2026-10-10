"""Contract tests for observe/syscall_abi.py (SY11 spec-oracle + 结构性守护).

The authoritative source for the number table is the system header
``asm/unistd_64.h`` itself (§10.2-1) — the old tree asserted a handful of
hand-picked entries against the table (a copy checked against a copy);
here the whole table is pinned against the header, and the flag tables
against fcntl.h/mman.h samples.
"""

import re
from pathlib import Path

import pytest

from pyprobe.observe.syscall_abi import (
    DECODE,
    MAP_FLAGS,
    OPEN_FLAGS,
    PROT_FLAGS,
    SYSCALL_NAMES,
    SYSCALL_NRS,
    TRACE_GROUPS,
)

_UNISTD = Path("/usr/include/x86_64-linux-gnu/asm/unistd_64.h")


class TestTableStructure:
    def test_reverse_lookup(self):
        """结构：SYSCALL_NRS 是 SYSCALL_NAMES 的精确逆映射（双向查找一致）。"""
        assert SYSCALL_NRS == {n: nr for nr, n in SYSCALL_NAMES.items()}

    def test_names_unique(self):
        """结构：名无重复（逆映射不丢条目）。"""
        assert len(set(SYSCALL_NAMES.values())) == len(SYSCALL_NAMES)

    def test_table_is_dense_enough(self):
        """结构：表覆盖主流范围（0–334 连续段 + io_uring 等扩展段，
        密度防大面积缺漏）。"""
        assert len(SYSCALL_NAMES) >= 300
        assert all(nr in SYSCALL_NAMES for nr in range(0, 335))

    def test_decode_keys_are_real_syscalls(self):
        """结构：DECODE 键必须全是表内 syscall（元数据漂移守护）。"""
        assert set(DECODE) <= set(SYSCALL_NAMES.values())

    def test_group_members_are_real_syscalls(self):
        """结构：TRACE_GROUPS 成员必须全是表内 syscall。"""
        for group, members in TRACE_GROUPS.items():
            assert members <= set(SYSCALL_NAMES.values()), group

    def test_known_groups_exist(self):
        """结构：strace 风格的六个组齐备（CLI -e trace= 文档承诺）。"""
        assert {"file", "network", "process", "memory", "signal", "desc"} == \
            set(TRACE_GROUPS)


@pytest.mark.skipif(not _UNISTD.exists(), reason="unistd_64.h not installed")
class TestSpecOracleUnistdHeader:
    """SY11 (§10.2-1)：全量表 vs 系统头文件（权威来源）。"""

    def _parse_header(self):
        """Extract ``#define __NR_<name> <num>`` pairs from unistd_64.h."""
        out = {}
        for line in _UNISTD.read_text().splitlines():
            m = re.match(r"#define\s+__NR_(\w+)\s+(\d+)", line)
            if m:
                out[int(m.group(2))] = m.group(1)
        return out

    def test_table_matches_header(self):
        """SY11：表内每个号/名对与头文件一致（内核可能更新——只校验
        表内已有的号，头文件新增的号不强制收录）。"""
        header = self._parse_header()
        for nr, name in SYSCALL_NAMES.items():
            assert nr in header, f"{nr} ({name}) not in header"
            assert header[nr] == name, (
                f"nr {nr}: table says {name}, header says {header[nr]}")


class TestSpecOracleFlagTables:
    """SY11 (§10.2-1)：flags 表 vs fcntl.h/mman.h 抽样（glibc 权威值）。"""

    def _header_value(self, paths, name, _depth=0):
        """Resolve ``#define <name> <value>`` across candidate headers with
        octal/hex literals and single-level alias chains（glibc 的 `# if`
        变体会产生多个 define——先全量收集，数值优先，别名递归；
        __O_CLOEXEC 等跨文件别名经候选路径列表覆盖）。"""
        if _depth > 3:
            pytest.skip(f"alias chain too deep for {name}")
        if isinstance(paths, (str, Path)):
            paths = [paths]
        tokens = []
        for p in paths:
            p = Path(p)
            if p.exists():
                tokens += re.findall(
                    rf"#\s*define\s+{re.escape(name)}\s+(\S+)",
                    p.read_text())
        for token in tokens:  # 数值字面量优先
            if token.startswith(("0x", "0X")):
                return int(token, 16)
            if re.fullmatch(r"0[0-7]+", token):
                return int(token, 8)  # C 八进制字面量（如 0100）
            if token.isdigit():
                return int(token)
        for token in tokens:  # 其次别名递归
            if re.fullmatch(r"__?\w+", token):
                return self._header_value(paths, token, _depth + 1)
        pytest.skip(f"{name} not found in {paths}")

    @pytest.mark.skipif(
        not Path("/usr/include/x86_64-linux-gnu/bits/fcntl-linux.h").exists(),
        reason="fcntl-linux.h not installed")
    def test_open_flags_samples(self):
        """SY11：OPEN_FLAGS 关键值与 glibc fcntl 定义一致。"""
        hdrs = ["/usr/include/x86_64-linux-gnu/bits/fcntl-linux.h",
                "/usr/include/x86_64-linux-gnu/bits/cloexec.h"]
        for name in ("O_CREAT", "O_EXCL", "O_TRUNC", "O_APPEND",
                     "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
            value = self._header_value(hdrs, name)
            assert OPEN_FLAGS.get(value) == name

    @pytest.mark.skipif(
        not Path("/usr/include/x86_64-linux-gnu/bits/mman-linux.h").exists(),
        reason="mman-linux.h not installed")
    def test_map_prot_flags_samples(self):
        """SY11：MAP/PROT 关键值与 glibc mman 定义一致。"""
        hdr = "/usr/include/x86_64-linux-gnu/bits/mman-linux.h"
        for name in ("PROT_READ", "PROT_WRITE", "PROT_EXEC",
                     "MAP_SHARED", "MAP_PRIVATE", "MAP_FIXED",
                     "MAP_ANONYMOUS"):
            value = self._header_value(hdr, name)
            table = PROT_FLAGS if name.startswith("PROT") else MAP_FLAGS
            assert table.get(value) == name
