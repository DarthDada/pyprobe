"""Contract tests for target/symbols.py (S1–S9).

Two test-data tiers (测试数据纪律):
* synthetic ELF64 images built by ``build_elf`` below — every field placed
  from the ELF-64 spec (e_ident/e_type/e_shoff/shdr/sym layouts cited inline),
  so parse logic is pinned against an independent construction, not against
  the parser's own assumptions;
* the real running interpreter (spec-oracle S9) — end-to-end truth.
"""

import os
import struct
import sys

import pytest

from pyprobe.target import symbols
from pyprobe.target.symbols import ElfImage, find_symbol, read_const

# ---------------------------------------------------------------------------
# Synthetic ELF64 builder (authoritative layout: ELF-64 specification)
# ---------------------------------------------------------------------------

_SYM_SIZE = 24  # sizeof(Elf64_Sym): I B B H Q Q


def build_elf(path, *, e_type=3, symtab=(), dynsym=(), data=b"",
              data_vaddr=0x400000):
    """Write a minimal ELF64 image to ``path``.

    symtab/dynsym: iterables of (name: bytes, stt: int, shndx: int,
    value: int). Section indices: 1=.symtab 2=.strtab 3=.shstrtab 4=.data
    5=.dynsym 6=.dynstr (dynsym sections only when ``dynsym`` non-empty).
    """
    strtab = b"\x00"
    sym_bytes = bytearray()
    for name, stt, shndx, value in symtab:
        off = len(strtab)
        strtab += name + b"\x00"
        # st_info = (bind << 4) | type; LOCAL bind suffices for lookup tests
        sym_bytes += struct.pack("<IBBHQQ", off, stt, 0, shndx, value, 0)

    dynstr = b"\x00"
    dyn_bytes = bytearray()
    for name, stt, shndx, value in dynsym:
        off = len(dynstr)
        dynstr += name + b"\x00"
        dyn_bytes += struct.pack("<IBBHQQ", off, stt, 0, shndx, value, 0)

    names = [b".symtab", b".strtab", b".shstrtab", b".data"]
    if dynsym:
        names += [b".dynsym", b".dynstr"]
    shstrtab = b"\x00"
    name_off = {}
    for n in names:
        name_off[n] = len(shstrtab)
        shstrtab += n + b"\x00"

    # Sequential file layout after the 64-byte ehdr.
    parts = []
    cursor = 64

    def place(content):
        nonlocal cursor
        off = cursor
        parts.append(content)
        cursor += len(content)
        return off

    strtab_off = place(strtab)
    dynstr_off = place(dynstr) if dynsym else 0
    shstrtab_off = place(shstrtab)
    symtab_off = place(bytes(sym_bytes))
    dynsym_off = place(bytes(dyn_bytes)) if dynsym else 0
    data_off = place(data)

    shnum = 7 if dynsym else 5
    shoff = cursor

    def shdr(name, sh_type, flags, addr, offset, size, link=0, info=0,
             align=1, entsize=0):
        return struct.pack("<IIQQQQIIQQ", name_off.get(name, 0), sh_type,
                           flags, addr, offset, size, link, info, align,
                           entsize)

    shdrs = [bytes(64)]  # section 0: NULL
    shdrs.append(shdr(b".symtab", 2, 0, 0, symtab_off, len(sym_bytes),
                      link=2, align=8, entsize=_SYM_SIZE))
    shdrs.append(shdr(b".strtab", 3, 0, 0, strtab_off, len(strtab)))
    shdrs.append(shdr(b".shstrtab", 3, 0, 0, shstrtab_off, len(shstrtab)))
    shdrs.append(shdr(b".data", 1, 3, data_vaddr, data_off, len(data)))
    if dynsym:
        shdrs.append(shdr(b".dynsym", 11, 0, 0, dynsym_off, len(dyn_bytes),
                          link=6, align=8, entsize=_SYM_SIZE))
        shdrs.append(shdr(b".dynstr", 3, 0, 0, dynstr_off, len(dynstr)))

    # ehdr: magic+ident, then type@16 machine@18 version@20 entry@24
    # phoff@32 shoff@40 flags@48 ehsize@52 phentsize@54 phnum@56
    # shentsize@58 shnum@60 shstrndx@62 (ELF-64 spec, Figure 4-3)
    ehdr = bytearray(b"\x7fELF" + bytes([2, 1, 1]) + bytes(9))
    ehdr += struct.pack("<HHIQQQIHHHHHH", e_type, 62, 1, 0, 0, shoff, 0,
                        64, 0, 0, 64, shnum, 3)
    assert len(ehdr) == 64

    with open(path, "wb") as f:
        f.write(bytes(ehdr))
        for p in parts:
            f.write(p)
        for s in shdrs:
            f.write(s)
    return str(path)


