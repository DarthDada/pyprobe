/*
 * py_stack_dump.c - pyprobe: inspect a running CPython 3.12 process.
 *
 * Build:
 *   make -C reference/c
 *
 * Usage:
 *   ./pyprobe <pid>            Python stack dump (py-spy dump style)
 *   ./pyprobe <pid> --native   Native stack dump (gdb "thread apply all bt" style)
 *
 * Python mode works by locating the exported `_PyRuntime` symbol in the
 * target's python3.12 binary, then walking:
 *   _PyRuntime -> interpreters.main -> threads.head (PyThreadState list)
 *   tstate->cframe->current_frame (_PyInterpreterFrame chain via ->previous)
 *   frame->f_code (PyCodeObject) -> co_qualname / co_filename / co_firstlineno
 *   + line-number resolution from co_linetable (PEP 626).
 * Uses process_vm_readv (no ptrace attach needed).
 *
 * Native mode uses elfutils libdwfl to ptrace-attach all threads and unwind
 * the native call stack via .eh_frame/.debug_frame, resolving symbols from
 * .symtab/.dynsym — matching gdb's "thread apply all bt" output format.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/uio.h>
#include <sys/stat.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <dirent.h>
#include <elf.h>
#include <limits.h>

/*
 * Architecture support: x86-64 and aarch64.
 *
 * Both are 64-bit LP64 with ELF64 binaries.  CPython uses 30-bit digits
 * on both.  If 32-bit architectures need to be supported in the future,
 * add Elf32 variants here.
 */
#if defined(__x86_64__) || defined(__aarch64__)
  #define PYPROBE_ELF64     1
  #define PyProbe_Ehdr       Elf64_Ehdr
  #define PyProbe_Shdr       Elf64_Shdr
  #define PyProbe_Sym        Elf64_Sym
  #define PyProbe_ST_TYPE    ELF64_ST_TYPE
#else
  #error "Unsupported architecture: only x86-64 and aarch64 are supported"
#endif

/* Pull in the exact struct layouts so we can use offsetof() at compile time.
   Python.h already pulls in the CPython internal headers via the include chain. */
#include <Python.h>
#include <internal/pycore_runtime.h>
#include <internal/pycore_interp.h>
#include <internal/pycore_frame.h>
#include <internal/pycore_code.h>
#include <internal/pycore_dict.h>
#include <internal/pycore_object.h>

/* ------------------------------------------------------------------ */
/* Limits                                                              */
/* ------------------------------------------------------------------ */

#define MAX_FRAMES    100
#define MAX_THREADS   256
#define MAX_NAME_LEN  128
#define MAX_STR_LEN   (1 << 20)   /* 1 MB cap for remote strings */

/* ------------------------------------------------------------------ */
/* process_vm_readv wrapper with partial-read retry                   */
/* ------------------------------------------------------------------ */

static ssize_t read_remote(pid_t pid, uintptr_t addr, void *buf, size_t len)
{
    struct iovec local  = { buf, len };
    struct iovec remote = { (void *)addr, len };
    size_t total = 0;

    while (total < len) {
        local.iov_base  = (char *)buf + total;
        local.iov_len   = len - total;
        remote.iov_base = (void *)(addr + total);
        remote.iov_len  = len - total;

        ssize_t n = process_vm_readv(pid, &local, 1, &remote, 1, 0);
        if (n < 0) {
            if (errno == EFAULT || errno == EPERM || errno == ESRCH)
                return -1;
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (n == 0)
            return total;          /* unexpected EOF */
        total += (size_t)n;
    }
    return (ssize_t)total;
}

/* ------------------------------------------------------------------ */
/* ELF symbol lookup  (parse .symtab/.dynsym of the target binary)    */
/* ------------------------------------------------------------------ */

/* Read /proc/<pid>/cmdline and format as a space-separated string. */
static int read_cmdline(pid_t pid, char *buf, size_t cap)
{
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/cmdline", pid);
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    ssize_t n = read(fd, buf, cap - 1);
    close(fd);
    if (n <= 0) return -1;
    buf[n] = '\0';
    /* Replace NUL separators between argv entries with spaces */
    for (ssize_t i = 0; i < n - 1; i++)
        if (buf[i] == '\0') buf[i] = ' ';
    /* Strip trailing NUL(s) */
    while (n > 0 && buf[n - 1] == '\0') buf[--n] = '\0';
    return 0;
}

/* Decode a PY_VERSION_HEX value into major.minor.micro string. */
static void decode_py_version(unsigned long hex, char *buf, size_t cap)
{
    int major = (hex >> 24) & 0xFF;
    int minor = (hex >> 16) & 0xFF;
    int micro = (hex >>  8) & 0xFF;
    snprintf(buf, cap, "%d.%d.%d", major, minor, micro);
}

/* Read a compile-time const value from an ELF file (without accessing the
   remote process).  Useful for Py_Version which is a const unsigned long. */
static int read_const_from_elf(const char *path, const char *symname,
                                void *buf, size_t len)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;

    PyProbe_Ehdr ehdr;
    if (pread(fd, &ehdr, sizeof(ehdr), 0) != (ssize_t)sizeof(ehdr)) {
        close(fd); return -1;
    }

    size_t shsize = ehdr.e_shnum * sizeof(PyProbe_Shdr);
    PyProbe_Shdr *shdrs = malloc(shsize);
    if (!shdrs) { close(fd); return -1; }
    if (pread(fd, shdrs, shsize, ehdr.e_shoff) != (ssize_t)shsize) {
        free(shdrs); close(fd); return -1;
    }

    const PyProbe_Shdr *shstr_sh = &shdrs[ehdr.e_shstrndx];
    char *shstrtab = malloc(shstr_sh->sh_size);
    if (!shstrtab) { free(shdrs); close(fd); return -1; }
    pread(fd, shstrtab, shstr_sh->sh_size, shstr_sh->sh_offset);

    PyProbe_Shdr *symtab_sh = NULL, *dynsym_sh = NULL;
    for (int i = 0; i < ehdr.e_shnum; i++) {
        const char *name = shstrtab + shdrs[i].sh_name;
        if (shdrs[i].sh_type == SHT_SYMTAB && strcmp(name, ".symtab") == 0)
            symtab_sh = &shdrs[i];
        else if (shdrs[i].sh_type == SHT_DYNSYM && strcmp(name, ".dynsym") == 0)
            dynsym_sh = &shdrs[i];
    }

    int found = -1;
    const PyProbe_Shdr *tables[] = { symtab_sh, dynsym_sh, NULL };
    for (int t = 0; tables[t] && found != 0; t++) {
        const PyProbe_Shdr *st = tables[t];
        const PyProbe_Shdr *strst = &shdrs[st->sh_link];

        char *strtab = malloc(strst->sh_size);
        if (!strtab) continue;
        pread(fd, strtab, strst->sh_size, strst->sh_offset);

        size_t nsym = st->sh_size / sizeof(PyProbe_Sym);
        PyProbe_Sym *syms = malloc(st->sh_size);
        if (!syms) { free(strtab); continue; }
        pread(fd, syms, st->sh_size, st->sh_offset);

        for (size_t i = 0; i < nsym; i++) {
            if (PyProbe_ST_TYPE(syms[i].st_info) != STT_OBJECT &&
                PyProbe_ST_TYPE(syms[i].st_info) != STT_NOTYPE)
                continue;
            const char *name = strtab + syms[i].st_name;
            if (strcmp(name, symname) == 0) {
                /* Convert virtual address to file offset via section header */
                PyProbe_Shdr *sec = &shdrs[syms[i].st_shndx];
                uintptr_t file_off = syms[i].st_value - sec->sh_addr + sec->sh_offset;
                if (pread(fd, buf, len, file_off) == (ssize_t)len)
                    found = 0;
                break;
            }
        }
        free(syms);
        free(strtab);
    }

    free(shstrtab);
    free(shdrs);
    close(fd);
    return found;
}

