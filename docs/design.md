# pyprobe 架构设计

> 本文是 pyprobe 架构与设计的**单一事实来源**（single source of truth）。
> AGENTS.md（构建/运行/测试操作指令）与 README.md（快速上手）仅引用本文，不重复架构内容，避免散弹式修改。

---

## 1. 概述

pyprobe 是 CPython 进程外检查工具，无需 ptrace attach 即可获取运行中 Python 进程的调用栈。提供两种工作模式：

- **Python 栈转储**（默认）— 遍历 `_PyRuntime → interpreter → threads → frames`，输出 py-spy 风格的 Python 调用栈（函数名、文件、行号、qualname）。使用 `process_vm_readv(2)` 读取目标进程内存，**不需要 ptrace attach**。
- **Native 栈转储**（`--native`）— 通过 elfutils libdwfl 对所有线程进行 DWARF 回溯展开，输出 gdb `thread apply all bt` 风格的原生调用栈。使用 `ptrace(2)` attach 所有线程。

主交付物为**纯 Python 实现**（`pyprobe/` 包，仅依赖标准库 ctypes/struct），另附 C 参考实现用于开发期交叉校验（不对外交付）。

---

## 2. 模块结构

```
pyprobe/                  纯 Python 实现（入口 pyprobe.cli:main）
├── __init__.py           公共 API 导出（__all__）
├── __main__.py           python -m pyprobe 入口
├── cli.py                argparse CLI（stack 子命令）
├── memory.py             process_vm_readv 远程内存读取 (ctypes) + 页级 LRU 缓存
├── elf.py                ELF64 符号查找 (struct, 零依赖) + PIE load base
├── pyobject.py           PyLong / PyUnicode / PyBytes 远程读取
├── dict_iter.py          CPython 3.11–3.13 Dict 迭代器（combined/unicode/split/managed）
├── linetable.py          PEP 626 行号表解析（addr2line）
├── thread_names.py       threading._active 线程名查找
├── stack_dump.py         Python 栈转储主逻辑（collect/format/dump 三层）
├── native_dump.py        Native 栈转储 (ctypes + libdwfl，惰性加载)
├── types.py              结构化数据类型（FrameInfo/ThreadInfo/ProcessInfo/...）
├── errors.py             异常层级（PyProbeError 基类 + 子类）
├── colors.py             ANSI 颜色帮助 + clicolors 检测（零依赖）
├── offsets.py            CPython 结构体偏移量（多版本验证表，单一数据源）
└── offsets.json          开发期偏移量覆盖（生成于 tools/gen_offsets.c）

tools/                    构建期工具
└── gen_offsets.c         生成 pyprobe/offsets.json

reference/c/              C 参考实现（py_stack_dump.c, Makefile；非交付，仅供交叉校验）
examples/                 示例目标进程（fastapi_app.py）
scripts/                  辅助脚本（build.sh, build_wheels.sh, gen_offsets.sh, run_tests.sh）

tests/                    测试
├── conftest.py           target_pid session fixture（派生子进程作为探测目标）
├── helpers.py            FakeReader + CPython 对象内存构造器
├── targets/target_app.py 集成测试目标进程（主线程 + bg-worker 线程）
└── test_*.py             单元测试 + test_integration.py（端到端）
```

---

## 3. 分层 API 设计

库 API 与 CLI 显式分离为三层（对应 `stack_dump.py` / `native_dump.py` 的 `collect_*` / `format_*` / `dump_*`）：

| 层 | 函数 | 职责 | 返回/行为 |
|----|------|------|-----------|
| 采集 | `collect_python(pid)` | 纯数据采集 | `(ProcessInfo, list[ThreadInfo])`，抛 `PyProbeError` 子类 |
| 采集 | `collect_native(pid)` | ptrace attach + DWARF unwind | `list[NativeThreadInfo]`，抛 `AttachFailed` |
| 格式化 | `format_process(proc_info, threads, *, color=False)` | 结构化数据 → CLI 风格字符串 | `str`（`color=True` 时含 ANSI 码） |
| 格式化 | `format_native(cmdline, threads, *, color=False)` | 同上（native） | `str` |
| CLI 封装 | `dump_python(pid, color=None)` | collect + format + print | 退出码 `int`，异常转 stderr |
| CLI 封装 | `dump_native(pid, color=None)` | 同上（native） | 退出码 `int` |