@pytest.fixture
def elf_path(tmp_path):
    return str(tmp_path / "t.so")


class TestElfValidation:
    def test_bad_magic_rejected(self, elf_path):
        """S1：非 ELF 输入 → ValueError（解析器的第一道防线，防把任意
        文件当 ELF 读出垃圾偏移）。"""
        with open(elf_path, "wb") as f:
            f.write(b"NOTELF" + bytes(64))
        with pytest.raises(ValueError, match="not an ELF file"):
            ElfImage.open(elf_path)

    def test_non_elf64_rejected(self, elf_path):
        """S1：ELF32（EI_CLASS=1）→ ValueError（本工具只做 64 位布局）。"""
        with open(elf_path, "wb") as f:
            f.write(b"\x7fELF" + bytes([1, 1, 1]) + bytes(64))
        with pytest.raises(ValueError, match="not ELF64"):
            ElfImage.open(elf_path)


class TestLookup:
    def test_symtab_wins_over_dynsym(self, elf_path):
        """S3：同名符号两表并存时 .symtab 优先（symtab 含完整绑定信息，
        搜索顺序是复刻约束）。"""
        build_elf(elf_path,
                  symtab=[(b"sym_a", 1, 4, 0x1111)],
                  dynsym=[(b"sym_a", 1, 4, 0x2222)])
        img = ElfImage.open(elf_path)
        assert img.lookup("sym_a") == (0x1111, 4)

    def test_dynsym_searched_when_symtab_misses(self, elf_path):
        """S3 配套：.symtab 缺席（strip 过的二进制常态）时落到 .dynsym。"""
        build_elf(elf_path, dynsym=[(b"dyn_only", 1, 4, 0x2222)])
        img = ElfImage.open(elf_path)
        assert img.lookup("dyn_only") == (0x2222, 4)

    def test_function_symbols_filtered_out(self, elf_path):
        """S4：仅 STT_NOTYPE(0)/STT_OBJECT(1) 参与匹配——STT_FUNC(2) 被滤
        （旧语义：数据符号查找不撞同名函数）。"""
        build_elf(elf_path, symtab=[(b"func_sym", 2, 4, 0x3333),
                                    (b"obj_sym", 0, 4, 0x4444)])
        img = ElfImage.open(elf_path)
        assert img.lookup("func_sym") is None
        assert img.lookup("obj_sym") == (0x4444, 4)

    def test_missing_symbol_returns_none(self, elf_path):
        """S6 原语面：查找未命中 → None（find_symbol 包装为 0，分工明确）。"""
        build_elf(elf_path, symtab=[(b"present", 1, 4, 0x1)])
        assert ElfImage.open(elf_path).lookup("absent") is None


class TestReadAtSymbol:
    def test_file_offset_computation(self, elf_path):
        """S7：file_off = st_value − sh_addr + sh_offset（PIE 二进制里符号
        虚拟地址到文件偏移的换算，错位即读错数据）。"""
        build_elf(elf_path, data=b"\x0c\x03\x00\x00\x0d\x00\x00\x00",
                  data_vaddr=0x400000,
                  symtab=[(b"version_const", 1, 4, 0x400000)])
        img = ElfImage.open(elf_path)
        assert img.read_at_symbol("version_const", 8) == \
            b"\x0c\x03\x00\x00\x0d\x00\x00\x00"

    def test_short_read_returns_none(self, elf_path):
        """S7 失败模式：请求长度超过符号后剩余数据 → None（截断数据
        比失败更危险——解码半截版本号会产出看似合法的垃圾）。"""
        build_elf(elf_path, data=b"\x0c\x03", data_vaddr=0x400000,
                  symtab=[(b"version_const", 1, 4, 0x400000)])
        img = ElfImage.open(elf_path)
        assert img.read_at_symbol("version_const", 8) is None

    def test_missing_symbol_returns_none(self, elf_path):
        """S7 失败模式：符号不存在 → None。"""
        build_elf(elf_path, symtab=[(b"present", 1, 4, 0x400000)])
        assert ElfImage.open(elf_path).read_at_symbol("absent", 8) is None