/* Read the load-base address of the main executable from /proc/<pid>/maps.
   For PIE (ET_DYN) binaries, ELF symbol values are relative to this base. */
static uintptr_t get_load_base(pid_t pid, const char *exe_path)
{
    char maps_path[64];
    snprintf(maps_path, sizeof(maps_path), "/proc/%d/maps", pid);
    FILE *f = fopen(maps_path, "r");
    if (!f) return 0;

    char line[512];
    uintptr_t base = 0;
    size_t exe_len = strlen(exe_path);

    while (fgets(line, sizeof(line), f)) {
        char *path_start = strchr(line, '/');
        if (!path_start) continue;
        line[strcspn(line, "\n")] = '\0';
        if (strncmp(path_start, exe_path, exe_len) == 0 &&
            (path_start[exe_len] == '\0' ||
             strcmp(path_start + exe_len, " (deleted)") == 0)) {
            sscanf(line, "%lx-", &base);
            break;
        }
    }
    fclose(f);
    return base;
}

static uintptr_t find_symbol_in_elf(const char *path, const char *symname, pid_t pid)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) { perror("open elf"); return 0; }

    PyProbe_Ehdr ehdr;
    if (pread(fd, &ehdr, sizeof(ehdr), 0) != (ssize_t)sizeof(ehdr)) {
        fprintf(stderr, "bad ELF header\n"); close(fd); return 0;
    }

    /* For PIE/shared objects (ET_DYN), symbol values are offsets from the
       load base.  For regular executables (ET_EXEC), they are absolute. */
    uintptr_t load_base = 0;
    if (ehdr.e_type == ET_DYN)
        load_base = get_load_base(pid, path);

    /* Read section headers */
    size_t shsize = ehdr.e_shnum * sizeof(PyProbe_Shdr);
    PyProbe_Shdr *shdrs = malloc(shsize);
    if (!shdrs) { close(fd); return 0; }
    if (pread(fd, shdrs, shsize, ehdr.e_shoff) != (ssize_t)shsize) {
        fprintf(stderr, "bad section headers\n"); free(shdrs); close(fd); return 0;
    }

    /* Find the section-header-string-table */
    const PyProbe_Shdr *shstr_sh = &shdrs[ehdr.e_shstrndx];
    char *shstrtab = malloc(shstr_sh->sh_size);
    if (!shstrtab) { free(shdrs); close(fd); return 0; }
    pread(fd, shstrtab, shstr_sh->sh_size, shstr_sh->sh_offset);

    PyProbe_Shdr *symtab_sh = NULL;
    PyProbe_Shdr *dynsym_sh = NULL;

    for (int i = 0; i < ehdr.e_shnum; i++) {
        const char *name = shstrtab + shdrs[i].sh_name;
        if (shdrs[i].sh_type == SHT_SYMTAB && strcmp(name, ".symtab") == 0)
            symtab_sh = &shdrs[i];
        else if (shdrs[i].sh_type == SHT_DYNSYM && strcmp(name, ".dynsym") == 0)
            dynsym_sh = &shdrs[i];
    }

    /* Search .symtab first, then .dynsym if not found. */
    uintptr_t result = 0;

    const PyProbe_Shdr *tables[] = { symtab_sh, dynsym_sh, NULL };
    for (int t = 0; tables[t] && result == 0; t++) {
        const PyProbe_Shdr *st = tables[t];
        const PyProbe_Shdr *strst = &shdrs[st->sh_link];

        char *strtab = malloc(strst->sh_size);
        if (!strtab) continue;
        pread(fd, strtab, strst->sh_size, strst->sh_offset);

        size_t nsym = st->sh_size / sizeof(PyProbe_Sym);
        PyProbe_Sym *syms = malloc(st->sh_size);
        if (!syms) { free(strtab); continue; }
        pread(fd, syms, st->sh_size, st->sh_offset);

        for (size_t i = 0; i < nsym; i++) {
            if (PyProbe_ST_TYPE(syms[i].st_info) != STT_OBJECT &&
                PyProbe_ST_TYPE(syms[i].st_info) != STT_NOTYPE)
                continue;
            const char *name = strtab + syms[i].st_name;
            if (strcmp(name, symname) == 0) {
                result = syms[i].st_value + load_base;
                break;
            }
        }
        free(syms);
        free(strtab);
    }

    free(shstrtab);
    free(shdrs);
    close(fd);
    return result;
}

/* ------------------------------------------------------------------ */
/* Higher-level remote readers                                         */
/* ------------------------------------------------------------------ */

static int r_ptr(pid_t pid, uintptr_t addr, uintptr_t *out)
{
    return read_remote(pid, addr, out, sizeof(void *)) == (ssize_t)sizeof(void *) ? 0 : -1;
}

static int r_int(pid_t pid, uintptr_t addr, int *out)
{
    return read_remote(pid, addr, out, sizeof(int)) == (ssize_t)sizeof(int) ? 0 : -1;
}

/* ------------------------------------------------------------------ */
/* PyLong reader (Python 3.12 layout: lv_tag + ob_digit[])            */
/* ------------------------------------------------------------------ */

/* Forward declarations — read_pyunicode/read_pybytes are defined later */
static char *read_pyunicode(pid_t pid, uintptr_t obj_addr);
static char *read_pybytes(pid_t pid, uintptr_t obj_addr, Py_ssize_t *out_len);

