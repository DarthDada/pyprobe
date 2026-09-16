"""ELF symbol lookup — manual ELF64 parsing with struct module (no deps)."""

import struct

from .memory import RemoteReader


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


_SHDR_FMT = "<IIQQQQIIQQ"
_SHDR_SIZE = 64
_SYM_FMT = "<IBBHQQ"
_SYM_SIZE = 24


def _read_shdr(f, shoff, idx):
    f.seek(shoff + idx * _SHDR_SIZE)
    return struct.unpack(_SHDR_FMT, f.read(_SHDR_SIZE))


def _find_symtables(f, e_shoff, e_shnum, e_shstrndx):
    shdr = _read_shdr(f, e_shoff, e_shstrndx)
    f.seek(shdr[4])
    shstrtab = f.read(shdr[5])

    symtab_idx = None
    dynsym_idx = None
    for i in range(e_shnum):
        s = _read_shdr(f, e_shoff, i)
        name = shstrtab[s[0]:].split(b"\x00")[0]
        if s[1] == 2 and name == b".symtab":
            symtab_idx = i
        elif s[1] == 11 and name == b".dynsym":
            dynsym_idx = i
    return symtab_idx, dynsym_idx


def _search_symbols(f, shdr_idx, e_shoff, symname_bytes):
    s = _read_shdr(f, e_shoff, shdr_idx)
    sh_offset = s[4]
    sh_size = s[5]
    sh_link = s[6]

    str_s = _read_shdr(f, e_shoff, sh_link)
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
    with open(exe_path, "rb") as f:
        e_shoff, e_shnum, e_shstrndx, e_type = _read_ehdr(f)

        load_base = 0
        if e_type == 3:
            load_base = _get_load_base(pid, exe_path)

        symtab_idx, dynsym_idx = _find_symtables(f, e_shoff, e_shnum, e_shstrndx)

        for idx in (symtab_idx, dynsym_idx):
            if idx is None:
                continue
            found = _search_symbols(f, idx, e_shoff, symname_b)
            if found:
                return found[0] + load_base
    return 0


def read_const(exe_path, symname, length):
    symname_b = symname.encode()
    with open(exe_path, "rb") as f:
        e_shoff, e_shnum, e_shstrndx, e_type = _read_ehdr(f)

        symtab_idx, dynsym_idx = _find_symtables(f, e_shoff, e_shnum, e_shstrndx)

        for idx in (symtab_idx, dynsym_idx):
            if idx is None:
                continue
            found = _search_symbols(f, idx, e_shoff, symname_b)
            if found:
                st_value, st_shndx = found
                sec = _read_shdr(f, e_shoff, st_shndx)
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


def read_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            data = f.read()
        return data.replace(b"\x00", b" ").rstrip(b" ").decode("utf-8", "replace")
    except OSError:
        return None


def decode_py_version(hexval):
    major = (hexval >> 24) & 0xFF
    minor = (hexval >> 16) & 0xFF
    micro = (hexval >> 8) & 0xFF
    return f"{major}.{minor}.{micro}"