class TestCache:
    def test_second_open_returns_cached_image(self, elf_path):
        """S8：(path, mtime) 键缓存——二次 open 同一文件返回同一对象
        （节头表只解码一次，快照期性能守护）。"""
        build_elf(elf_path, symtab=[(b"s", 1, 4, 0x1)])
        assert ElfImage.open(elf_path) is ElfImage.open(elf_path)

    def test_mtime_change_invalidates(self, elf_path):
        """S8 配套：mtime 变化即重新解析（二进制被替换后不得用旧布局）。"""
        build_elf(elf_path, symtab=[(b"s", 1, 4, 0x1)])
        first = ElfImage.open(elf_path)
        os.utime(elf_path, (0, 1_700_000_000))
        assert ElfImage.open(elf_path) is not first


class TestFindSymbol:
    def test_et_dyn_adds_load_base(self, elf_path, monkeypatch):
        """S5：ET_DYN(PIE) 符号 = st_value + load_base（进程内运行时地址，
        不加基址会在 PIE 目标上读到全错地址）。"""
        build_elf(elf_path, e_type=3, symtab=[(b"_PyRuntime", 1, 4, 0x3000)])
        monkeypatch.setattr(symbols, "load_base",
                            lambda pid, path: 0x555555554000)
        assert find_symbol(elf_path, "_PyRuntime", 1234) == 0x555555554000 + 0x3000

    def test_et_exec_no_load_base(self, elf_path, monkeypatch):
        """S5 配套：ET_EXEC(2) 固定加载地址，不加基址。"""
        build_elf(elf_path, e_type=2, symtab=[(b"_PyRuntime", 1, 4, 0x3000)])
        monkeypatch.setattr(
            symbols, "load_base",
            lambda pid, path: pytest.fail("load_base must not be consulted"))
        assert find_symbol(elf_path, "_PyRuntime", 1234) == 0x3000

    def test_missing_returns_zero(self, elf_path):
        """S6：符号缺失 → 0（调用方以 0 判失败并抛 SymbolNotFound）。"""
        build_elf(elf_path, symtab=[(b"present", 1, 4, 0x1)])
        assert find_symbol(elf_path, "absent", 1234) == 0


class TestRealInterpreter:
    """S9 (spec-oracle)：真实运行中解释器——解析逻辑的最终真理。"""

    exe = os.readlink("/proc/self/exe")
    pid = os.getpid()

    def test_find_pyruntime(self):
        """S9：_PyRuntime 在真实 CPython 二进制中可定位且非 0。"""
        assert find_symbol(self.exe, "_PyRuntime", self.pid) != 0

    def test_find_missing_symbol(self):
        """S9 失败模式：真实二进制中的缺失符号 → 0。"""
        assert find_symbol(self.exe, "__nonexistent_symbol__", self.pid) == 0

    def test_read_py_version(self):
        """S9：Py_Version 常量 8 字节小端解码 == 运行中解释器版本。"""
        data = read_const(self.exe, "Py_Version", 8)
        assert data is not None and len(data) == 8
        from pyprobe.target.identity import decode_py_version
        v = sys.version_info
        assert decode_py_version(int.from_bytes(data, "little")) == (
            f"{v.major}.{v.minor}.{v.micro}")

    def test_read_missing_const(self):
        """S9 失败模式：缺失常量 → None。"""
        assert read_const(self.exe, "__nope__", 8) is None