static int read_pylong(pid_t pid, uintptr_t addr, unsigned long *out)
{
    PyLongObject obj;
    if (read_remote(pid, addr, &obj, sizeof(obj)) != (ssize_t)sizeof(obj))
        return -1;

    uintptr_t tag = obj.long_value.lv_tag;
    Py_ssize_t size = (Py_ssize_t)(tag >> 3);
    /* sign: 0=positive, 1=zero, 2=negative */

    if (size == 0) {
        *out = 0;
        return 0;
    }

    if (size == 1) {
        *out = (unsigned long)obj.long_value.ob_digit[0];
        return 0;
    }

    if (size == 2) {
        digit d0 = obj.long_value.ob_digit[0];
        digit d1;
        uintptr_t d1_addr = addr + offsetof(PyLongObject, long_value.ob_digit)
                             + sizeof(digit);
        if (read_remote(pid, d1_addr, &d1, sizeof(d1)) != (ssize_t)sizeof(d1))
            return -1;
        *out = (unsigned long)d0 | ((unsigned long)d1 << 30);
        return 0;
    }

    return -1;  /* too large */
}

/* ------------------------------------------------------------------ */
/* Dict iterator (Python 3.11+ combined/split tables)                  */
/* ------------------------------------------------------------------ */

typedef struct {
    pid_t pid;
    uint8_t kind;       /* 0=DICT_KEYS_GENERAL, 1=UNICODE, 2=SPLIT */
    int index;
    Py_ssize_t nentries;
    uintptr_t values;   /* 0=combined table, else split-table values array */
    char *entries;      /* pre-read entries buffer (one bulk read) */
    size_t entry_size;
} DictIter;

static int dict_iter_from_dict(DictIter *it, pid_t pid, uintptr_t dict_addr)
{
    it->entries = NULL;

    PyDictObject dict;
    if (read_remote(pid, dict_addr, &dict, sizeof(dict)) != (ssize_t)sizeof(dict))
        return -1;

    uintptr_t keys_addr = (uintptr_t)dict.ma_keys;
    if (keys_addr == 0)
        return -1;

    struct _dictkeysobject keys;
    if (read_remote(pid, keys_addr, &keys, sizeof(keys)) != (ssize_t)sizeof(keys))
        return -1;

    size_t indices_size = (size_t)1 << keys.dk_log2_index_bytes;
    uintptr_t entries_addr = keys_addr + indices_size + sizeof(struct _dictkeysobject);

    it->pid = pid;
    it->kind = keys.dk_kind;
    it->index = 0;
    it->nentries = keys.dk_nentries;
    it->values = (uintptr_t)dict.ma_values;
    it->entry_size = (it->kind == 0) ? sizeof(PyDictKeyEntry)
                                     : sizeof(PyDictUnicodeEntry);

    /* Bulk-read all entries in one syscall */
    size_t total = (size_t)it->nentries * it->entry_size;
    if (total > 0 && total <= MAX_STR_LEN) {
        it->entries = malloc(total);
        if (it->entries) {
            if (read_remote(pid, entries_addr, it->entries, total) != (ssize_t)total) {
                free(it->entries);
                it->entries = NULL;
            }
        }
    }
    return 0;
}

static int dict_iter_from_managed_values(DictIter *it, pid_t pid,
                                          uintptr_t values_addr,
                                          uintptr_t type_addr)
{
    it->entries = NULL;

    PyHeapTypeObject ht;
    if (read_remote(pid, type_addr, &ht, sizeof(ht)) != (ssize_t)sizeof(ht))
        return -1;

    uintptr_t keys_addr = (uintptr_t)ht.ht_cached_keys;
    if (keys_addr == 0)
        return -1;

    struct _dictkeysobject keys;
    if (read_remote(pid, keys_addr, &keys, sizeof(keys)) != (ssize_t)sizeof(keys))
        return -1;

    size_t indices_size = (size_t)1 << keys.dk_log2_index_bytes;
    uintptr_t entries_addr = keys_addr + indices_size + sizeof(struct _dictkeysobject);

    it->pid = pid;
    it->kind = keys.dk_kind;
    it->index = 0;
    it->nentries = keys.dk_nentries;
    it->values = values_addr;
    it->entry_size = (it->kind == 0) ? sizeof(PyDictKeyEntry)
                                     : sizeof(PyDictUnicodeEntry);

    /* Bulk-read all entries in one syscall */
    size_t total = (size_t)it->nentries * it->entry_size;
    if (total > 0 && total <= MAX_STR_LEN) {
        it->entries = malloc(total);
        if (it->entries) {
            if (read_remote(pid, entries_addr, it->entries, total) != (ssize_t)total) {
                free(it->entries);
                it->entries = NULL;
            }
        }
    }
    return 0;
}

static int get_instance_dict_iter(DictIter *it, pid_t pid, uintptr_t obj_addr)
{
    uintptr_t type_addr;
    if (r_ptr(pid, obj_addr + offsetof(PyObject, ob_type), &type_addr) != 0)
        return -1;

    unsigned long flags;
    if (read_remote(pid, type_addr + offsetof(PyTypeObject, tp_flags),
                    &flags, sizeof(flags)) != (ssize_t)sizeof(flags))
        return -1;

    if (flags & Py_TPFLAGS_MANAGED_DICT) {
        /* Python 3.12: tagged dict/values pointer at obj - 3*sizeof(void*) */
        uintptr_t tagged;
        if (r_ptr(pid, obj_addr - 3 * sizeof(void *), &tagged) != 0)
            return -1;
        if (tagged == 0)
            return -1;
        if (tagged & 1) {
            /* inline values mode */
            return dict_iter_from_managed_values(it, pid, tagged + 1, type_addr);
        } else {
            /* dict mode */
            return dict_iter_from_dict(it, pid, tagged);
        }
    }

    /* Traditional: tp_dictoffset */
    Py_ssize_t dictoffset;
    if (read_remote(pid, type_addr + offsetof(PyTypeObject, tp_dictoffset),
                    &dictoffset, sizeof(dictoffset)) != (ssize_t)sizeof(dictoffset))
        return -1;
    if (dictoffset == 0)
        return -1;

    uintptr_t dict_addr;
    if (r_ptr(pid, obj_addr + dictoffset, &dict_addr) != 0)
        return -1;
    if (dict_addr == 0)
        return -1;

    return dict_iter_from_dict(it, pid, dict_addr);
}

