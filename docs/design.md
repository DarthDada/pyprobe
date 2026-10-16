# pyprobe 架构设计

> 本文是 pyprobe 架构与设计的**单一事实来源**（single source of truth）。
> 模块级契约与行为点清单（contracts.md §10 重构产出）见 [contracts.md](contracts.md)；
> AGENTS.md（构建/运行/测试操作指令）与 README.md（快速上手）仅引用本文与
> contracts.md，不重复架构内容，避免散弹式修改。
>
> 本文描述的是第一性原理重构（contracts.md §10）后的架构（旧树已由 `next/` 原子替换，
> 2026-10）。原 ADR 制度已退役（2026-10-16）：仍具约束力的决策内容已并入本文对应
> 章节，历史全文见 git log；「有意为之的怪行为」汇总见 §16。

---

## 1. 概述

pyprobe 的本质是一个**外部观测者问题**：仅凭目标 PID 与内核接口，重建运行中的
CPython 运行时状态并呈现。它提供四种工作模式：

- **Python 栈转储**（默认）— 遍历 `_PyRuntime → interpreter → threads → frames`，
  输出 py-spy 风格的 Python 调用栈（函数名、文件、行号）。使用 `process_vm_readv(2)`
  读取目标内存，**不需要 ptrace attach**。
- **Native 栈转储**（`--native`）— 经 elfutils libdwfl 对全部线程做 DWARF 回溯，
  输出 gdb `thread apply all bt` 风格的原生调用栈。使用 ptrace（统一引擎，§8）。
- **Syscall 追踪**（`syscall` 子命令）— ptrace SEIZE + SYSCALL 实时追踪所有线程的
  系统调用，输出 strace 风格事件流 / `strace -c` 风格统计（§14）。
- **采样与性能分析**（`record` / `top` 子命令）— 周期性采样 Python 调用栈：
  `record` 聚合为 folded stacks，`top` 终端实时刷新各线程当前帧与热点函数排行
  （§15）。使用 `process_vm_readv(2)`，**不需要 ptrace attach**。
- **Web 监控**（`web` 子命令，可选 extras `pyprobe[web]`）— 采样观测模式
  的浏览器前端：HTTP 服务器 + SSE 推送热点统计与活跃线程**完整调用栈**。与 `top`
  共享 Sampler/TopStats，同样**不需要 ptrace attach**；web 是与 cli 平级的顶层
  消费形态，非第五种观测模式。

主交付物为**纯 Python 实现**（仅标准库 ctypes/struct），另附 C 参考实现用于开发期
交叉校验（不对外交付）。

### 1.1 七项不可约能力与分层

pyprobe 的能力被分解为七项不可约能力，各归唯一的包；包间依赖方向**唯一合法**：

```
kernel → target → cpython → observe → present → {cli, web}
```

| 能力 | 归属包 |
|------|--------|
| 字节传输（process_vm_readv / procfs 读内存） | `kernel/` |
| 动态控制（ptrace seize/interrupt/wait/detach） | `kernel/` |
| 符号定位（ELF 符号、运行时结构地址） | `target/` |
| 布局知识（CPython 版本结构偏移） | `target/` |
| 图遍历（解释器→线程→帧→对象） | `cpython/` |
| 时间维度（快照 / 采样 / 事件流） | `observe/` |
| 呈现（文本 / JSON / 颜色） | `present/` |

横切模块 `errors.py` / `dto.py` 可被任意层引用且自身不依赖任何层；颜色策略属呈现层
（`present/color.py`，仅 present/cli 可引用）。import 方向由结构契约测试机械强制
（ADR A8，`tests/test_structure.py`）。

---

## 2. 模块结构（架构地图）

会话先读本表按「入口」定位文件；**禁止未读地图先全库 grep**。地图只写已落地结构；改结构时更新对应行并刷新验证日期（规则见 AGENTS.md）。

| 路径 | 职责 | 入口（文件:符号） | 禁令/依赖 |
|------|------|------------------|-----------|
| `pyprobe/kernel/` | 内核接口：字节传输 + 动态控制 | `mem.py:Transport`、`ptrace.py:PtraceEngine` | 禁 import 上层；仅横切 |
| `pyprobe/target/` | 目标知识：符号定位 + 布局 + 识别 | `layout.py:resolve_layout`、`symbols.py:ElfImage` | → kernel |
| `pyprobe/cpython/` | CPython 对象模型：零裸偏移遍历 | `frames.py:walk_frames`、`runtime.py:read_thread_chain` | → target |
| `pyprobe/observe/` | 时间维度：快照 / 采样 / 事件流 | `session.py:open_session`、`sampling.py:Sampler` | → cpython |
| `pyprobe/present/` | 呈现：文本 / JSON / 颜色 | `text.py:format_process`、`dumps.py:dump_python` | → observe |
| `pyprobe/cli.py` | argparse 薄壳（stack/syscall/record/top） | `cli.py:main` | 可 import 全部；web 延迟 import |
| `pyprobe/web/` | Web 编排：采样调度 + 订阅扇出、HTTP/SSE 服务、启动编排 | `monitor.py:Monitor`、`app.py:create_app`、`serve.py:serve_web` | 仅 observe + 横切 + `present.color`（A13 批 8b 修订窄例外）；禁 present 其余/cli |
| `pyprobe/dto.py` `pyprobe/errors.py` `pyprobe/offsets.py` `pyprobe/colors.py` | 横切（DTO/异常）+ 兼容门面 | — | dto/errors 禁 import 层；offsets/colors 层模块禁用 |
| `frontend/` | Web 前端（React + Vite，ADR A13）：EventSource 消费 SSE，页面结构对齐 top | `src/App.jsx` | 独立于 Python 分层；产物入库 `pyprobe/web/static/`，新鲜度由 CI 前端任务机械校验（FE3） |
| `tools/gen_offsets.c` | 偏移量生成器（真实头文件） | `scripts/gen_offsets.sh` | — |
| `scripts/` | 构建 / 测试 / CI 唯一入口 | `scripts/ci.sh` | 禁裸命令 |
| `tests/` | 测试（按 oracle 类型组织，§13） | `conftest.py:target_pid` | 禁照抄被测常量 |
| `reference/c/` | C 参考实现（交叉校验，不交付） | `scripts/build.sh` | 不对外交付 |