设计要点：
- `collect_*` **不打印**，返回结构化 dataclass，调用方可程序化使用（序列化、后处理）。
- `format_*` 是纯函数，输入数据输出字符串。
- `dump_*` 是薄封装：捕获异常 → stderr 提示 + 退出码。
- `collect_frames` / `collect_thread` 额外导出，便于单元测试注入 mock reader。

`__init__.py` 通过 `__all__` 导出完整公共 API（数据类型、异常、采集/格式化/CLI 函数、`offsets` 子模块）。

### 3.1 结构化数据类型（types.py）

```python
FrameInfo(name, filename, line)              # 单个 Python 栈帧
ThreadInfo(native_tid, thread_id, name, frames, idle)  # 线程 + 帧列表
ProcessInfo(pid, cmdline, exe_path, python_version)    # 进程元数据
NativeFrame(pc, symbol, module)              # 单个 native 栈帧
NativeThreadInfo(tid, comm, frames, unwind_failed)    # native 线程 + 帧列表
```

每个 dataclass 自带 `format()` / `format_header()` 方法，`format_*` 函数组合调用它们生成输出。

### 3.2 异常层级（errors.py）

```
PyProbeError                      基类（库调用方可统一 catch）
├── ProcessNotFound               /proc/<pid> 不可读（进程不存在）
├── PermissionDenied              权限不足
├── SymbolNotFound                ELF 符号未找到（如 _PyRuntime）
├── NoInterpreterState            无法读取 interpreter state
├── NoThreadState                 无法读取 thread state 链
├── VersionNotSupported           CPython 版本未验证（带 fallback 版本信息）
└── AttachFailed                  ptrace attach 失败（native 模式）
```

`collect_*` 抛出具体异常；`dump_*` 捕获后转 stderr + 退出码。库调用方可 catch `PyProbeError` 统一处理或 catch 子类区分失败模式。

### 3.3 颜色基础设施（colors.py）

CLI 输出对齐 py-spy 的颜色语义，零依赖自实现（不引 rich/colorama）：

| 输出元素 | 颜色 |
|----------|------|
| pid / tid / LWP tid | bold + yellow（`\x1b[1m\x1b[33m`） |
| 函数名 / native symbol | green |
| 文件名 / native module | cyan |
| 行号 / pc / (idle) 标记 | dim |
| `[!]` 错误行 / Backtrace stopped | red |
| cmdline / 版本行 / 线程名 / 帧号 | 不着色 |

