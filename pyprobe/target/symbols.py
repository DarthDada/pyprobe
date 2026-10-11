"""ELF symbol and constant location — manual ELF64 parsing (no deps).

Two concerns, two API levels:

* :class:`ElfImage` — the parsed, file-level view: section headers, symbol
  tables, symbol→file-offset reads. Pure function of the file on disk.
* :func:`find_symbol` / :func:`read_const` — the process-level view:
  applies the load base of a PIE (ET_DYN) binary mapped into a live
  process (via ``kernel/procfs.load_base``), which is what stack walking
  actually needs.

契约（contracts.md 批 2 表 S1–S9）。当前为骨架：格式常量（ELF64 spec
权威值）与签名已锁定，方法返回哨兵，批次 2 实现填实。
"""

import os
import struct

from ..kernel.procfs import load_base

# ELF64 format constants (authoritative: ELF-64 spec / elf.h).
_SHDR_FMT = "<IIQQQQIIQQ"
_SHDR_SIZE = 64
_SYM_FMT = "<IBBHQQ"
_EHDR_SIZE = 64

_SHT_SYMTAB = 2
_SHT_DYNSYM = 11
_ET_DYN = 3
_STT_NOTYPE = 0
_STT_OBJECT = 1

#: Parse cache keyed by (path, mtime) — a changed binary is re-parsed,
#: an unchanged one is decoded exactly once (契约 S8).
_IMAGE_CACHE: dict[tuple[str, float], "ElfImage"] = {}


class ElfImage:
    """Parsed ELF64 image (cached per (path, mtime) — 契约 S8).

    Use :meth:`open` to construct. ``ValueError`` on non-ELF / non-ELF64
    input (契约 S1).
    """

    e_type: int = 0

    def __init__(self, path: str, data: bytes, e_type: int, shdr_table: bytes,
                 symtab_idx: int | None, dynsym_idx: int | None):
        self.path = path
        self.e_type = e_type
        self._data = data
        self._shdr_table = shdr_table
        self._symtab_idx = symtab_idx
        self._dynsym_idx = dynsym_idx

    @classmethod
    def open(cls, path: str) -> "ElfImage":
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = 0  # stat 失败按 0 计：仍可解析，缓存键退化 (S8)
        key = (path, mtime)
        img = _IMAGE_CACHE.get(key)
        if img is None:
            img = cls._parse(path)
            _IMAGE_CACHE[key] = img
        return img

    @classmethod
    def _parse(cls, path: str) -> "ElfImage":
        with open(path, "rb") as f:
            data = f.read()
        # S1: 解析器第一道防线——非 ELF / 非 ELF64 输入直接拒绝。
        if data[:4] != b"\x7fELF":
            raise ValueError("not an ELF file")
        if data[4] != 2:  # EI_CLASS != ELFCLASS64
            raise ValueError("not ELF64")
        # S2: ehdr 字段定位（ELF-64 spec Figure 4-3）。
        e_type = struct.unpack_from("<H", data, 16)[0]
        e_shoff = struct.unpack_from("<Q", data, 40)[0]
        e_shnum = struct.unpack_from("<H", data, 60)[0]
        e_shstrndx = struct.unpack_from("<H", data, 62)[0]

        shdr_table = data[e_shoff:e_shoff + e_shnum * _SHDR_SIZE]
        str_hdr = struct.unpack_from(_SHDR_FMT, shdr_table,
                                     e_shstrndx * _SHDR_SIZE)
        shstrtab = data[str_hdr[4]:str_hdr[4] + str_hdr[5]]

        symtab_idx = None
        dynsym_idx = None
        for i, s in enumerate(struct.iter_unpack(_SHDR_FMT, shdr_table)):
            name = shstrtab[s[0]:].split(b"\x00")[0]
            if s[1] == _SHT_SYMTAB and name == b".symtab":
                symtab_idx = i
            elif s[1] == _SHT_DYNSYM and name == b".dynsym":
                dynsym_idx = i
        return cls(path, data, e_type, shdr_table, symtab_idx, dynsym_idx)

    def lookup(self, name: str) -> tuple[int, int] | None:
        """Find ``name`` in .symtab then .dynsym (S3), STT_NOTYPE/OBJECT
        only (S4). Returns ``(st_value, st_shndx)`` or None."""
        target = name.encode() + b"\x00"
        for idx in (self._symtab_idx, self._dynsym_idx):  # S3 搜索序
            if idx is None:
                continue
            found = self._search_section(idx, target)
            if found is not None:
                return found
        return None

    def _search_section(self, shdr_idx: int,
                        target: bytes) -> tuple[int, int] | None:
        s = struct.unpack_from(_SHDR_FMT, self._shdr_table,
                               shdr_idx * _SHDR_SIZE)
        # sh_link 指向该符号表的字符串表节
        str_s = struct.unpack_from(_SHDR_FMT, self._shdr_table,
                                   s[6] * _SHDR_SIZE)
        strtab = self._data[str_s[4]:str_s[4] + str_s[5]]
        symdata = self._data[s[4]:s[4] + s[5]]
        tlen = len(target)
        for st_name, st_info, _other, st_shndx, st_value, _size in \
                struct.iter_unpack(_SYM_FMT, symdata):
            if (st_info & 0xF) not in (_STT_NOTYPE, _STT_OBJECT):
                continue  # S4: 仅数据符号参与匹配（不撞同名函数）
            if strtab[st_name:st_name + tlen] == target:
                return st_value, st_shndx
        return None

    def read_at_symbol(self, name: str, length: int) -> bytes | None:
        """Read ``length`` bytes at the symbol's file offset
        (file_off = st_value − sh_addr + sh_offset, S7). None on miss/short."""
        found = self.lookup(name)
        if found is None:
            return None
        st_value, st_shndx = found
        sec = struct.unpack_from(_SHDR_FMT, self._shdr_table,
                                 st_shndx * _SHDR_SIZE)
        # S7: 符号虚拟地址 → 节内偏移 → 文件偏移（sec[3]=sh_addr,
        # sec[4]=sh_offset, sec[5]=sh_size）
        in_sec = st_value - sec[3]
        if in_sec + length > sec[5]:
            # 短读 → None：截断数据比失败更危险（半截版本号会解码出垃圾）
            return None
        file_off = sec[4] + in_sec
        data = self._data[file_off:file_off + length]
        return data if len(data) == length else None


def find_symbol(exe_path: str, name: str, pid: int) -> int:
    """Runtime address of ``name`` in the process ``pid``.

    Adds the load base for ET_DYN (PIE) executables (S5); returns 0 when
    the symbol is absent (S6).
    """
    img = ElfImage.open(exe_path)
    found = img.lookup(name)
    if found is None:
        return 0  # S6: 调用方以 0 判失败并抛 SymbolNotFound
    st_value = found[0]
    if img.e_type == _ET_DYN:
        # S5: PIE 的运行时地址 = st_value + 加载基址；ET_EXEC 固定地址不加
        return st_value + load_base(pid, exe_path)
    return st_value


def read_const(exe_path: str, name: str, length: int) -> bytes | None:
    """Read a data constant (e.g. ``Py_Version``) from the binary (S7)."""
    return ElfImage.open(exe_path).read_at_symbol(name, length)