以代码为准，验证于 2026-10-12

---

## 3. 分层 API 设计

库 API 与 CLI 分离为三层（`collect_*` / `format_*` / `dump_*`），与包分层对应：

| 层 | 函数（名称；签名以代码为准） | 职责 | 返回/行为 |
|----|------|------|-----------|
| 采集 | `collect_python` / `collect_native` / `collect_syscalls` / `collect_profile` | Python 栈 / native DWARF 回溯 / syscall 追踪 / 采样聚合 | 结构化 dataclass |
| 引擎 | `open_session` / `collect_snapshot` / `Sampler` | 存活期不变量一次性解析 + 单次快照 / 长时采样 | `Session` / `list[ThreadInfo]` |
| 格式化 | `format_process` / `format_native` / `format_summary` / `format_folded` | 数据 → CLI 风格字符串（`present/text.py`） | `str` |
| 格式化 | `format_process_json` / `format_native_json` / `format_syscalls_json` | 数据 → JSON（`present/jsonout.py`） | `str` |
| CLI 封装 | `dump_python` / `dump_native` / `dump_syscalls` / `dump_record` / `dump_top` | collect + format + print + 退出码（`present/dumps.py`） | `int` |
| Web 编排 | `serve_web` | Monitor + FastAPI + uvicorn 编排（`web/serve.py`，ADR A13） | `int` |

> 上表只钉分层职责与命名；逐函数签名以代码为准，公开 API 面契约见
> [contracts.md §4](contracts.md) 模块契约表（双写漂移史见 ADR A14）。

设计要点：
- `collect_*` **不打印**，返回结构化 dataclass，调用方可程序化使用。
- `format_*` 是纯函数，输入数据输出字符串（ADR A5：格式化全部住 `present/text.py`，
  dto 只存数据）。
- `dump_*` 薄封装：颜色决策（按流独立）、异常 → stderr + 退出码、版本告警 surfacing。
- `cli.py` 是纯 argparse 薄壳，只做参数解析与分发（A6 保留决策）。

### 3.1 结构化数据类型（dto.py）

七类 dataclass（`FrameInfo` / `ThreadInfo` / `ProcessInfo` / `NativeFrame` /
`NativeThreadInfo` / `SyscallEvent` / `ProfileData`）只存数据、不格式化（A5）。
字段名/顺序/默认值以 `pyprobe/dto.py` 为准，`asdict` 键集即 JSON schema（复刻约束，
D1 以测试 pin 住）。
**路径缩短**（显示层约定）：数据字段始终保存完整路径，`present/text.py` 缺省
（`verbose=False`）缩短显示——Python 帧取路径**末 2 级**（对齐 py-spy），native
module 取 **basename**；`verbose=True`（`-v/--verbose`）保持完整路径。native 符号名
同属显示层：缺省截断 LLVM 尾部 `.llvm.<数字>`（ADR A11），数据面（DTO / JSON）保持原值。

### 3.2 公共 API 面与兼容映射

`__init__.py` 的 `__all__` 与旧树**逐项一致**，唯一显式例外是 ADR A1 新增的
`resolve_layout`（contracts.md §10.5-2 门禁；名单 pin 在 `tests/test_api_exports.py`）。旧名在
新实现上的绑定：

- `ProcessSession` = `observe.session.Session`；`resolve_process` = `open_session`
- `collect_frames` = `cpython.frames.walk_frames`；`collect_thread` = `build_thread`；
  `read_thread_chain` = `cpython.runtime.read_thread_chain`
- `SyscallStat` / `TraceFilter` / `Sampler` / `TopStats` 绑定 observe 实现
- `format_*` / `dump_*` 绑定 present 实现
- `offsets` 为只读兼容门面（API3；新代码必须经 `target.layout.resolve_layout` 取
  `Layout`，由 `tests/test_structure.py` 机械禁止层模块 import 该门面）

### 3.3 异常层级（errors.py）