static int dict_iter_next(DictIter *it, uintptr_t *key, uintptr_t *value)
{
    while (it->index < it->nentries) {
        int idx = it->index++;

        if (!it->entries)
            return 0;

        uintptr_t k = 0, v = 0;
        if (it->kind == 0) {  /* DICT_KEYS_GENERAL */
            PyDictKeyEntry *e = (PyDictKeyEntry *)
                (it->entries + (size_t)idx * it->entry_size);
            k = (uintptr_t)e->me_key;
            v = (uintptr_t)e->me_value;
        } else {  /* DICT_KEYS_UNICODE / SPLIT */
            PyDictUnicodeEntry *e = (PyDictUnicodeEntry *)
                (it->entries + (size_t)idx * it->entry_size);
            k = (uintptr_t)e->me_key;
            v = (uintptr_t)e->me_value;
        }

        if (k == 0)
            continue;

        /* For split tables, read value from values array */
        if (it->values != 0) {
            uintptr_t val_addr = it->values + (size_t)idx * sizeof(void *);
            if (r_ptr(it->pid, val_addr, &v) != 0)
                continue;
        }

        *key = k;
        *value = v;
        return 1;
    }
    return 0;
}

static void dict_iter_free(DictIter *it)
{
    free(it->entries);
    it->entries = NULL;
}

/* ------------------------------------------------------------------ */
/* Thread name lookup via threading._active                            */
/* ------------------------------------------------------------------ */

typedef struct {
    unsigned long thread_id;    /* PyThreadState.thread_id (pthread_t) */
    char name[MAX_NAME_LEN];
} ThreadName;

static int compare_threadname_by_tid(const void *a, const void *b)
{
    const ThreadName *ta = a, *tb = b;
    if (ta->thread_id < tb->thread_id) return -1;
    if (ta->thread_id > tb->thread_id) return 1;
    return 0;
}

static int get_thread_names(pid_t pid, uintptr_t interp_addr,
                            ThreadName *names, int max_names)
{
    int count = 0;
    uintptr_t key, value;
    DictIter mod_it = {0};
    DictIter dict_it = {0};
    DictIter active_it = {0};
    DictIter inst_it = {0};

    /* 1. interp->imports.modules (sys.modules dict) */
    uintptr_t modules_addr;
    if (r_ptr(pid, interp_addr + offsetof(PyInterpreterState, imports)
                            + offsetof(struct _import_state, modules),
               &modules_addr) != 0 || modules_addr == 0)
        goto done;

    /* 2. Find "threading" module in sys.modules */
    if (dict_iter_from_dict(&mod_it, pid, modules_addr) != 0)
        goto done;

    while (dict_iter_next(&mod_it, &key, &value)) {
        char *mod_name = read_pyunicode(pid, key);
        if (!mod_name) continue;
        int is_threading = (strcmp(mod_name, "threading") == 0);
        free(mod_name);
        if (!is_threading) continue;

        /* 3. Read module __dict__ via tp_dictoffset */
        uintptr_t mod_type_addr;
        if (r_ptr(pid, value + offsetof(PyObject, ob_type), &mod_type_addr) != 0)
            goto done;

        Py_ssize_t dictoffset;
        if (read_remote(pid, mod_type_addr + offsetof(PyTypeObject, tp_dictoffset),
                        &dictoffset, sizeof(dictoffset)) != (ssize_t)sizeof(dictoffset))
            goto done;
        if (dictoffset == 0)
            goto done;

        uintptr_t mod_dict_addr;
        if (r_ptr(pid, value + dictoffset, &mod_dict_addr) != 0 || mod_dict_addr == 0)
            goto done;

        /* 4. Find "_active" in module __dict__ */
        if (dict_iter_from_dict(&dict_it, pid, mod_dict_addr) != 0)
            goto done;

        while (dict_iter_next(&dict_it, &key, &value)) {
            char *var_name = read_pyunicode(pid, key);
            if (!var_name) continue;
            int is_active = (strcmp(var_name, "_active") == 0);
            free(var_name);
            if (!is_active) continue;

            /* 5. Iterate _active: {thread_id(int): Thread object} */
            if (dict_iter_from_dict(&active_it, pid, value) != 0)
                goto done;

            while (dict_iter_next(&active_it, &key, &value)) {
                if (count >= max_names)
                    goto done;

                unsigned long tid;
                if (read_pylong(pid, key, &tid) != 0)
                    continue;

                /* Read Thread object's _name attribute */
                if (get_instance_dict_iter(&inst_it, pid, value) != 0)
                    continue;

                uintptr_t attr_key, attr_value;
                while (dict_iter_next(&inst_it, &attr_key, &attr_value)) {
                    char *attr_name = read_pyunicode(pid, attr_key);
                    if (!attr_name) continue;
                    int is_name = (strcmp(attr_name, "_name") == 0);
                    free(attr_name);
                    if (!is_name) continue;

                    char *thread_name = read_pyunicode(pid, attr_value);
                    if (thread_name) {
                        names[count].thread_id = tid;
                        strncpy(names[count].name, thread_name, MAX_NAME_LEN - 1);
                        names[count].name[MAX_NAME_LEN - 1] = '\0';
                        free(thread_name);
                        count++;
                    }
                    break;
                }
                dict_iter_free(&inst_it);
            }
            dict_iter_free(&active_it);
            goto done;  /* found _active, done */
        }
        dict_iter_free(&dict_it);
        goto done;  /* found threading module, done */
    }

done:
    dict_iter_free(&mod_it);
    dict_iter_free(&dict_it);
    dict_iter_free(&active_it);
    dict_iter_free(&inst_it);
    return count;
}

/*
 * Read a remote PyUnicodeObject and return its content as a heap-allocated
 * C string (UTF-8). Handles compact-ascii, compact-non-ascii (1/2/4 byte),
 * and legacy forms.
 */
