"""ELF symbol lookup — manual ELF64 parsing with struct module (no deps).

Scope: ELF section-header + symbol-table parsing only. Process-level
metadata readers (``/proc/<pid>/cmdline``, ``Py_Version`` decoding) live
in ``procmeta.py`` — they were extracted here to keep the dependency
graph honest (TODO §8.6).
"""

import os
import struct
from collections import namedtuple


_SHDR_FMT = "<IIQQQQIIQQ"
_SHDR_SIZE = 64
_SYM_FMT = "<IBBHQQ"


def _read_ehdr(f):
    f.seek(0)
    data = f.read(64)
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    ei_class = data[4]
    if ei_class != 2:
        raise ValueError("not ELF64")
    e_shoff = struct.unpack_from("<Q", data, 40)[0]
    e_shnum = struct.unpack_from("<H", data, 60)[0]
    e_shstrndx = struct.unpack_from("<H", data, 62)[0]
    e_type = struct.unpack_from("<H", data, 16)[0]
    return e_shoff, e_shnum, e_shstrndx, e_type


_ELFInfo = namedtuple("_ELFInfo", [
    "e_type", "shdr_table", "shstrtab", "symtab_idx", "dynsym_idx",
])

_elf_cache = {}


def _get_elf_info(exe_path):
    """Parse ELF once; cache by (path, mtime). Shared by find_symbol
    and read_const so the section header table is read + decoded once.
    load_base is pid-dependent and computed separately in find_symbol."""
    try:
        mtime = os.stat(exe_path).st_mtime
    except OSError:
        mtime = 0
    key = (exe_path, mtime)
    info = _elf_cache.get(key)
    if info is not None:
        return info

    with open(exe_path, "rb") as f:
        e_shoff, e_shnum, e_shstrndx, e_type = _read_ehdr(f)

        # read the entire section header table in one shot
        f.seek(e_shoff)
        shdr_table = f.read(e_shnum * _SHDR_SIZE)

        # parse shstrtab (section name string table)
        shstr_hdr = struct.unpack_from(_SHDR_FMT, shdr_table,
                                       e_shstrndx * _SHDR_SIZE)
        f.seek(shstr_hdr[4])
        shstrtab = f.read(shstr_hdr[5])

        symtab_idx = None
        dynsym_idx = None
        for i, s in enumerate(struct.iter_unpack(_SHDR_FMT, shdr_table)):
            name = shstrtab[s[0]:].split(b"\x00")[0]
            if s[1] == 2 and name == b".symtab":
                symtab_idx = i
            elif s[1] == 11 and name == b".dynsym":
                dynsym_idx = i

    info = _ELFInfo(e_type, shdr_table, shstrtab, symtab_idx, dynsym_idx)
    _elf_cache[key] = info
    return info


def _search_symbols(f, info, shdr_idx, symname_bytes):
    s = struct.unpack_from(_SHDR_FMT, info.shdr_table, shdr_idx * _SHDR_SIZE)
    sh_offset = s[4]
    sh_size = s[5]
    sh_link = s[6]

    str_s = struct.unpack_from(_SHDR_FMT, info.shdr_table, sh_link * _SHDR_SIZE)
    f.seek(str_s[4])
    strtab = f.read(str_s[5])

    f.seek(sh_offset)
    symdata = f.read(sh_size)

    target = symname_bytes + b"\x00"
    tlen = len(target)
    for st_name, st_info, st_other, st_shndx, st_value, st_size in \
            struct.iter_unpack(_SYM_FMT, symdata):
        stt = st_info & 0xF
        if stt not in (1, 0):
            continue
        if strtab[st_name:st_name + tlen] == target:
            return st_value, st_shndx
    return None


def find_symbol(exe_path, symname, pid):
    symname_b = symname.encode()
    info = _get_elf_info(exe_path)

    load_base = 0
    if info.e_type == 3:
        load_base = _get_load_base(pid, exe_path)

    with open(exe_path, "rb") as f:
        for idx in (info.symtab_idx, info.dynsym_idx):
            if idx is None:
                continue
            found = _search_symbols(f, info, idx, symname_b)
            if found:
                return found[0] + load_base
    return 0


def read_const(exe_path, symname, length):
    symname_b = symname.encode()
    info = _get_elf_info(exe_path)

    with open(exe_path, "rb") as f:
        for idx in (info.symtab_idx, info.dynsym_idx):
            if idx is None:
                continue
            found = _search_symbols(f, info, idx, symname_b)
            if found:
                st_value, st_shndx = found
                sec = struct.unpack_from(_SHDR_FMT, info.shdr_table,
                                         st_shndx * _SHDR_SIZE)
                file_off = st_value - sec[3] + sec[4]
                f.seek(file_off)
                data = f.read(length)
                return data if len(data) == length else None
    return None


def _get_load_base(pid, exe_path):
    try:
        with open(f"/proc/{pid}/maps") as f:
            for line in f:
                path_start = line.find("/")
                if path_start < 0:
                    continue
                path = line[path_start:].strip()
                if path == exe_path or path == exe_path + " (deleted)":
                    base_str = line.split("-")[0]
                    return int(base_str, 16)
    except OSError:
        pass
    return 0