`PyProbeError` 基类 + 9 个子类（进程缺失 / 权限 / 符号 / 解释器与线程状态 / 版本 /
attach / 采样退出 / 架构不支持）；名称与触发条件以 `pyprobe/errors.py` 为准。
消息逐字复刻旧语义（CLI `[!] {e}` 用户可见输出；E2）。`collect_*` 抛具体异常，
`dump_*` 捕获后转 stderr + 退出码。

### 3.4 颜色基础设施（present/color.py）

CLI 输出对齐 py-spy 的颜色语义，零依赖自实现：

| 输出元素 | 颜色 |
|----------|------|
| pid / tid / LWP tid | bold + yellow |
| 函数名 / native symbol | green |
| 文件名 / native module | cyan |
| 行号 / pc / (idle) 标记 | dim |
| `[!]` 错误行 / Backtrace stopped | red |

`should_color(stream)` 遵循 [clicolors spec](https://bixense.com/clicolors/)，
优先级短路：`CLICOLOR_FORCE != "0"` 无条件强制 → `NO_COLOR` 非空禁用 → 非 tty 禁用
→ `TERM=dumb` 禁用 → `CLICOLOR == "0"` 禁用 → 否则启用。stdout/stderr 独立检测。
颜色经显式 `color` 参数传递，**无全局开关**（保持 format 纯函数）。

---

## 4. 内存读取与视图分离（ADR A4）

`kernel/mem.py:Transport(pid, syscall=None)` 封装 `process_vm_readv(2)`（ctypes 绑定
libc）。读取循环处理 `EINTR`（重试）与短读（partial read）；任何错误返回 `None`，
不抛异常。

`kernel/views.py` 提供两种**内存视图**，各自声明一致性需求：

| 视图 | 语义 | 用于 |
|------|------|------|
| `SnapshotView(transport, *, max_pages=256)` | 页级 LRU，单次快照 | 单次栈转储（`collect_snapshot`，每次新建） |
| `LiveView(transport)` | 不缓存，每次读走传输层 | 长时观测（syscall 追踪、采样热路径） |

两种视图共有 `read` / `read_ptr` / `read_u32` / `read_u64` / `read_int`。常量
`PAGE_SIZE=4096` / `CACHE_MAX_PAGES=256` / `BYPASS_CACHE_THRESHOLD=4096`。

栈遍历具有高空间局部性（帧链、Code 字段、dict 条目），缓存整页把多次小读合并为每页
一次 syscall；≥1 页的读绕过缓存避免驱逐热数据；部分页读取（映射末尾）不缓存，调用方
按长度检查感知短读。长时观测不得复用快照缓存——目标内存持续变化会读到过期数据。

---

## 5. ELF 符号与加载基址（target/symbols.py + kernel/procfs.py）

`target/symbols.py` 手动解析 ELF64（`struct`，零依赖），支持 `.symtab` 与 `.dynsym`：

- `ElfImage.open(path)` → `.lookup` / `.read_at_symbol` / `.e_type`；解析缓存按
  `(path, mtime)` 键控。
- `find_symbol(exe_path, name, pid)`：类型过滤仅 `STT_NOTYPE`/`STT_OBJECT`；符号搜索
  序 `.symtab` 先于 `.dynsym`；`ET_DYN`（PIE）加 `load_base`，`ET_EXEC` 不加；找不到
  返回 0。
- `read_const(exe_path, name, length)`：`file_off = st_value − sh_addr + sh_offset`，
  **以节剩余字节（`sh_size`）为界**，越界/短读/缺符号 → `None`。

加载基址解析属 `kernel/procfs.py` 的 /proc 知识：`parse_load_base(maps_text, exe_path)`
取**首个** path 匹配行（含 `" (deleted)"` 后缀）起始地址；`load_base(pid, exe_path)`
包装读取，OSError → 0。字面匹配，路径解析策略不入 kernel 层（调用方经
`/proc/<pid>/exe` readlink 提供已解析路径）。

---

## 6. 布局知识：Layout 值对象（ADR A1）

`target/layout.py` 把 CPython 版本结构偏移建模为**不可变值对象**，
消灭旧树 `offsets._active` 全局单例与 `get()` 读路径副作用（旧 §8.1）：

- `resolve_layout(version_str, *, overrides_path=None) -> Layout`：显式解析；
  `Layout(version, key, verified, table)`（`table` 为 `MappingProxy`，不可变），
  `.get(name)`（缺键 `KeyError`）/`.get_or(name, default)`。
- 已验证版本（3.11–3.14）→ `verified=True`，`table` 为该版本表；未验证版本 →
  `DEFAULT_VERSION`（"3.12"）表 + `verified=False`，**不抛异常**（旧 `configure` 的
  "先填 fallback 后 raise"契约变更为 `verified` 标记；警告串由 `observe/session.py`
  用 `VersionNotSupported` 消息组装）。
- `Layout.key` 恒为 backing 表的版本，`requested` 版本另行保留（防误导诊断）。
- **dev override**：`overrides.json`（`offsets_path`）的 `_version` 与目标版本匹配时
  整表替换 base；`overrides_path` 为显式参数（`None` 用默认；测试 autouse 指向不存在
  路径，契约 L12）。
- 版本差异只住 Layout：`get` 命中则用该版本值，缺键经 `get_or` 分支探针（如 3.11 无
  `InterpreterState.imports`、3.13 无 `ThreadState.cframe`）。
- `supported_versions()` 升序；`DEFAULT_VERSION` 公开常量。

`tools/gen_offsets.c`（经 `scripts/gen_offsets.sh`）从真实头文件生成
`pyprobe/offsets.json`。新版本接入以 spec-oracle 测试为入口：内置表 vs 生成产物
（`tests/test_layout.py`，契约 L8/L9）。

**新增偏移键流程**：`tools/gen_offsets.c` 加字段 → `target/layout.py` 四版本
`_VERIFIED_OFFSETS` 表加值 → `tests/test_layout.py` oracle 加键 →
`scripts/gen_offsets.sh` 重生成 offsets.json。非本地版本的表值：在对应 Python
版本环境运行 `scripts/gen_offsets.sh` 生成该版本产物后并入内置表（需真实头文件；
CI 跨版本矩阵 `scripts/ci_target.sh` 负责各版本集成验证，不产表值）。

---

## 7. CPython 数据结构遍历（cpython/，ADR A3）

每个 typed reader 绑定 `Layout`，**遍历代码零裸偏移运算**——所有偏移经
`layout.get` / `get_or` 在 reader 内部消费；跨度装配（Code 头、ThreadState 字段簇的
单次合并读）归对应模块内部实现，对外只暴露类型化字段。

### 7.1 帧链与运行时图（frames.py / runtime.py）

```
resolve_interpreter(view, layout, runtime_addr)   # main → head 回退
  → read_thread_chain(view, layout, interp_addr)  # ThreadState 链，MAX_THREADS=256
    → current_frame_of(view, layout, tstate_addr)
        （layout 探针 ThreadState.current_frame 直达 3.13+；
         否则 cframe → CFrame.current_frame 间接 3.11/3.12）
      → walk_frames(view, layout, frame_addr, trampoline_addr)  # MAX_FRAMES=100
```

- `walk_frames` 沿 `previous` 链走：跳过 `f_code == 0` 的帧与 trampoline 帧；
  `co_name` 与 `co_filename` 均 NULL 的尾部陈旧帧（3.13 datastack 残留）终止遍历；
  返回 `list[dto.FrameInfo]`（内层在前）。
- `code.py`：`read_code_header` 合并读 `co_firstlineno`/`co_filename`/`co_name`；
  `addr2line(view, layout, code_addr, lasti, firstlineno)` 包装行号解析；
  `line_for_offset` 为纯函数（PEP 626 解码，含 CPython signed varint 语义）。
  `lasti = prev_instr − (code + co_code_adaptive)`，负值钳 0。
- 行号解析正确性以 **spec-oracle** 守护：`line_for_offset` vs 真实 CPython
  `co_lines()` 地面真值（contracts.md §10.2-1）。

### 7.2 字典迭代器（dicts.py）

`DictReader(view, layout)` 支持 3.11–3.14 的全部变体：combined（`kind=0`，
`PyDictKeyEntry`，key 偏移 8）、unicode（`kind=1`，`PyDictUnicodeEntry`，key 偏移 0）、
split（values 数组独立）、managed dict（`Py_TPFLAGS_MANAGED_DICT`，各版本 pre-header
槽位约定不同——3.11 双槽 / 3.12 tagged 单槽 / 3.13+ untagged 可内嵌）。
`from_dict` / `from_managed_values` / `.next`。

### 7.3 对象读取（objects.py）

- **PyLong**：3.12+ 读 `lv_tag`（`size = tag >> 3`）；3.11 读 `ob_size`（`get_or` 回退
  链）；支持 0/1/2 位 digit（每位 30 位），>2 位 → `None`。
- **PyBytes**：`ob_size` 上界 `MAX_STR_LEN`（`1<<20`，对象层尺寸上界）；数据读失败 →
  `None`。
- **PyUnicode**：解析 `PyASCIIObject` state byte（compact=bit5 / is_ascii=bit6 /
  kind=bits2-4）；compact+ascii 读内联数据；compact 非 ascii 按 kind
  （latin-1 / utf-16-le / utf-32-le）；非 compact 从 `data_any` 指针读。

### 7.4 线程名（names.py）

`get_thread_names(view, layout, interp_addr) -> {thread_id: name}`。定位
`sys.modules`（3.12 走 `InterpreterState.imports + _import_state.modules` 直针；其余
版本遍历 `sysdict` 找 `"modules"`），找 `threading` 模块，读其 `_active` 字典，实例
字典解析处理 managed/non-managed 两种（§7.2 槽位约定）；任何失败 → 部分/空 dict，不抛。

---

## 8. Native 栈转储（observe/native.py + kernel/ptrace.py，ADR A2）

### 8.1 统一 ptrace 引擎（kernel/ptrace.py）

syscall 追踪与 native 回溯共享同一套 seize/interrupt/wait 状态机（消灭旧树两套 attach
逻辑）。`PtraceEngine(pid, *, feature="ptrace", restart_op=PTRACE_SYSCALL)`：

- `seize(options=0)`：扫 `/proc/<pid>/task` 逐线程 `PTRACE_SEIZE` + 循环 re-scan
  （捕获扫描间隙新建的线程）；任意异常**全量回滚**（已 seize 线程 DETACH，A7 修复）；
  非主线程序列中死亡跳过。
- `stop_all()` / `restart_all()` / `resume(tid, sig=0)` / `wait_event()`（返回
  `SyscallStop` / `ExecEvent` / `Exited` 事件）/ `getregs(tid)` / `detach()`（幂等）。
- **arch 检查下沉 `__init__`**：非 x86-64 → `UnsupportedArchitecture`（feature 名由
  消费方传入以保消息语义；A7 修复——旧树 CLI 路径绕过该检查）。
- `UserRegs` x86-64 布局 + `ARG_REGS=(rdi, rsi, rdx, r10, r8, r9)`。
- 事件语义（分组停止吞掉、真实信号转发、TRACECLONE 注册新线程、TRACEEXEC 补发）见
  契约 T 系（`tests/test_ptrace.py` 全状态路径 stub 覆盖）。

### 8.2 流程（native）

`collect_native(pid)`：`PtraceEngine(feature="native stack dump").seize(options=0)` →
`stop_all()` → libdwfl（`assume_ptrace_stopped=True`）遍历线程/帧 → `finally detach()`。
**ATTACH→SEIZE**（安全 detach 理由同 §14.2）。libdw/libdwfl 惰性加载（非 native 模式
导入无副作用）。

- 帧解析：`dwfl_frame_pc`（非激活帧 `lookup=pc−1`）、`dwfl_module_addrname`（缺 →
  `??`）、`dwfl_module_info` 取模块路径；单线程帧上限 `_MAX_FRAMES=256`。
- 源码位置（A12）：`dwfl_module_getsrc`+`dwfl_lineinfo` 取 `(filename, line)`，仅在
  模块带 DWARF 行表时可得（gate 于 `available_symbols`）；否则帧只有模块路径。
- 线程结果按 tid 升序；`unwind_failed` = `fr != 0 且 0 帧`，或 lone `pc == 0`
  哨兵帧（对齐 gdb：io_uring `iou-sqp-*` 等内核工作线程回溯只得一个 `pc == 0`
  假帧，gdb 对此打印 `Backtrace stopped: Cannot access memory at address 0x0`——
  假帧保留显示，但判定为失败并输出同款红色提示行）。
- ctypes 回调（`CFUNCTYPE`）经 `ctypes.py_object` 传递 Python list 收集结果。

> A2 使 native 行为有变，contracts.md §10.5-1 的 `stack --native --json` 旧新差异对比为其强制验证
> 项（批 7 出口）。

---

## 9. 权限模型

### 9.1 Python 栈 / 采样模式（默认）

使用 `process_vm_readv(2)`，**不需要 ptrace attach**。

| 条件 | 是否可用 |
|------|----------|
| root（CAP_SYS_PTRACE） | 任意进程 |
| 同用户 + `ptrace_scope=0` | 任意进程 |
| 同用户 + `ptrace_scope=1`（默认） | 仅子进程 |
| 目标进程 `dumpable=0` | **仍可读取** |

### 9.2 Native / Syscall 模式

使用 ptrace（native：SEIZE + DWARF unwind；syscall：SEIZE + SYSCALL），权限要求同表
（但 `dumpable=0` 目标**不可用**），另有约束：

| 条件 | 是否可用 |
|------|----------|
| 非 x86-64 架构 | **不可用**（`UnsupportedArchitecture`） |
| 目标含分离后的线程 | 仅追踪 attach 时刻已存在 + TRACECLONE 捕获的线程 |

> uvicorn / FastAPI 运行时会将 `dumpable` 设为 0，导致 native/syscall 模式在非 root
> 下无法 attach；Python 栈与采样模式不受此限制。

`process_vm_readv` / `ptrace` 要求目标进程是当前进程的后代（`ptrace_scope=1`）。集成
测试通过 `subprocess.Popen` 派生子进程作为探测目标，天然满足此条件。

```bash
cat /proc/sys/kernel/yama/ptrace_scope   # 检查当前 ptrace_scope
sudo python -m pyprobe stack -p <pid> --native   # root 可绕过所有限制
```

---

## 10. CPython 版本支持

| CPython 版本 | 架构 | 状态 |
|-------------|------|------|
| 3.11.x | x86-64 | 已验证 |
| 3.12.x | x86-64 | 已验证 |
| 3.13.x | x86-64 | 已验证 |
| 3.14.x | x86-64 | 已验证 |
| 3.11–3.14 | aarch64 | 未验证（偏移量理论上与 x86-64 相同，待实际验证） |

对未验证版本，`resolve_layout` 以 `DEFAULT_VERSION`（"3.12"）表 + `verified=False`
返回；`observe/session.py` 在 stderr 输出告警。可用 `scripts/gen_offsets.sh` 为目标
CPython 生成偏移量，验证后经 dev override 或编入内置表（§6）。

### 限制

- 仅支持 **Linux**（依赖 `/proc`、`process_vm_readv`、`ptrace`）。
- 偏移量已验证 x86-64；syscall/native 追踪仅 x86-64（aarch64 需 syscall 号表 + 寄存器
  ABI 适配）。

---

## 11. C 参考实现（reference/c/）

C 版本为开发辅助参考实现，**不对外交付**，仅用于与 Python 实现交叉校验。

| 特性 | C 参考实现 | Python 实现 |
|------|--------|-------------|
| Python 栈转储 | `build/pyprobe <pid>` | `python -m pyprobe stack -p <pid>` |
| Native 栈转储 | `build/pyprobe <pid> --native` | `python -m pyprobe stack -p <pid> --native` |
| 运行时依赖 | libdw.so、libelf.so、libz | libdw.so.1（仅 native 模式） |
| 编译需求 | C 编译器 + CPython 头文件 | 仅生成偏移量时需要 |
| 外部 Python 包 | 无 | 无（仅标准库 ctypes/struct） |

---

## 12. 集成模式

### 12.1 Wheel 包安装

Wheel 标签 `py3-none-linux_{x86_64,aarch64}`（纯 Python，Linux）。设计保障：

- `offsets.json` 经 `[tool.setuptools.package-data]` 打入 wheel；即使缺失，内置表也
  能工作。
- 子包（kernel/target/cpython/observe/present）经 `[tool.setuptools.packages.find]`
  打包。
- libdw 惰性加载——非 native 模式导入无副作用。

### 12.2 源码集成 + 命令行工具

`pyprobe <subcommand> -p <pid>`（argparse，支持 `--help`/`--version`）。
`gen_offsets.sh` 依赖 C 编译器 + CPython 开发头文件。

### 12.3 源码集成 + 接口

```python
from pyprobe import collect_python, format_process, PyProbeError

try:
    proc_info, threads = collect_python(pid)
except PyProbeError as exc:
    ...
else:
    print(format_process(proc_info, threads))
```

`resolve_layout(version)` 可用于在无目标进程时直接取版本布局（A1 新增 API）。

### 12.4 Web 监控模式（可选 extras，ADR A13）

> 用法（安装、参数、权限）见 [README](../README.md#web-监控web)；本例已重跑核对
> （2026-10，默认端口起服、启动行与页面/接口响应真实可复现）。

```bash
pip install 'pyprobe[web]'        # fastapi + uvicorn 最小集；核心 wheel 零依赖不变
pyprobe web -p <pid>              # http://127.0.0.1:9470/ 实时热点 + 完整线程调用栈
```

- `web/` 与 cli 平级（依赖方向 `… → present → {cli, web}`）；web **不得 import
  `present`**（窄例外 `present.color`；JSON 面经 `dataclasses.asdict` 直出——文本
  格式化是终端专属语义，结构测试 ST-W1 机械强制）；`pyprobe/__init__.py`
  不 import web，缺依赖时 `pyprobe web` 友好报错（rc 2），其余子命令不受影响。
- 前端为 React + Vite（`frontend/`），构建产物**入库 git**（`pyprobe/web/static/`），
  wheel 构建不依赖 Node；离线构建流程（§12.1 与 README）不受影响。
- SSE 协议与 API schema 契约见 contracts.md 批次 8（WM/WA/WS/WC/ST-W/FE 系）。

---

## 13. 测试架构

测试按 **oracle 类型**组织（非按模块镜像），权威来源优先于自指断言：

### 13.1 spec-oracle 测试（最高价值）

被测实现对照**独立权威来源**：

- `line_for_offset` vs CPython `co_lines()`（真实函数集全指令偏移逐点一致）。
- 内置 layout 表 vs `gen_offsets` 真实头文件产物（去 `_version` 比对）。
- `SYSCALL_NAMES` 全量 vs `/usr/include/.../unistd_64.h` 解析；flags 抽样 vs
  fcntl.h/mman.h。
- syscall 渲染样例 vs strace 真实输出（strace 缺席时 skip）。
- native 线程头/符号样例——批 7 以 `stack --native --json` 旧新差异对比为强制验证项。

### 13.2 构造内存单测（无子进程）

`tests/fakemem.py` 的构造器由 `Layout` 驱动（**禁止从被测常量照抄**），版本布局 ×
对象变体矩阵参数化（3.11–3.14）。视图层用 stub 传输覆盖全缓存路径（LRU 淘汰、跨页、
旁路、LiveView 不缓存）。

### 13.3 状态机单测

统一 ptrace 引擎以 stubbed libc/waitpid 覆盖全状态路径（CLONE/EXEC/信号转发/detach
幂等；契约 T 系）。

### 13.4 存活集成测试（`@pytest.mark.integration`）

- `target_pid` / `fast_syscall_pid` session fixture（`tests/conftest.py`）经子进程
  stdout 的 **READY 行握手**（就绪轮询替代固定 sleep）；`try/finally` 中
  `terminate()` + `wait(5)`。`TARGET_PYTHON` 指定目标解释器做跨版本端到端。
- session→snapshot→sampling→tracing 端到端（`test_session_snapshot.py` /
  `test_cli_live.py` / `test_ptrace_live.py`）。
- `slow` marker：真实等待的测试，`scripts/run_tests.sh -- -m 'not slow'` 跳过。
- 非 Linux / ptrace 权限不足自动 skip。integration 预算 ≤15s。

### 13.5 结构契约测试

`tests/test_structure.py` 以 ast 扫描机械强制 import 方向（kernel→…→present→{cli, web}
单向，横切层不自咬）与 offsets facade 禁用；`tests/test_package.py` 守护包解析根、offsets.json
与"旧模块名不可导入"；`tests/test_api_exports.py` pin `__all__` 名单。

### 13.6 手动探测约束

手动端到端验证时：

- **必须** `subprocess.Popen` 派生子进程并经 stdout 获取真实 PID（复用
  `tests/conftest.py:target_pid` 模式），`try/finally` 中 `terminate()` + `wait(5)`。
- **禁止** shell `&` + `$!`/`pgrep` 取 PID（`uv run` 包装器导致 PID 错位）。
- **禁止**在持久 shell 里裸用 `&` 跑长驻进程（管道继承致 shell 卡死）。

---

## 14. Syscall 追踪（observe/syscalls.py / observe/syscall_abi.py）

`pyprobe syscall -p <pid>` 实时追踪目标**所有线程**的系统调用（strace 风格），含
`--summary`（strace -c 等价）与 `--json`（与 `--summary` 互斥）。仅 x86-64。

### 14.1 静态数据（syscall_abi.py）

`SYSCALL_NAMES`（号→名）+ `SYSCALL_NRS`、`DECODE`（98 常用 syscall 的逐参数类别）、
`TRACE_GROUPS`（file/network/process/memory/signal/desc 类组）、
`OPEN_FLAGS`/`MAP_FLAGS`/`PROT_FLAGS`。权威来源为系统头文件（spec-oracle 守护）。

### 14.2 引擎（消费统一 ptrace 引擎）

`collect_syscalls(pid, *, trace, max_events, verbose, on_event=None)`：
`PtraceEngine(feature="syscall tracing").seize(TRACESYSGOOD|TRACECLONE|TRACEEXEC)` →
`stop_all()` → `restart_all()` → `wait_event` 循环 → `finally detach()`。

- entry/exit 按相位配对（phase 隐含在 stash 有无）：entry 暂存 `(nr, 6 args, 时间戳)`；
  exit 弹出并发射事件。**6 参数全显**（复刻约束）。
- 参数读取经 **LiveView**（长时追踪不缓存）。
- `ret > 0xFFFFFFFF00000000` → 减 `1<<64`；`-4096 < ret < 0` → `error = -ret`、
  `ret = -1`。
- `ExecEvent`：`execve` 无常规 exit stop，补发 exit 事件（**走用户 TraceFilter**，A7
  修复——旧树用全匹配过滤器）；处理后 resume。
- `max_events` 到量即停；`Exited` / ECHILD 结束循环。`on_event` 发射即回调（dump 层
  流式输出与 KeyboardInterrupt 部分结果的通道）。

### 14.3 参数渲染与过滤（复刻约束）

- `escape_bytes` / `truncate_escaped` / `render_str_arg`：strace 风格字符串（非可见
  ASCII 转 `\NNN`；非 verbose 截断 32 字符 + `...`）。
- `read_cstr`（NUL 终止，完全不可读回退裸地址，`read_available` 二分探测短映射）；
  `read_timespec`（`{sec, nsec}`）。
- `decode_args`（entry）+ `fill_out_args`（exit 后拼接缓冲区 `0xaddr/"..."`；
  A7：`ret==0` 且 `buf_in` → 空串 `""`；`timespec_out` 于 exit 重读）。
- 标量规则：`fd`/`dirfd` 以寄存器**无符号**值与 `AT_FDCWD` 魔数直比（无符号扩展），
  其余 `str(value)`；`addr`/`rem` 无特殊指针渲染，走「int 范围 str 否则 hex」；`mode`
  八进制；`signal` → `SIG<n>`；flags OR 解码 + 残余 hex；未识别 syscall 全 hex。
- `TraceFilter`：空 → 全追踪；纯 `!` 排除 → 除排除外全；**混合 → include − exclude**
  （A7 修复：旧树并入 include 致语义反转）。
- `format_summary` + `SyscallStat`：strace -c 风格统计表（total/s 列等复刻约束）。

### 14.4 已知坑位

- `orig_rax` 可能符号扩展为 `-1`，取低 32 位恢复 nr。
- x86-64 返回值错误判定用无符号比较（`ret > 0xFFFFFFFF00000000`）后再转有符号。
- clone 事件里新线程 tid 由随后 SIGSTOP delivery-stop 的 wpid 提供（`tid not in tids`
  分支注册）。
- `waitpid` 可能被信号打断（`InterruptedError`），循环 continue 重试；`KeyboardInterrupt`
  时 `dump_syscalls` 仍 detach 并打印已收集 summary / JSON。
- attach 时线程若已在 syscall 内，首个 stop 可能是 exit-stop 而无对应 entry（相位由 stash
  有无隐含）——可能产生一条伪事件；已知 ptrace 类问题，保留为行为观察点。

---

## 15. 采样引擎（observe/sampling.py / profile.py / topstats.py）

`record` 周期采样聚合 folded stacks；`top` 持续采样 + 终端刷新。共享 `Sampler`，
`process_vm_readv(2)`，**不需要 ptrace attach**（权限同 §9.1）。

### 15.1 Sampler

`open_session` 一次性解析进程存活期不变量（exe / `_PyRuntime` / 版本 + `resolve_layout`
/ interp / trampoline / `ProcessInfo` / 线程名），`Sampler` 在其上做热路径：

- `sample() → list[ThreadInfo]`：**每样本新建 SnapshotView**（跨样本复用会读陈旧内存）；
  读线程链（`NoThreadState` → `ProcessExited`）；`/proc stat` 非 `R` → `idle_hint=True`
  剪枝（跳过帧遍历）；按 `native_tid` 升序。
- `refresh_names()`：重读线程名。`record` 全程不调（名字是聚合键，需稳定）；`top` 每
  周期调一次。
- `view_factory` 为测试注入点。

### 15.2 调度（profile.py）

**绝对时间调度防漂移**：`next_t += interval`；单次采样落后过多时重置基线（不追赶）。
`KeyboardInterrupt` / `ProcessExited` 捕获后返回**部分数据**；目标 init 错误仍抛
`PyProbeError` 子类。

### 15.3 top 聚合（topstats.py）与渲染（present/text.py）

`TopStats` 为纯数据状态（A5：render 归 present）：`update(threads)` 计 own（叶子帧名，
`None → "?"` 归一）/ total（栈内去重）/ samples / idle_samples / current。

A7 修复：idle 守卫查 `idle_threads` 去重（旧树误查 `current` 致同一 idle 线程每周期
重复 append 无限膨胀），且 idle 线程同时从 `current` 移除。

`render_top(stats, proc_info, *, elapsed, color, top_n)`：进程头 rstrip + Elapsed/样本数
+ Active threads 表 + Idle threads 行 + Top functions 表。终端进入隐藏光标、整屏重写、
`finally` 恢复；**非 tty → rc 2**（无静默降级）；Ctrl-C/目标退出 → 干净退出 + stderr
摘要，rc 0。

### 15.4 folded stacks（profile.py）

每行 `"<帧0>";<帧1>;... <count>`，帧序 **root → leaf**；线程前缀 `"<name>"` /
`tid-<native_tid>`；帧名纯函数名；排序 count 降序 + 字典序；空 → 空输出。`dump_record`
stdout 纯 folded、进度/摘要走 stderr（管道安全），`-o` 写文件。

### 15.5 JSON 输出（present/jsonout.py）

`format_process_json` / `format_native_json` / `format_syscalls_json`：
`dataclasses.asdict` + `json.dumps(indent=2, ensure_ascii=False)`。JSON **无色**、字段
完整路径、`None → null`；错误路径仍走 stderr 文本 + rc 1；syscall `--json` 抑制文本流，
追踪结束后输出单个 `{"events": [...]}`。

### 15.6 守护测试（防退化）

- 版本告警恰好一条且不打在 collect 层（`resolve_layout` 不打印；session 组装，dump 层
  输出）。
- 每 `sample()` 新建 view（factory 计数）；`sample()` 不刷新线程名，`refresh_names()`
  生效。
- `fold_key` root-first（reverse 守护）；绝对调度 `next_t += interval`。
- `color=False` 渲染无 ANSI；JSON 可 `json.loads` 并与 `collect_python` 交叉验证。

---

## 16. 有意为之的怪行为（防误修清单）

下列行为**看似 bug，实为刻意**——逐字节对齐旧版 / strace / gdb，或是对外兼容承诺。
改动前先读对应章节；每项均有守护测试，"修复"它们会让测试变红（契约侧见
contracts.md §3 复刻约束）。

| 行为 | 为什么 | 详见 |
|------|--------|------|
| native 线程只回溯出一个地址为 0 的帧 → 判回溯失败 | io_uring 等内核线程的假帧；gdb 同样报 "Backtrace stopped" | §8.2 |
| 文本输出截断符号尾部 `.llvm.<数字>` | gdb 显示短符号名；JSON 数据面保留原值 | §3.1 |
| syscall 参数 6 个全显、summary 带 `total/s` 列、字符串 32 字符截断 | 对齐 strace 输出 | §14.3 |
| 栈输出线程按 tid 升序；路径缩短（Python 帧末 2 级 / native basename） | 复刻旧版输出格式 | §3 / §8 |
| `top` 在非 tty 环境直接退出（rc 2），不降级 | 非交互场景无意义，宁肯明示环境不满足 | §15.3 |
| `--json` schema 与退出码逐字节稳定 | 兼容承诺；唯一豁免：native 帧新增 `filename`/`line`（§8.2 源码位置） | §3.2 |
| `record` 进度/摘要去 stderr，folded 数据只去 stdout | 管道洁净：数据流不被日志污染 | §15.4 |
| native 帧能解析源码位置时输出 `at file:line`，与 `from <module>` 互斥 | gdb 语义 | §8.2 |