static char *read_pyunicode(pid_t pid, uintptr_t obj_addr)
{
    /* Read PyASCIIObject (which is the prefix of all unicode forms) */
    PyASCIIObject ascii;
    if (read_remote(pid, obj_addr, &ascii, sizeof(ascii)) != (ssize_t)sizeof(ascii))
        return NULL;

    Py_ssize_t length = ascii.length;
    if (length < 0 || length > MAX_STR_LEN)   /* sanity cap */
        return NULL;

    int compact = ascii.state.compact;
    int is_ascii = ascii.state.ascii;
    int kind = ascii.state.kind;

    if (compact && is_ascii) {
        /* data immediately follows PyASCIIObject */
        char *buf = malloc(length + 1);
        if (!buf) return NULL;
        uintptr_t data_addr = obj_addr + sizeof(PyASCIIObject);
        if (read_remote(pid, data_addr, buf, length) != (ssize_t)length) {
            free(buf);
            return NULL;
        }
        buf[length] = '\0';
        return buf;
    }

    if (compact) {
        /* PyCompactUnicodeObject; data follows after it, in `kind`-byte units */
        size_t data_off = sizeof(PyCompactUnicodeObject);
        char *buf = malloc(length + 1);
        if (!buf) return NULL;
        uintptr_t data_addr = obj_addr + data_off;
        if (kind == 1) {
            if (read_remote(pid, data_addr, buf, length) != (ssize_t)length) {
                free(buf); return NULL;
            }
            buf[length] = '\0';
        } else if (kind == 2) {
            Py_UCS2 *w = malloc(length * sizeof(Py_UCS2));
            if (!w) { free(buf); return NULL; }
            if (read_remote(pid, data_addr, w, length * sizeof(Py_UCS2))
                != (ssize_t)(length * sizeof(Py_UCS2))) {
                free(w); free(buf); return NULL;
            }
            for (Py_ssize_t i = 0; i < length; i++) {
                Py_UCS4 ch = w[i];
                if (ch < 0x80) buf[i] = (char)ch;
                else { buf[i] = '?'; }   /* simplified: non-ascii */
            }
            buf[length] = '\0';
            free(w);
        } else if (kind == 4) {
            Py_UCS4 *w = malloc(length * sizeof(Py_UCS4));
            if (!w) { free(buf); return NULL; }
            if (read_remote(pid, data_addr, w, length * sizeof(Py_UCS4))
                != (ssize_t)(length * sizeof(Py_UCS4))) {
                free(w); free(buf); return NULL;
            }
            for (Py_ssize_t i = 0; i < length; i++) {
                Py_UCS4 ch = w[i];
                if (ch < 0x80) buf[i] = (char)ch;
                else buf[i] = '?';
            }
            buf[length] = '\0';
            free(w);
        } else {
            free(buf);
            return NULL;
        }
        return buf;
    }

    /* Legacy (non-compact): data pointer is in PyUnicodeObject.data.any */
    PyUnicodeObject full;
    if (read_remote(pid, obj_addr, &full, sizeof(full)) != (ssize_t)sizeof(full))
        return NULL;
    uintptr_t data_addr = (uintptr_t)full.data.any;
    if (data_addr == 0)
        return NULL;

    char *buf = malloc(length + 1);
    if (!buf) return NULL;
    if (kind == 1) {
        if (read_remote(pid, data_addr, buf, length) != (ssize_t)length) {
            free(buf); return NULL;
        }
    } else if (kind == 2) {
        Py_UCS2 *w = malloc(length * sizeof(Py_UCS2));
        if (!w) { free(buf); return NULL; }
        if (read_remote(pid, data_addr, w, length * sizeof(Py_UCS2))
            != (ssize_t)(length * sizeof(Py_UCS2))) { free(w); free(buf); return NULL; }
        for (Py_ssize_t i = 0; i < length; i++)
            buf[i] = (w[i] < 0x80) ? (char)w[i] : '?';
        free(w);
    } else if (kind == 4) {
        Py_UCS4 *w = malloc(length * sizeof(Py_UCS4));
        if (!w) { free(buf); return NULL; }
        if (read_remote(pid, data_addr, w, length * sizeof(Py_UCS4))
            != (ssize_t)(length * sizeof(Py_UCS4))) { free(w); free(buf); return NULL; }
        for (Py_ssize_t i = 0; i < length; i++)
            buf[i] = (w[i] < 0x80) ? (char)w[i] : '?';
        free(w);
    } else {
        free(buf);
        return NULL;
    }
    buf[length] = '\0';
    return buf;
}

/*
 * Read a remote PyBytesObject and return a heap buffer + length.
 * PyBytesObject layout: PyObject_VAR_HEAD + ob_shash + char ob_sval[1]
 */
static char *read_pybytes(pid_t pid, uintptr_t obj_addr, Py_ssize_t *out_len)
{
    /* We need ob_size which is PyVarObject.ob_size (part of PyObject_VAR_HEAD) */
    PyVarObject var;
    if (read_remote(pid, obj_addr, &var, sizeof(var)) != (ssize_t)sizeof(var))
        return NULL;
    Py_ssize_t size = var.ob_size;
    if (size < 0 || size > MAX_STR_LEN)
        return NULL;

    /* ob_sval is after PyObject_VAR_HEAD + ob_shash (Py_hash_t) */
    size_t sval_off = offsetof(PyBytesObject, ob_sval);
    char *buf = malloc(size + 1);
    if (!buf) return NULL;
    if (read_remote(pid, obj_addr + sval_off, buf, size) != (ssize_t)size) {
        free(buf);
        return NULL;
    }
    buf[size] = '\0';
    *out_len = size;
    return buf;
}

/* ------------------------------------------------------------------ */
/* Line-table resolution  (PEP 626)                                    */
/* ------------------------------------------------------------------ */

/*
 * Given a remote PyCodeObject at `code_addr`, and a bytecode index `lasti`
 * (in code units), resolve the source line number.
 *
 * Mirrors _PyCode_InitAddressRange + _PyCode_CheckLineNumber + advance().
 */
static int addr2line(pid_t pid, uintptr_t code_addr, int lasti, int firstlineno)
{
    /* Read co_linetable pointer */
    uintptr_t linetable_addr;
    if (r_ptr(pid, code_addr + offsetof(PyCodeObject, co_linetable),
                 &linetable_addr) != 0)
        return firstlineno;

    Py_ssize_t lt_len = 0;
    char *linetable = read_pybytes(pid, linetable_addr, &lt_len);
    if (!linetable)
        return firstlineno;

    /* Walk the linetable like CPython's advance() */
    int ar_start = -1;
    int ar_end = 0;
    int computed_line = firstlineno;
    int ar_line = -1;

    const uint8_t *lo_next = (const uint8_t *)linetable;
    const uint8_t *limit = lo_next + lt_len;

    while (lo_next < limit && ar_end <= lasti) {
        /* advance() */
        uint8_t first_byte = *lo_next;
        int code = (first_byte >> 3) & 15;

        /* get_line_delta */
        int ldelta;
        switch (code) {
            case 15: /* PY_CODE_LOCATION_INFO_NONE */
                ldelta = 0;
                break;
            case 13: /* PY_CODE_LOCATION_INFO_NO_COLUMNS */
            case 14: /* PY_CODE_LOCATION_INFO_LONG */
            {
                /* scan_signed_varint at lo_next+1 */
                const uint8_t *p = lo_next + 1;
                if (p >= limit) { ldelta = 0; break; }
                unsigned int uval = *p & 63;
                unsigned int shift = 0;
                while (p < limit && (*p & 64)) { p++; shift += 6; uval |= (*p & 63) << shift; }
                ldelta = (uval & 1) ? -(int)(uval >> 1) : (int)(uval >> 1);
                break;
            }
            case 10: ldelta = 0; break;  /* ONE_LINE0 */
            case 11: ldelta = 1; break;  /* ONE_LINE1 */
            case 12: ldelta = 2; break;  /* ONE_LINE2 */
            default: ldelta = 0; break;  /* short form, same line */
        }

        computed_line += ldelta;

        /* is_no_line_marker: (first_byte >> 3) == 0x1f i.e. code == 15 */
        if (code == 15)
            ar_line = -1;
        else
            ar_line = computed_line;

        ar_start = ar_end;
        ar_end += ((first_byte & 7) + 1) * 2;  /* *2 = sizeof(_Py_CODEUNIT) */

        /* skip to next entry-start byte (high bit set) */
        lo_next++;
        while (lo_next < limit && (*lo_next & 128) == 0)
            lo_next++;
    }

    free(linetable);
    return (ar_line != -1) ? ar_line : firstlineno;
}