检测逻辑 `should_color(stream)` 遵循 [clicolors spec](https://bixense.com/clicolors/)（与 py-spy 依赖的 console crate 一致），优先级短路：`CLICOLOR_FORCE != "0"` 无条件强制（胜过 NO_COLOR）→ `NO_COLOR` 非空禁用 → 非 tty 禁用 → `TERM=dumb` 禁用 → `CLICOLOR == "0"` 禁用 → 否则启用。stdout/stderr 独立检测。

无回归不变式（由测试守护）：
- **I1**：`colors.py` 所有帮助函数在 `color=False` 时恒等返回原文。
- **I2**：所有 format 方法缺省调用输出与引入颜色前逐字节一致。
- **I3**：`dump_*` 缺省（`color=None`）在非 tty（管道/重定向/capsys）下等价于 `color=False`。

决策记录：**不提供全局开关**（如 `set_colors_enabled`）。颜色经显式 `color` 参数传递——format 层 keyword-only 缺省 `False`（保持纯函数，库用户默认拿到纯文本），dump 层 `Optional[bool] = None`（None = 按流自动检测），CLI `--color {auto,always,never}` 映射为 `None/True/False`。理由：全局可变状态会破坏 `format_*` 纯函数契约、需要 conftest 重置 fixture，且嵌入宿主程序时存在"宿主 tty 泄漏 ANSI 码进日志"的风险。

---

## 4. 内存读取架构（memory.py）

### 4.1 远程读取

`RemoteReader` 封装 `process_vm_readv(2)`（ctypes 绑定 `libc.so.6`）。读取循环处理 `EINTR`（errno 4 自动重试）和短读（partial read）。返回 `None` 表示不可读。

辅助方法：`read_ptr` / `read_u32` / `read_u64` / `read_int` — 基于 `read()` 的薄封装。

### 4.2 页级 LRU 缓存

栈遍历具有高空间局部性（帧链、相邻 CodeObject 字段、连续 dict 条目、per-key unicode body）。缓存整页可将多次 4-8 字节小读合并为每页一次 syscall。

| 参数 | 值 | 说明 |
|------|----|------|
| `PAGE_SIZE` | 4096 | 页大小 |
| `CACHE_MAX_PAGES` | 256（1 MB） | LRU 最大页数 |
| `BYPASS_CACHE_THRESHOLD` | 4096 | ≥1 页的读取绕过缓存，避免驱逐热数据 |

缓存语义：**快照** — 在 `RemoteReader` 实例生命周期内有效，适用于单次 dump 调用。远程进程可能并发修改内存；缓存读取反映足够一致的快照（与 gdb / py-spy attach 模式相同）。

部分页读取（罕见，映射末尾）不缓存，直接透传，调用方通过长度检查感知短读。

---

## 5. ELF 符号查找（elf.py）

手动解析 ELF64（`struct` 模块，零依赖），支持 `.symtab` 和 `.dynsym` 两种符号表。`find_symbol(exe_path, symname, pid)` 查找符号地址；`read_const(exe_path, symname, length)` 读取符号对应的常量数据（如 `Py_Version`）。

关键设计：
- **ELF 解析缓存**：`_get_elf_info()` 按 `(path, mtime)` 缓存 section header table，避免重复解析。`find_symbol` 和 `read_const` 共享缓存。
- **PIE load base**：对 `e_type == ET_DYN`（PIE 可执行文件），从 `/proc/<pid>/maps` 读取加载基址，加到符号 value 上。
- `read_cmdline(pid)` 读取 `/proc/<pid>/cmdline`；`decode_py_version(hexval)` 解码 CPython 版本号（`major.minor.micro`）。

---

## 6. CPython 偏移量架构（offsets.py）

### 6.1 单一数据源

`_VERIFIED_OFFSETS` 字典是偏移量的**单一数据源**，按 CPython `major.minor` 版本键控。对于 64 位 LP64 架构（x86-64、aarch64），同一 CPython 版本的偏移量相同，无需架构键。

### 6.2 运行时配置

`configure(version_str)` 在 `collect_python` 中被调用（传入目标进程的 CPython 版本）：
- 已验证版本 → 加载对应偏移量表。
- 未验证版本 → stderr 告警 + 回退到 `_DEFAULT_VERSION`（"3.12"）偏移量（输出可能不正确）。
- 开发期覆盖：若 `offsets.json` 存在，仅当其 `_version` 键与目标进程 major.minor 一同时才覆盖验证表（用于在提升到 `_VERIFIED_OFFSETS` 前测试新生成的偏移量，避免跨版本污染）。

`get(name)` 惰性初始化（未 configure 时用默认版本），返回偏移量值。`get_or(name, default)` 对该版本不存在的键返回 `default`——字段在版本间缺失（如 3.11 无 `InterpreterState.imports`、3.13 无 `ThreadState.cframe`）时消费者据此分支。

### 6.3 生成工具

`tools/gen_offsets.c`（通过 `scripts/gen_offsets.sh`）编译并运行一个小程序，读取 CPython 头文件中的结构体偏移量，生成 `pyprobe/offsets.json`。新版本验证流程：运行 gen_offsets → 测试 → 确认后编入 `_VERIFIED_OFFSETS`。

---

## 7. CPython 数据结构遍历

### 7.1 Python 栈转储（stack_dump.py）

遍历链路：
```
find_symbol(_PyRuntime)
  → RuntimeState.interpreters + pyinterpreters.main  [fallback: pyinterpreters.head]
    → InterpreterState.threads + pythreads.head      (PyThreadState 链表)
      → ThreadState.cframe → CFrame.current_frame     (_PyInterpreterFrame 链 via ->previous)
        （3.13 省略 cframe 间接层，直接 ThreadState.current_frame）
        → InterpreterFrame.f_code                     (PyCodeObject)
          → CodeObject.co_name / co_filename / co_firstlineno / co_linetable / co_code_adaptive
```

关键逻辑：
- **InterpreterFrame 遍历**（`collect_frames`）：沿 `previous` 链走，最多 `MAX_FRAMES=100` 帧。跳过 `f_code == 0` 的帧和 `interpreter_trampoline` 帧（仅 3.12 存在该键）；`co_name` 与 `co_filename` 均为 NULL 的尾部陈旧帧（3.13 datastack 残留）终止遍历。
- **行号计算**：`lasti = prev_instr - (f_code + co_code_adaptive)`，传入 `addr2line` 解析行号表。
- **线程链遍历**（`_read_thread_chain`）：沿 `ThreadState.next` 链走，最多 `MAX_THREADS=256`。批量读取每条 tstate 的 `next`/`thread_id`/`native_tid` 字段（单次读取覆盖最小跨度区间）。
- **线程名查找**（`thread_names.py`）：定位 `sys.modules`（3.12 经 `InterpreterState.imports → modules` 直达；3.11/3.13 遍历 `sysdict` 字典找 `"modules"` 键），找到 `threading` 模块，读其 `_active` 字典，匹配 `thread_id → _name`。
- **空闲线程检测**（`_is_thread_idle`）：两种启发式——读 `/proc/<pid>/task/<tid>/stat` 状态非 `R`，或顶层帧匹配已知空闲惯用语（`threading.wait`、`selectors.select` 等）。
- `reader` 参数为鸭子类型（任何提供 `read`/`read_ptr` 的对象），便于单元测试注入 `FakeReader`。

### 7.2 字典迭代器（dict_iter.py）

`DictIter` 支持 CPython 3.11–3.13 字典的所有变体（各版本 `_dictkeysobject`/entries 布局一致）：
- **combined**（`kind=0`）：`PyDictKeyEntry`（key + hash + value），key 偏移为 8。
- **unicode**（`kind=1`）：`PyDictUnicodeEntry`（key + value），key 偏移为 0。
- **split**：values 数组独立存储，从 `DictObject.ma_values` 读取。
- **managed dict**：通过 `Py_TPFLAGS_MANAGED_DICT` 标志检测。实例 pre-header 槽位（`obj - 3*PTR_SIZE`）各版本约定不同：3.11 为独立两槽——dict 指针（未物化为 NULL）+ `obj-4` 的 untagged `PyDictValues*`（`PyObject.pre_values` 键，仅 3.11 表存在）；3.12 合并为单槽 tagged 指针（bit0=1 时为 `ptr-1` 的 values 数组）；3.13 单槽 untagged，NULL 表示 values 内嵌在对象内（`PyObject_size + dictvalues_header` 起始）。

`from_dict(dict_addr)` 从 `DictObject.ma_keys` / `ma_values` 初始化；`from_managed_values(values_addr, type_addr)` 从 `HeapTypeObject.ht_cached_keys` 初始化。

### 7.3 Python 对象读取（pyobject.py）

- **PyLong**（`read_pylong`）：3.12+ 读 `lv_tag`，`size = tag >> 3`；3.11 读 `ob_size`（有符号位数计数，负整数以超大无符号值读出后落到 None，即仅解析非负小整数）。支持 0/1/2 位 digit（每位 30 位），>2 位返回 None。
- **PyBytes**（`read_pybytes`）：读 `ob_size`，从 `ob_sval` 读数据；超 `MAX_STR_LEN` 返回 None。
- **PyUnicode**（`read_pyunicode`）：读 `PyASCIIObject` 头，解析 state byte（compact/is_ascii/kind）；compact+ascii 直接读内联数据；compact 非 ascii 从 `PyCompactUnicodeObject_size` 读；非 compact 从 `data_any` 指针读。支持 latin-1/utf-16-le/utf-32-le 三种编码。

### 7.4 行号表解析（linetable.py）

实现 PEP 626 行号表（`co_linetable`）解析。`addr2line(reader, code_addr, lasti, firstlineno)` 从 `lasti`（字节码偏移）解析对应行号。处理 code 10/11/12/13/14/15 各种 entry 类型及变长整数编码。无法解析时回退到 `firstlineno`。

### 7.5 线程名查找（thread_names.py）

`get_thread_names(reader, interp_addr)` 返回 `{thread_id: name}` 字典。链路：`sys.modules → "threading" 模块 → _active dict → {tid: Thread 实例} → 实例 dict → "_name"`。实例字典读取需处理 managed dict（`Py_TPFLAGS_MANAGED_DICT`，各版本 pre-header 槽位约定见 §7.2）与普通 dict 两种情况（`_get_instance_dict_iter`）。

---

## 8. Native 栈转储架构（native_dump.py）

### 8.1 惰性加载

`libdw` / `libc` 初始化为 `None`，CDLL 加载与原型设置移入 `_init_libs()`，仅在 `dump_native` / `collect_native` 首次调用时触发。**Python 栈模式不依赖 libdw**，`import pyprobe` 无模块级副作用（除 `memory.py` 的 `libc.so.6`，Linux 必有）。

### 8.2 流程

1. `_attach_all_threads(pid)` — 枚举 `/proc/<pid>/task`，对每个 tid `ptrace(PTRACE_ATTACH)` + `waitpid`。
2. `_collect_frames(pid)` — `dwfl_begin` → `dwfl_linux_proc_report` → `dwfl_linux_proc_attach` → `dwfl_getthreads`（遍历线程）→ `dwfl_thread_getframes`（遍历帧）。每帧通过 `dwfl_addrmodule` + `dwfl_module_addrname` 解析符号，`dwfl_module_info` 获取模块路径。
3. `_detach_all(tids)` — `ptrace(PTRACE_DETACH)` 所有线程。

结果按 tid 降序排列（匹配 gdb `thread apply all bt` 输出）。`unwind_failed` 标记无法展开的线程。

### 8.3 ctypes 回调

使用 `CFUNCTYPE` 定义 dwfl 回调签名（`_find_elf_t` / `_find_debuginfo_t` / `_section_address_t` / `_thread_cb_t` / `_frame_cb_t`）。帧/线程回调通过 `ctypes.py_object` 传递 Python list 引用收集结果。

---

## 9. 权限模型

### 9.1 Python 栈模式（默认）

使用 `process_vm_readv(2)`，**不需要 ptrace attach**。

| 条件 | 是否可用 |
|------|----------|
| root（CAP_SYS_PTRACE） | 任意进程 |
| 同用户 + `ptrace_scope=0` | 任意进程 |
| 同用户 + `ptrace_scope=1`（默认） | 仅子进程 |
| 目标进程 `dumpable=0` | **仍可读取** |

### 9.2 Native 模式（`--native`）

使用 `ptrace(2)` attach 所有线程 + libdwfl DWARF 回溯展开。

| 条件 | 是否可用 |
|------|----------|
| root（CAP_SYS_PTRACE） | 任意进程 |
| 同用户 + `ptrace_scope=0` | 任意进程 |
| 同用户 + `ptrace_scope=1`（默认） | 仅子进程 |
| 目标进程 `dumpable=0` | **不可用** |

> uvicorn / FastAPI 运行时会将 `dumpable` 设为 0，导致 native 模式在非 root 下无法 attach。Python 栈模式不受此限制。

```bash
cat /proc/sys/kernel/yama/ptrace_scope   # 检查当前 ptrace_scope
sudo python -m pyprobe stack -p <pid> --native   # root 可绕过所有限制
```

`process_vm_readv` / `ptrace` 要求目标进程是当前进程的后代（`ptrace_scope=1` 默认下）。集成测试通过 `subprocess.Popen` 派生子进程作为探测目标，天然满足此条件。

---

## 10. CPython 版本支持

| CPython 版本 | 架构 | 状态 |
|-------------|------|------|
| 3.11.x | x86-64 | 已验证 |
| 3.11.x | aarch64 | 未验证（偏移量理论上与 x86-64 相同，待实际验证） |
| 3.12.x | x86-64 | 已验证 |
| 3.12.x | aarch64 | 未验证（偏移量理论上与 x86-64 相同，待实际验证） |
| 3.13.x | x86-64 | 已验证 |
| 3.13.x | aarch64 | 未验证（偏移量理论上与 x86-64 相同，待实际验证） |

对未验证版本，pyprobe 会在 stderr 输出告警并使用 3.12 偏移量作为默认回退。可通过 `scripts/gen_offsets.sh` 为目标 CPython 生成偏移量，验证后编入 `pyprobe/offsets.py` 的 `_VERIFIED_OFFSETS`。

### 限制

- 已验证 3.11.x / 3.12.x / 3.13.x（x86-64）；其他版本回退 3.12 偏移量并告警。
- 仅支持 **Linux**（依赖 `/proc`、`process_vm_readv`、`ptrace`）。
- 已验证 **x86-64**；aarch64 偏移量理论上相同（均为 64 位 LP64）但未实际验证。
- Native 模式在非 root 下受 `ptrace_scope` 和 `dumpable` 限制。

---

## 11. C 参考实现（reference/c/）

C 版本为开发辅助参考实现，**不对外交付**，仅用于与 Python 实现交叉校验。

| 特性 | C 参考实现 | Python 实现 |
|------|--------|-------------|
| Python 栈转储 | `build/pyprobe <pid>` | `python -m pyprobe stack -p <pid>` |
| Native 栈转储 | `build/pyprobe <pid> --native` | `python -m pyprobe stack -p <pid> --native` |
| 运行时依赖 | libdw.so、libelf.so、libz | libdw.so.1（仅 native 模式） |
| 编译需求 | 需要 C 编译器 + CPython 头文件 | 仅生成偏移量时需要 |
| 外部 Python 包 | 无 | 无（全部使用标准库 ctypes/struct） |
| 架构支持 | x86-64、aarch64 | x86-64（aarch64 需重新生成偏移量） |
| CPython 版本 | 编译时绑定 | 运行时按版本自动选择偏移量 |

---

## 12. 集成模式

pyprobe 支持三种集成方式：

### 12.1 Wheel 包安装

Wheel 标签为 `py3-none-linux_{x86_64,aarch64}`（纯 Python，支持 Python 3.8+ 宿主，仅 Linux）。

设计保障：
- `offsets.json` 通过 `[tool.setuptools.package-data]` 打入 wheel；即使无 `offsets.json`，`_VERIFIED_OFFSETS` 编入代码也能正常工作。
- `setup.cfg` 使用 `python_tag = py3`，`pyproject.toml` 声明 `requires-python = ">=3.8"`——pyprobe 为纯 Python，宿主版本不受限。
- `offsets.py` 在读取目标 `Py_Version` 后调用 `configure(version_str)` 按目标版本选择已验证偏移量表，未验证版本输出 stderr 告警并回退。
- `native_dump.py` 惰性加载 libdw——非 native 模式导入无副作用。
- `offsets.py` 无模块级 I/O，`configure()` 按需调用。

### 12.2 源码集成 + 命令行工具

`pyprobe stack -p <pid>` 子命令（argparse，支持 `--help`/`--version`）。`gen_offsets.sh` 依赖 C 编译器 + CPython 开发头文件（源码集成必要步骤）。

### 12.3 源码集成 + 接口

通过 `__all__` 导出的公共 API 编程使用：

```python
from pyprobe import collect_python, format_process, PyProbeError

try:
    proc_info, threads = collect_python(pid)
except PyProbeError as exc:
    ...
else:
    print(format_process(proc_info, threads))
```

`collect_*` 返回结构化 dataclass，`format_*` 转字符串，`dump_*` 是 CLI 封装。

---

## 13. 测试架构

### 13.1 单元测试（无子进程依赖）

通过 `FakeReader`（内存字典模拟 `RemoteReader`）和 CPython 对象内存构造器实现**零进程依赖**测试：

- `FakeReader`（`tests/helpers.py`）：`{base_addr: bytes}` 区域模型，`read(addr, length)` 查找完全包含的区域返回切片。
- 对象构造器：`build_pyunicode` / `build_pybytes` / `build_pylong` / `build_code_object` / `build_frame` — 按配置偏移量写入字节。
- `FakeRemoteReader`：子类化真实 `RemoteReader`，stub `_read_syscall` 提供罐头页数据，测试真实页缓存逻辑（LRU 淘汰、跨页、旁路）。

覆盖模块：offsets（版本键/configure/get/fallback）、types（格式化 + `color=True` 精确 ANSI 断言）、errors（异常层级）、colors（帮助函数恒等性/包裹 + `should_color` 环境矩阵）、linetable（PEP 626 全 code 类型）、pyobject（PyLong/PyBytes/PyUnicode 各变体）、dict_iter（combined/unicode/split/managed）、memory（页缓存）、elf（decode_py_version/read_cmdline/find_symbol/read_const 真实 ELF）、stack_dump（collect_frames/`_is_thread_idle`/collect_thread/format_process/错误路径/dump CLI 颜色）、cli（参数解析/分发/`--color` 传递）。

### 13.2 集成测试（`@pytest.mark.integration`）

端到端验证 `collect_python` / `format_process` / `dump_python` / `collect_native`。

- `target_pid` session fixture（`conftest.py`）：`subprocess.Popen` 派生 `tests/targets/target_app.py`（主线程 + bg-worker 线程），通过 stdout 获取真实 PID。子进程是 pytest 后代，`process_vm_readv` 在 `ptrace_scope=1` 默认下可用。目标解释器默认为 `sys.executable`，可经 `TARGET_PYTHON` 环境变量指定其他版本（如 3.11/3.13）做跨版本端到端验证。
- 非 Linux 自动 skip；Native dump 在 ptrace 权限不足时自动 skip。

### 13.3 手动探测约束

手动端到端验证 `pyprobe stack` 时：
- **必须**用 `subprocess.Popen` 派生子进程并通过 stdout 获取真实 PID（复用 `conftest.py:target_pid` 模式）；`try/finally` 中 `terminate()` + `wait(timeout=5)` 确保回收。
- **禁止**用 shell `&` 后台启动 + `$!`/`pgrep` 获取 PID：`uv run` 包装器导致 `$!` 指向 `uv`/`bash` 而非 Python 解释器；`pgrep -f` 会匹配 `/bin/bash -c ...` 命令行。对非 Python 进程探测 `_PyRuntime` 必然失败。
- **禁止**在持久 shell 里裸用 `&` 跑长驻进程：后台子进程继承管道，不退出时管道不关闭，会导致整个 shell 会话卡死。