/* ------------------------------------------------------------------ */
/* Main: walk runtime -> interp -> threads -> frames                   */
/* ------------------------------------------------------------------ */

static void dump_frames(pid_t pid, uintptr_t frame_addr, uintptr_t trampoline_addr)
{
    int frame_idx = 0;
    for (int i = 0; frame_addr != 0 && i < MAX_FRAMES; i++) {
        /* Batch-read _PyInterpreterFrame fields (f_code, previous, prev_instr)
           in one syscall instead of three separate r_ptr calls. */
        _PyInterpreterFrame frame;
        size_t frame_sz = offsetof(_PyInterpreterFrame, prev_instr) + sizeof(void *);
        if (read_remote(pid, frame_addr, &frame, frame_sz) != (ssize_t)frame_sz)
            break;

        uintptr_t f_code    = (uintptr_t)frame.f_code;
        uintptr_t previous  = (uintptr_t)frame.previous;
        uintptr_t prev_instr = (uintptr_t)frame.prev_instr;

        if (f_code == 0)
            goto next;

        /* Skip interpreter trampoline frames (CPython 3.12+ internal shims).
           These are the same frames py-spy filters out. */
        if (trampoline_addr != 0 && f_code == trampoline_addr)
            goto next;

        /* Batch-read PyCodeObject fields (co_firstlineno, co_qualname,
           co_filename, co_name) in one syscall instead of four. */
        size_t co_lo = offsetof(PyCodeObject, co_firstlineno);
        size_t co_hi = offsetof(PyCodeObject, co_name) + sizeof(void *);
        if (co_lo > co_hi) { size_t t = co_lo; co_lo = co_hi; co_hi = t; }
        size_t co_span = co_hi - co_lo;
        char co_buf[256];
        if (co_span > sizeof(co_buf)) co_span = sizeof(co_buf);
        if (read_remote(pid, f_code + co_lo, co_buf, co_span) != (ssize_t)co_span)
            break;

        int firstlineno;
        uintptr_t co_qualname_addr, co_filename_addr, co_name_addr;
        memcpy(&firstlineno,       co_buf + (offsetof(PyCodeObject, co_firstlineno) - co_lo), sizeof(int));
        memcpy(&co_qualname_addr,  co_buf + (offsetof(PyCodeObject, co_qualname)   - co_lo), sizeof(void *));
        memcpy(&co_filename_addr,  co_buf + (offsetof(PyCodeObject, co_filename)   - co_lo), sizeof(void *));
        memcpy(&co_name_addr,      co_buf + (offsetof(PyCodeObject, co_name)       - co_lo), sizeof(void *));

        char *qualname = co_qualname_addr ? read_pyunicode(pid, co_qualname_addr) : NULL;
        char *filename = co_filename_addr ? read_pyunicode(pid, co_filename_addr) : NULL;
        char *name     = co_name_addr     ? read_pyunicode(pid, co_name_addr)     : NULL;

        /* Compute lasti = prev_instr - co_code_adaptive (in bytes).
           prev_instr and code_base are raw addresses, so their difference
           is already in bytes. The line table ranges (ar_end) are also in
           bytes (each entry's length is multiplied by sizeof(_Py_CODEUNIT)). */
        uintptr_t code_base = f_code + offsetof(PyCodeObject, co_code_adaptive);
        int lasti = (int)(prev_instr - code_base);
        if (lasti < 0) lasti = 0;

        int line = addr2line(pid, f_code, lasti, firstlineno);

        printf("  #%d %s (%s:%d)  [qualname=%s]\n",
               frame_idx,
               name ? name : "?",
               filename ? filename : "?",
               line,
               qualname ? qualname : "?");

        free(qualname);
        free(filename);
        free(name);
        frame_idx++;

    next:
        frame_addr = previous;
    }
}

/* ------------------------------------------------------------------ */
/* Thread info collection + sorted output                              */
/* ------------------------------------------------------------------ */

typedef struct {
    uintptr_t tstate_addr;
    unsigned long thread_id;      /* pthread_t (PyThreadState.thread_id) */
    unsigned long native_tid;     /* OS TID  (PyThreadState.native_thread_id) */
    char name[MAX_NAME_LEN];
} ThreadInfo;

static int compare_by_tid(const void *a, const void *b)
{
    const ThreadInfo *ta = a, *tb = b;
    if (ta->native_tid < tb->native_tid) return -1;
    if (ta->native_tid > tb->native_tid) return 1;
    return 0;
}

static void dump_thread(pid_t pid, uintptr_t tstate_addr,
                        unsigned long native_tid, unsigned long thread_id,
                        const char *name, uintptr_t trampoline_addr)
{
    /* cframe pointer */
    uintptr_t cframe_addr;
    if (r_ptr(pid, tstate_addr + offsetof(PyThreadState, cframe),
                 &cframe_addr) != 0)
        return;

    uintptr_t current_frame = 0;
    if (cframe_addr)
        r_ptr(pid, cframe_addr + offsetof(_PyCFrame, current_frame),
                 &current_frame);

    if (name[0]) {
        printf("Thread %lu: \"%s\"\n", native_tid, name);
    } else {
        printf("Thread %lu\n", native_tid);
    }
    if (current_frame == 0) {
        printf("  (no Python frame — thread may be in C code or idle)\n");
    } else {
        dump_frames(pid, current_frame, trampoline_addr);
    }
    printf("\n");
}

/* ------------------------------------------------------------------ */
/* Native mode: gdb-style "thread apply all bt" via elfutils libdwfl  */
/* ------------------------------------------------------------------ */

#if defined(__x86_64__) || defined(__aarch64__)

#include <elfutils/libdwfl.h>

#define MAX_NATIVE_THREADS 1024
#define MAX_FRAMES_PER_THREAD 256
#define MAX_LINE_LEN 512

typedef struct {
    pid_t tid;
    char comm[16];
    int nframes;
    char frames[MAX_FRAMES_PER_THREAD][MAX_LINE_LEN];
    int unwind_error;
} NativeThreadResult;

typedef struct {
    NativeThreadResult *result;
} FrameCtx;

static int native_frame_cb(Dwfl_Frame *state, void *arg)
{
    FrameCtx *ctx = arg;
    NativeThreadResult *r = ctx->result;

    Dwarf_Addr pc;
    bool isactivation;
    if (!dwfl_frame_pc(state, &pc, &isactivation))
        return DWARF_CB_ABORT;

    Dwarf_Addr lookup_pc = isactivation ? pc : pc - 1;

    Dwfl *dwfl = dwfl_thread_dwfl(dwfl_frame_thread(state));
    Dwfl_Module *mod = dwfl_addrmodule(dwfl, lookup_pc);
    const char *sym = mod ? dwfl_module_addrname(mod, lookup_pc) : NULL;
    const char *modpath = NULL;
    if (mod)
        dwfl_module_info(mod, NULL, NULL, NULL, NULL, NULL, &modpath, NULL);

    if (r->nframes < MAX_FRAMES_PER_THREAD) {
        if (modpath)
            snprintf(r->frames[r->nframes], MAX_LINE_LEN,
                     "#%d  0x%016lx in %s () from %s",
                     r->nframes, (unsigned long)pc,
                     sym ? sym : "??", modpath);
        else
            snprintf(r->frames[r->nframes], MAX_LINE_LEN,
                     "#%d  0x%016lx in %s ()",
                     r->nframes, (unsigned long)pc,
                     sym ? sym : "??");
        r->nframes++;
    }
    return DWARF_CB_OK;
}

typedef struct {
    NativeThreadResult *results;
    int nresults;
    int count;
} ThreadCtx;

static int native_thread_cb(Dwfl_Thread *thread, void *arg)
{
    ThreadCtx *tctx = arg;
    pid_t tid = dwfl_thread_tid(thread);

    if (tctx->count >= tctx->nresults)
        return DWARF_CB_ABORT;

    NativeThreadResult *r = &tctx->results[tctx->count];
    r->tid = tid;
    r->nframes = 0;
    r->unwind_error = 0;

    FrameCtx fctx = { .result = r };
    int fr = dwfl_thread_getframes(thread, native_frame_cb, &fctx);
    if (fr != 0 && r->nframes == 0)
        r->unwind_error = 1;

    tctx->count++;
    return DWARF_CB_OK;
}

static int collect_tids(pid_t pid, pid_t *tids, int max)
{
    char task_dir[64];
    snprintf(task_dir, sizeof(task_dir), "/proc/%d/task", pid);
    DIR *d = opendir(task_dir);
    if (!d) return -1;
    int n = 0;
    struct dirent *de;
    while ((de = readdir(d)) && n < max) {
        if (de->d_name[0] == '.') continue;
        tids[n++] = atoi(de->d_name);
    }
    closedir(d);
    return n;
}

static int compare_pid(const void *a, const void *b)
{
    pid_t pa = *(const pid_t *)a, pb = *(const pid_t *)b;
    return (pa > pb) - (pa < pb);
}

static void read_comm(pid_t pid, pid_t tid, char *buf, size_t cap)
{
    buf[0] = '\0';
    char path[80];
    snprintf(path, sizeof(path), "/proc/%d/task/%d/comm", pid, tid);
    int fd = open(path, O_RDONLY);
    if (fd < 0) return;
    ssize_t n = read(fd, buf, cap - 1);
    close(fd);
    if (n <= 0) { buf[0] = '\0'; return; }
    while (n > 0 && buf[n - 1] == '\n') n--;
    buf[n] = '\0';
}

static int compare_result_tid_desc(const void *a, const void *b)
{
    const NativeThreadResult *ra = a, *rb = b;
    return (ra->tid < rb->tid) - (ra->tid > rb->tid);
}

static int dump_native(pid_t pid)
{
    pid_t tids[MAX_NATIVE_THREADS];
    int ntids = collect_tids(pid, tids, MAX_NATIVE_THREADS);
    if (ntids < 0) {
        fprintf(stderr, "[!] cannot read /proc/%d/task: %s\n", pid, strerror(errno));
        return 1;
    }
    qsort(tids, ntids, sizeof(pid_t), compare_pid);

    int nalloc = ntids < MAX_NATIVE_THREADS ? ntids : MAX_NATIVE_THREADS;
    NativeThreadResult *results = calloc(nalloc, sizeof(NativeThreadResult));
    if (!results) { fprintf(stderr, "[!] out of memory\n"); return 1; }
    for (int i = 0; i < nalloc; i++) {
        results[i].tid = tids[i];
        read_comm(pid, tids[i], results[i].comm, sizeof(results[i].comm));
    }

    static char *debuginfo_path;
    static const Dwfl_Callbacks callbacks = {
        .find_elf        = dwfl_linux_proc_find_elf,
        .find_debuginfo  = dwfl_standard_find_debuginfo,
        .section_address = dwfl_offline_section_address,
        .debuginfo_path  = &debuginfo_path,
    };
    Dwfl *dwfl = dwfl_begin(&callbacks);
    if (!dwfl) {
        fprintf(stderr, "[!] dwfl_begin: %s\n", dwfl_errmsg(-1));
        free(results);
        return 1;
    }
    if (dwfl_linux_proc_report(dwfl, pid) != 0) {
        fprintf(stderr, "[!] dwfl_linux_proc_report: %s\n", dwfl_errmsg(-1));
        dwfl_end(dwfl); free(results); return 1;
    }
    dwfl_report_end(dwfl, NULL, NULL);

    if (dwfl_linux_proc_attach(dwfl, pid, false) != 0) {
        fprintf(stderr, "[!] dwfl_linux_proc_attach: %s (errno=%d: %s)\n",
                dwfl_errmsg(-1), errno, strerror(errno));
        dwfl_end(dwfl); free(results); return 1;
    }

    ThreadCtx tctx = { .results = results, .nresults = nalloc, .count = 0 };
    dwfl_getthreads(dwfl, native_thread_cb, &tctx);
    int ncollected = tctx.count;

    qsort(results, ncollected, sizeof(NativeThreadResult),
          compare_result_tid_desc);

    char cmdline[4096];
    if (read_cmdline(pid, cmdline, sizeof(cmdline)) == 0)
        printf("Process %d: %s\n", pid, cmdline);
    else
        printf("Process %d\n", pid);
    printf("\n");

    for (int i = 0; i < ncollected; i++) {
        NativeThreadResult *res = &results[i];
        printf("Thread %d (Thread 0x%016lx (LWP %d) \"%s\"):\n",
               i + 1, (unsigned long)0, res->tid, res->comm);
        if (res->nframes == 0 && res->unwind_error)
            printf("Backtrace stopped: Cannot access memory at address 0x0\n");
        else
            for (int j = 0; j < res->nframes; j++)
                printf("%s\n", res->frames[j]);
        printf("\n");
    }

    dwfl_end(dwfl);
    free(results);
    return 0;
}

#endif /* x86-64 || aarch64 */

int main(int argc, char **argv)
{
    /* Parse: pyprobe <pid> [--native] */
    int native_mode = 0;
    if (argc == 3 && strcmp(argv[2], "--native") == 0) {
        native_mode = 1;
    } else if (argc != 2) {
        fprintf(stderr, "usage: %s <pid> [--native]\n", argv[0]);
        return 1;
    }
    pid_t pid = (pid_t)atoi(argv[1]);

    if (native_mode) {
#if defined(__x86_64__) || defined(__aarch64__)
        return dump_native(pid);
#else
        fprintf(stderr, "[!] --native is only supported on x86-64 and aarch64\n");
        return 1;
#endif
    }

    /* 1. Find the python executable path (readlink resolves venv symlinks) */
    char exe_path[PATH_MAX];
    char proc_link[64];
    snprintf(proc_link, sizeof(proc_link), "/proc/%d/exe", pid);
    ssize_t n = readlink(proc_link, exe_path, sizeof(exe_path) - 1);
    if (n < 0) {
        perror("readlink /proc/pid/exe");
        return 1;
    }
    exe_path[n] = '\0';

    /* 2. Find _PyRuntime symbol address (handles PIE load base) */
    uintptr_t runtime_addr = find_symbol_in_elf(exe_path, "_PyRuntime", pid);
    if (runtime_addr == 0) {
        fprintf(stderr, "[!] cannot find _PyRuntime symbol\n");
        return 1;
    }

    /* 3. Resolve Python version from ELF file (Py_Version is a compile-time const) */
    char version_str[32] = "?";
    unsigned long py_version;
    if (read_const_from_elf(exe_path, "Py_Version", &py_version, sizeof(py_version)) == 0)
        decode_py_version(py_version, version_str, sizeof(version_str));

    /* 4. Print py-spy-style header */
    char cmdline[4096];
    if (read_cmdline(pid, cmdline, sizeof(cmdline)) == 0)
        printf("Process %d: %s\n", pid, cmdline);
    else
        printf("Process %d: %s\n", pid, exe_path);
    printf("Python v%s (%s)\n\n", version_str, exe_path);

    /* 5. interpreters.main  (offset within _PyRuntimeState) */
    uintptr_t interp_addr;
    if (r_ptr(pid, runtime_addr + offsetof(_PyRuntimeState, interpreters)
                          + offsetof(struct pyinterpreters, main),
               &interp_addr) != 0) {
        fprintf(stderr, "[!] failed to read interpreters.main\n");
        return 1;
    }

    /* Fallback: if main is NULL, read interpreters.head instead. */
    if (interp_addr == 0) {
        r_ptr(pid, runtime_addr + offsetof(_PyRuntimeState, interpreters)
                      + offsetof(struct pyinterpreters, head),
              &interp_addr);
    }
    if (interp_addr == 0) {
        fprintf(stderr, "[!] no interpreter state\n");
        return 1;
    }

    /* 6. Read interp->interpreter_trampoline (CPython 3.12+).
           Frames whose f_code == this are internal shim frames to skip. */
    uintptr_t trampoline_addr = 0;
    r_ptr(pid, interp_addr + offsetof(PyInterpreterState, interpreter_trampoline),
             &trampoline_addr);

    /* 7. interp->threads.head  (PyThreadState linked list) */
    uintptr_t tstate_addr;
    if (r_ptr(pid, interp_addr + offsetof(PyInterpreterState, threads)
                          + offsetof(struct pythreads, head),
              &tstate_addr) != 0) {
        fprintf(stderr, "[!] failed to read threads.head\n");
        return 1;
    }

    /* 8. Collect all threads — batch-read next, thread_id, native_thread_id
           in one syscall instead of three per thread. */
    ThreadInfo threads[MAX_THREADS];
    int nthreads = 0;

    size_t ts_lo = offsetof(PyThreadState, next);
    size_t ts_hi = offsetof(PyThreadState, native_thread_id) + sizeof(unsigned long);
    if (ts_lo > ts_hi) { size_t t = ts_lo; ts_lo = ts_hi; ts_hi = t; }
    size_t ts_span = ts_hi - ts_lo;

    while (tstate_addr != 0 && nthreads < MAX_THREADS) {
        char ts_buf[1024];
        size_t rd = ts_span > sizeof(ts_buf) ? sizeof(ts_buf) : ts_span;
        if (read_remote(pid, tstate_addr + ts_lo, ts_buf, rd) != (ssize_t)rd)
            break;

        unsigned long thread_id, native_tid;
        uintptr_t next_addr;
        memcpy(&next_addr,   ts_buf + (offsetof(PyThreadState, next)             - ts_lo), sizeof(void *));
        memcpy(&thread_id,   ts_buf + (offsetof(PyThreadState, thread_id)        - ts_lo), sizeof(unsigned long));
        memcpy(&native_tid,  ts_buf + (offsetof(PyThreadState, native_thread_id) - ts_lo), sizeof(unsigned long));

        threads[nthreads].tstate_addr = tstate_addr;
        threads[nthreads].thread_id = thread_id;
        threads[nthreads].native_tid = native_tid;
        threads[nthreads].name[0] = '\0';
        nthreads++;

        tstate_addr = next_addr;
    }

    /* 9. Look up thread names from threading._active */
    ThreadName names[MAX_THREADS];
    int nnames = get_thread_names(pid, interp_addr, names, MAX_THREADS);

    /* Match names to threads via binary search (O(n log n) vs O(n²)) */
    qsort(names, nnames, sizeof(ThreadName), compare_threadname_by_tid);
    for (int i = 0; i < nthreads; i++) {
        ThreadName key = { .thread_id = threads[i].thread_id };
        ThreadName *match = bsearch(&key, names, nnames, sizeof(ThreadName),
                                    compare_threadname_by_tid);
        if (match) {
            size_t name_len = strlen(match->name);
            if (name_len >= MAX_NAME_LEN) name_len = MAX_NAME_LEN - 1;
            memcpy(threads[i].name, match->name, name_len);
            threads[i].name[name_len] = '\0';
        }
    }

    /* 10. Sort by native_tid (OS TID) */
    qsort(threads, nthreads, sizeof(ThreadInfo), compare_by_tid);

    /* 11. Dump threads */
    for (int i = 0; i < nthreads; i++) {
        dump_thread(pid, threads[i].tstate_addr,
                    threads[i].native_tid, threads[i].thread_id,
                    threads[i].name, trampoline_addr);
    }

    return 0;
}
