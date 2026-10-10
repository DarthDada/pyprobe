# next/ 重建契约（TODO §10）

> 本文件是 next/ 重建的**契约单一事实来源**：ADR 全文、分层与依赖规则、模块契约骨架、
> 行为点清单机制、派发包模板。旧树架构 SSOT 仍是根 `docs/design.md`（冻结期内不改）；
> 替换完成后（§10.6-4）design.md 以本文件为准重写。
>
> 铁律复述：**冻结旧树**（`pyprobe/`、`tests/` 不接受新功能）、**无新功能**（§2 等替换后再做）、
> **ADR 纪律**（架构变更仅限本文件 ADR 清单，新想法必须先成 ADR 经主代理批准）。

## 1. 第一性原理与分层

pyprobe 的本质是**外部观测者问题**：仅凭 PID 与内核接口重建 CPython 运行时状态并呈现。
七项不可约能力各有唯一归属包；每包至少归属一项，否则不建包（防过度拆分）。

| 能力 | 归属包 |
|------|--------|
| 字节传输（process_vm_readv / procfs 读内存） | `kernel/` |
| 动态控制（ptrace seize/interrupt/wait/detach） | `kernel/` |
| 符号定位（ELF 符号、运行时结构地址） | `target/` |
| 布局知识（CPython 版本结构偏移） | `target/` |
| 图遍历（解释器→线程→帧→对象） | `cpython/` |
| 时间维度（快照 / 采样 / 事件流） | `observe/` |
| 呈现（文本 / JSON / 颜色） | `present/` |

**依赖方向铁律**：`kernel → target → cpython → observe → present → cli`，仅横切层
（`errors` / `dto` / `color`）可被任意层引用且自身不依赖任何层。import 方向由结构契约
测试机械强制（ADR A8），禁反向。

## 2. 架构决策记录（ADR——重构允许的全部架构变更）

> 决策已确认（2026-10）：A1、A2、A3/A4/A5 三件套、A8 全部纳入。每项 ADR 落地时须有
> 守护测试，且测试注释回链本文件对应条目（§10.5-5 验收项）。

### A1. Layout 值对象化（吞并旧 §8.1）

`resolve_layout(version) -> Layout` 显式传递，消灭旧 `offsets._active` 全局单例与
`get()` 读路径副作用（隐式 configure）。收益：可同时观测不同 CPython 版本的进程；
测试无需手工复位全局态。dev override（offsets.json）语义保留。库 API 面新增
`resolve_layout` 为 §10.5-2 的显式例外。

### A2. 统一 ptrace 引擎（`kernel/ptrace.py`）

一套 seize/interrupt/wait 状态机 + GETREGS + detach，syscall 追踪与 native 回溯共享；
native 侧 ATTACH→SEIZE（与旧 design.md §14.2 相同的安全 detach 理由）；消灭旧
native_dump 与 syscall_tracer 两套 attach 逻辑。native 行为有变，§10.5-1 的
`stack --native --json` 旧新差异对比为其强制验证项。

### A3. CPython 对象模型层（`cpython/`）

PyLong/Unicode/Bytes/Dict/Code/Frame/ThreadState/InterpreterState 各有绑定 Layout 的
typed reader；遍历代码零裸偏移运算（消灭旧 stack_dump 内 co_lo/co_hi 式跨度心算）；
版本差异只住 Layout。

### A4. 内存视图分离

`SnapshotView`（页级 LRU，单次快照语义）/ `LiveView`（不缓存，长时观测）替代旧树
"RemoteReader 缓存 + read_uncached + _UncachedReader + 每 sample 手工新建 reader"
四个零散机制；每种观测声明自己的一致性需求。

### A5. DTO 去 format() 化

数据与呈现严格分离（反转旧 §8.9 妥协）：格式化全部入 `present/text.py`，dto 只存数据。

### A6. 保留决策（第一性原理验证后保留，非惯性）

collect/format/dump 三层语义、CLI 薄壳、errors 层级、clicolors 颜色策略维持不变——
在新分层中以 session→snapshot→present 与 `present/color.py` 的形态重现。

### A7. 行为修复（吞并旧 §9 P0/P1）

随对应批次落地，旧树不单独修：

- TopStats idle 统计（旧 §9 P0-1）：守卫查 idle 列表去重，active→idle 从 current 移除；
  守护测试覆盖"同一 idle 线程连续采样"与"active→idle 转换"（批次 4）
- arch 检查下沉到 ptrace 引擎 attach 路径（旧 §9 P0-2；批次 1/5）
- TraceFilter 混合包含/排除改 `include - exclude` 语义（旧 §9 P0-3；批次 5）
- 旧 §9 P1 批次：timespec_out exit 重读、EOF 空渲染、execve 走用户过滤器、attach 全
  异常回滚、无名帧 OWN% 归一、琐碎项打包（批次 4/5）

### A8. 子包化与依赖规则

`kernel/ target/ cpython/ observe/ present/` + 横切（errors/dto）；import 方向
由结构契约测试机械强制（禁反向）。包名仍为 `pyprobe`，替换免改名。
颜色策略属呈现层（`present/color.py`，仅 present/cli 可引用），非横切——
A5 落地后 dto 不再依赖颜色（2026-10 批次 1 契约细化，修订 TODO §10.1 A8 原文
"横切含 color" 的表述）。

## 3. 复刻约束（非差异，禁止"顺手优化"）

下列"怪但文档化"行为一律保留，契约与测试逐项点名：

- syscall 渲染：6 寄存器参数全显；summary `total/s` 列；32 字符截断；errno 名解码；
  elapsed 计时格式
- 栈输出：线程升序；路径缩短规则（Python 帧末 2 级 / native basename）；空闲双启发式
- CLI：JSON schema 与退出码逐字节兼容（豁免仅 A7 涉及字段）
- `record` folded 输出键集合（计数天然抖动，§10.5-1 只比键集合）

## 4. 模块契约骨架（按批次填充）

> 每批设计时，主代理把对应"公开 API / 行为点"两列填实；骨架只锁定职责与归属。
> 行为点清单状态：`已覆盖` / `已放弃（理由入 ADR）`。

### 批次 1 — `kernel/` + 横切

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `kernel/mem.py` | 字节传输：远程读原语与错误语义 | `Transport(pid, syscall=None)`；`.read(addr, length) -> bytes \| None` | 见 §5.1 批 1 表 |
| `kernel/views.py` | A4：SnapshotView / LiveView | `SnapshotView(transport, *, max_pages=256)`；`LiveView(transport)`；共有 `read/read_ptr/read_int/read_u32/read_u64`；常量 `PAGE_SIZE/PAGE_MASK/CACHE_MAX_PAGES/BYPASS_CACHE_THRESHOLD/PTR_SIZE/PTR_FMT/INT_SIZE/INT_FMT` | 见 §5.1 批 1 表 |
| `kernel/procfs.py` | /proc 元信息（cmdline/task 枚举/comm） | `read_cmdline(pid) -> str \| None`；`list_tids(pid) -> list[int]`；`read_comm(pid, tid) -> str` | 见 §5.1 批 1 表 |
| `kernel/ptrace.py` | A2：统一 ptrace 状态机（批内最大件） | `PtraceEngine(pid, *, feature="ptrace", restart_op=PTRACE_SYSCALL)`；`.seize(options=0)`/`.stop_all()`/`.restart_all()`/`.resume(tid, sig=0)`/`.wait_event()`/`.getregs(tid)`/`.detach()`；事件 `SyscallStop/ExecEvent/Exited`；`UserRegs`/`ARG_REGS`/PTRACE_* 常量 | 见 §5.1 批 1 表 |
| `errors.py`（横切） | 错误层级（保留旧语义，消息逐字复刻） | `PyProbeError` + 9 子类（同名同属性同消息） | 见 §5.1 批 1 表 |
| `dto.py`（横切） | A5：纯数据 DTO（无 format()，无颜色依赖） | `FrameInfo/ThreadInfo/ProcessInfo/NativeFrame/NativeThreadInfo/SyscallEvent/ProfileData`（字段与默认值同旧 types.py） | 见 §5.1 批 1 表 |

#### 批 1 行为点清单（§5.1 实例）

**kernel/mem.py**（旧 `memory.py` `_read_syscall` 部分）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| M1 | 读循环直至填满；部分成功继续读 | test_memory.py 间接 | test_mem.py::test_partial_then_complete |
| M2 | 任何错误返回 None（不抛异常） | test_unreadable_page_returns_none | test_mem.py::test_error_returns_none |
| M3 | 读到 0 字节返回 None | （旧代码 L83-84） | test_mem.py::test_zero_bytes_returns_none |
| M4 | 短读如实返回（长度由调用方校验） | test_partial_page_uncached | test_mem.py::test_short_read_returned |
| M5 | EINTR（errno 4）重试 | （旧代码 L76-78） | test_mem.py::test_eintr_retried |
| M6 | length==0 返回 b"" 且无 syscall | test_zero_length | test_views.py（视图层拦截，传输层同语义） |

**kernel/views.py**（旧 `memory.py` RemoteReader 缓存 + `_UncachedReader` + `read_uncached` + sampler 手工新建 reader 四机制合并为 A4）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| V1 | 单页读命中缓存，第二次同页无 syscall | test_cached_page_no_second_syscall | test_views.py::TestSnapshotView |
| V2 | 不可读页返回 None | test_unreadable_page_returns_none | 同 |
| V3 | 部分页（映射末尾）不缓存、透传 | test_partial_page_uncached | 同 |
| V4 | 跨页装配；任一缺失页 → None | test_multi_page_read / test_multi_page_missing_returns_none | 同 |
| V5 | ≥PAGE_SIZE 读绕过缓存 | test_large_read_bypasses_cache | 同 |
| V6 | LRU 超 CACHE_MAX_PAGES(256) 逐出最旧 | test_cache_evicts_oldest | 同 |
| V7 | read_ptr/u32/u64/int 小端解码；短读 → None | TestReadHelpers | 两视图各一组 |
| V8 | LiveView 每次读都走传输层（长时观测一致性，A4）；读粒度为页对齐整页取读后切片（与 SnapshotView 共享装配路径；页内读不跨映射，无正确性差异） | （旧 `_UncachedReader`/read_uncached 语义） | test_views.py::TestLiveView |
| V9 | 常量：PAGE_SIZE=4096 / PTR_SIZE=8 / BYPASS==PAGE_SIZE / CACHE_MAX_PAGES=256 | TestConstants | test_views.py::TestConstants |

**kernel/procfs.py**（旧 `procmeta.py` + `syscall_tracer._list_tids` + `native_dump._read_comm`）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| P1 | cmdline NUL→空格、去尾空格、UTF-8 replace 解码；OSError → None | （旧 procmeta docstring） | test_procfs.py |
| P2 | list_tids 升序 int 列表；进程缺失 → ProcessNotFound | test_attach_missing_process 间接 | test_procfs.py |
| P3 | PermissionError → PermissionDenied（**A7 琐碎项修复**：旧树漏捕获，用户见 traceback）；不捕获 ProcessLookupError（旧死代码） | （旧 §9 P1-9） | test_procfs.py::test_permission_denied |
| P4 | read_comm 去空白；OSError → "" | （旧 native_dump L140-145） | test_procfs.py |

**kernel/ptrace.py**（旧 `syscall_tracer.py` 引擎 + `native_dump.py` `_attach_all_threads`/`_detach_all` 合并为 A2）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| T1 | seize 全线程 + 重扫循环补抓新线程；options 随 SEIZE 传递 | test_seize_all_threads | test_ptrace.py::TestSeize |
| T2 | seize 失败回滚：已 seize 线程全部 DETACH；**A7 P1-4 修复**：任意异常（非仅 AttachFailed）都回滚 | test_attach_failure_rolls_back | TestSeize::test_rollback_on_any_exception |
| T3 | 主线程 seize 失败 → AttachFailed；非主线程序列中死亡跳过 | test_attach_failure_rolls_back | TestSeize |
| T4 | stop_all：INTERRUPT 每线程 + 收集停止（死亡线程容忍） | test_seize_all_threads | TestSeize |
| T5 | wait_event：syscall-stop → SyscallStop | test_entry_exit_pairing_emits_event（引擎侧） | TestWaitEvent |
| T6 | group-stop（PTRACE_EVENT_STOP）吞咽不转发 | test_group_stop_swallowed | TestWaitEvent |
| T7 | 真实信号经 restart_op data 转发 | test_real_signal_forwarded | TestWaitEvent |
| T8 | 新线程 SIGSTOP delivery-stop 吞咽并注册 tid | test_sigstop_new_thread_swallowed | TestWaitEvent |
| T9 | SIGTRAP 非事件陷阱转发 | （旧代码 L262-264） | TestWaitEvent |
| T10 | PTRACE_EVENT_EXEC → ExecEvent 返回（消费方处理后 resume） | （旧 `_handle_exec` 引擎侧） | TestWaitEvent |
| T11 | PTRACE_EVENT_CLONE 父进程直接续行，不产事件 | （旧代码 L256-258） | TestWaitEvent |
| T12 | ECHILD → None；InterruptedError 继续等；Exited **立即返回**并注销 tid（逐停处理语义同旧树 run 循环；机械停在一次 wait_event 调用内吞咽，SyscallStop/ExecEvent/Exited 均一停一返，消费方跨调用推进序列） | test_main_thread_exit_ends_loop | TestWaitEvent |
| T13 | **A7 琐碎项**：删除阻塞 waitpid 的 `wpid == 0` 死分支 | （旧 §9 P1-9） | （无测试——代码不存在，结构保证） |
| T14 | detach：INTERRUPT + 有界 WNOHANG 收集 + DETACH 每线程；幂等；tids 清空 | test_detach_interrupts_and_detaches_all | TestDetach |
| T15 | getregs 失败 → None | （旧 `_getregs`） | TestGetregs |
| T16 | **A7 P0-2 修复**：arch 检查下沉引擎 `__init__`，aarch64 → UnsupportedArchitecture（feature 名由消费方传入以保消息语义） | test_unsupported_arch | test_ptrace.py::test_unsupported_arch |
| T17 | UserRegs x86-64 布局 27 字段 + ARG_REGS=(rdi,rsi,rdx,r10,r8,r9) | （旧 `_UserRegs`/`_ARG_REGS`） | TestGetregs |

**errors.py**（旧 `errors.py` 全量——消息是 CLI `[!] {e}` 用户可见输出，逐字复刻）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| E1 | 9 子类全部继承 PyProbeError；属性同旧（pid/symbol/exe_path/version/fallback/feature/arch/supported） | test_errors.py 全量 | test_errors.py |
| E2 | 消息逐字复刻（含默认 detail 文案、SymbolNotFound 无路径省略 "in"、AttachFailed 无 detail 省略冒号） | test_message_and_attributes | test_errors.py（消息全 pin） |

**dto.py**（旧 `types.py` 数据部分；A5 剥离 format()/颜色/errno 表/路径缩短——后者批次 6 入 present/text.py）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| D1 | 7 个 dataclass 字段名/顺序/默认值同旧（asdict 键集 = JSON schema 复刻约束） | test_json_output.py 结构断言 | test_dto.py（键集 pin） |
| D2 | 无 format()/format_header() 方法；不 import 颜色（A5 守护） | （A5 新增） | test_dto.py + test_structure.py |

#### 批 1 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_memory.py | 已覆盖（拆为 test_mem.py + test_views.py） | — |
| tests/test_errors.py | 已覆盖（test_errors.py，消息 pin 加强） | — |
| tests/test_syscall_tracer.py | 引擎部分已覆盖（test_ptrace.py）；entry/exit 配对、过滤、渲染属批次 5 | 配对/渲染非引擎职责，A2 分层后归 observe/syscalls.py |
| tests/test_types.py | 数据部分已覆盖（test_dto.py）；format() 渲染属批次 6 | format() 被 A5 废除，渲染迁 present/text.py |
| tests/test_procmeta.py | 已覆盖（test_procfs.py）；decode_py_version 属批次 2（target/identity） | 版本解码是 CPython 布局知识，非内核接口 |

### 批次 2 — `target/`

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `target/identity.py` | CPython 进程识别与版本判定 | `exe_path_of(pid) -> str`；`detect_version(exe_path) -> str`；`decode_py_version(hexval) -> str` | 见 §5.1 批 2 表 |
| `target/layout.py` | A1：resolve_layout / Layout 值对象 / dev override | `Layout(version, key, verified, table)`（`.get`/`.get_or`）；`resolve_layout(version_str, *, overrides_path=None) -> Layout`；`supported_versions()`；`DEFAULT_VERSION` | 见 §5.1 批 2 表 |
| `target/symbols.py` | ELF 符号与运行时结构定位 | `find_symbol(exe_path, name, pid) -> int`；`read_const(exe_path, name, length) -> bytes \| None`；`ElfImage.open(path)`（`.lookup`/`.read_at_symbol`/`.e_type`） | 见 §5.1 批 2 表 |
| `kernel/procfs.py`（增补） | maps 行 → 加载基址（ELF 重定位所需 /proc 知识） | `parse_load_base(maps_text, exe_path) -> int`；`load_base(pid, exe_path) -> int` | P5（见批 2 表） |

#### 批 2 行为点清单（§5.1 实例）

**target/layout.py**（旧 `offsets.py`；A1 核心：消灭 `_active` 全局单例与 `get()` 读路径副作用。契约变更记录：旧 `configure()` 对未验证版本"先填 fallback 后抛 VersionNotSupported"，新 API 不抛——`Layout.verified=False` 标记，警告串由批次 4 session 用 `VersionNotSupported` 消息组装（消息文本复刻已由 errors.py E2 钉住））

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| L1 | 已验证版本（"3.12.13" 全串与 "3.12" 短串）→ verified=True，table 为该版本表 | test_known_version_sets_active | test_layout.py |
| L2 | version_key 提取（`_version_key` 私有函数直钉）："3.12.13"→"3.12"、"3.11"→"3.11"、"3"→"3"、""→""；**Layout.key 恒为 backing 表的版本**——verified 取其键、fallback 取 DEFAULT_VERSION、override 命中取 override 版本（无 minor 段的输入不原样保留，防误导诊断） | TestVersionKey | 同 |
| L3 | 未验证版本 → DEFAULT_VERSION 表、verified=False、requested 版本保留；**不抛异常**（A1 契约变更，见上）；override 命中未验证版本时 verified 仍为 False（旧"raise 照发"语义） | test_unknown_version_raises_and_falls_back（语义改写） | 同 |
| L4 | dev override：`_version` 匹配 → 整表替换（对 verified 与 fallback 基座同规则）；不匹配 → 忽略；`overrides_path` 显式传参 | TestDevOverride 三条 | 同 |
| L5 | get 缺键 → KeyError；get_or 返回 default | test_keyerror_for_missing_key | 同 |
| L6 | supported_versions 升序、含 3.11–3.14 | TestSupportedVersions | 同 |
| L7 | DEFAULT_VERSION == "3.12" | test_default_version | 同 |
| L8 | 表键集独立 oracle（_SHARED_KEYS + _VERSION_EXTRA_KEYS；防表回归而非自指确认；TDD 流程注释保留） | TestOffsetsTable 全量 | 同 |
| L9 | spec-oracle：内置表[V] == 入库 offsets.json（去 `_version`）当 json._version==V（vs gen_offsets 真实头文件产物，§10.2-1） | （新增，§10.2-1） | 同 |
| L10 | Layout 表不可变（MappingProxy；旧 test_active_is_copy 的"防污染验证表"语义由不可变性彻底保证） | test_active_is_copy | 同 |
| L11 | 多版本 Layout 并存互不干扰（A1 核心收益：per-session 布局，可同时观测不同版本进程） | （旧全局态下不可能，新增守护） | 同 |
| L12 | override 隔离：默认 overrides_path 可被 conftest 指向不存在路径（§10.2-7 落地——套件验证内置表而非 tracked json） | （§10.2-7，新增） | conftest  autouse fixture + 守护测试 |

**target/identity.py**（旧 `procmeta.decode_py_version` + `process.resolve_process` 的 exe/版本段）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| I1 | decode_py_version 位提取（major 24-31 / minor 16-23 / micro 8-15，release level 丢弃） | TestDecodePyVersion 三条 | test_identity.py |
| I2 | spec-oracle：运行中解释器 sys.version_info 重组 hexval 解码回同串 | test_known_py_version | 同 |
| I3 | exe_path_of：readlink /proc/pid/exe；OSError → ProcessNotFound(pid)（from 链保留） | （旧 process.py L73-76） | 同 |
| I4 | detect_version：read_const Py_Version 8 字节小端解码；读不到 → "?" 哨兵（ProcessInfo.python_version 默认值语义来源） | （旧 process.py L82-85） | 同 |

**target/symbols.py**（旧 `elf.py` 全量）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| S1 | ELF 校验：非 ELF → ValueError("not an ELF file")；非 ELF64 → ValueError("not ELF64") | （旧 _read_ehdr） | test_symbols.py（合成 ELF） |
| S2 | ehdr 字段定位：e_type@16 / e_shoff@40 / e_shnum@60 / e_shstrndx@62 | （旧 _read_ehdr） | 同 |
| S3 | 符号搜索序：.symtab（SHT_SYMTAB=2）先于 .dynsym（SHT_DYNSYM=11） | （旧 find_symbol L114） | 同 |
| S4 | 符号类型过滤：仅 STT_NOTYPE(0)/STT_OBJECT(1) 参与匹配 | （旧 _search_symbols L97-98） | 同 |
| S5 | ET_DYN(3) → st_value + load_base（/proc/pid/maps 首个匹配映射，含 " (deleted)" 后缀）；ET_EXEC 不加 | （旧 find_symbol L110-111 + _get_load_base） | 同 |
| S6 | 找不到 → find_symbol 返回 0 | test_find_missing_symbol | 同 |
| S7 | read_const：file_off = st_value − sh_addr + sh_offset；**以节剩余字节（sh_size）为界**，越界/短读/缺符号 → None（批次 2 验收澄清：旧树仅 EOF 界，节后会读到节头垃圾——新语义更严且消费方 Py_Version 8 字节不受影响） | test_read_missing_const | 同 |
| S8 | 解析缓存：(path, mtime) 键，二次调用不重读文件 | （旧 _elf_cache） | 同 |
| S9 | spec-oracle：真实解释器 _PyRuntime 非 0；Py_Version 8 字节解码 == platform 版本 | TestFindSymbolRealPython / TestReadConstRealPython | 同 |

**kernel/procfs.py 增补**（旧 `elf._get_load_base` 的 /proc 侧——maps 行解析属 procfs 职责）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| P5 | parse_load_base：取**首个** path 匹配行（含 " (deleted)" 后缀）的起始地址 hex 解码；无匹配 → 0；load_base 包装 /proc/pid/maps 读取，OSError → 0；**字面匹配，调用方负责提供内核解析后的路径**（生产路径来自 /proc/pid/exe readlink，天然已解析；批次 2 验收驳回 realpath 内建——路径解析策略不入 kernel 层） | （旧 _get_load_base L143-156） | test_procfs.py 增补 + 真实 /proc/self/maps spec 测试 |

#### 批 2 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_offsets.py | 已覆盖（test_layout.py）；`configure`/`get`/`get_or` 模块函数与 `_active` 复位测试不移植 | 全局单例即 A1 要消灭的对象；L3 语义改写（不抛异常）已入契约 |
| tests/test_elf.py | 已覆盖（test_symbols.py，另增合成 ELF 单测——旧树只有真实二进制 spec 测试，解析逻辑无单元级守护） | — |
| tests/test_procmeta.py | decode_py_version 已覆盖（test_identity.py I1/I2）；read_cmdline 批次 1 已覆盖（test_procfs.py P1） | — |

### 批次 3 — `cpython/`

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `cpython/objects.py` | PyLong/Unicode/Bytes typed reader | `read_long(view, layout, addr) -> int \| None`；`read_bytes(...) -> bytes \| None`；`read_unicode(...) -> str \| None`；`MAX_STR_LEN` | 见 §5.1 批 3 表 |
| `cpython/dicts.py` | 字典遍历（combined/unicode/split/managed） | `DictReader(view, layout)`（`.from_dict`/`.from_managed_values`/`.next`） | 同上 |
| `cpython/code.py` | Code 对象头与 linetable | `read_code_header(view, layout, code_addr) -> CodeHeader \| None`；`addr2line(view, layout, code_addr, lasti, firstlineno) -> int`；纯函数 `line_for_offset(linetable, lasti, firstlineno) -> int` | 同上 |
| `cpython/frames.py` | 帧链遍历 | `walk_frames(view, layout, frame_addr, trampoline_addr) -> list[dto.FrameInfo]`；`MAX_FRAMES` | 同上 |
| `cpython/runtime.py` | 解释器/线程图遍历 | `resolve_interpreter(view, layout, runtime_addr) -> int`；`resolve_trampoline(view, layout, interp_addr) -> int`；`read_thread_chain(view, layout, interp_addr) -> list[ThreadStateRef]`；`current_frame_of(view, layout, tstate_addr) -> int`；`MAX_THREADS` | 同上 |
| `cpython/names.py` | 线程名（threading._active 遍历） | `get_thread_names(view, layout, interp_addr) -> dict[int, str]` | 同上 |

A3 约束全批适用：**遍历代码零裸偏移运算**——所有偏移经 `layout.get/get_or` 在 typed
reader 内部消费；跨度装配（如 CodeObject 头、ThreadState 字段簇的单次合并读）归对应
模块内部实现，对外只暴露类型化字段。版本差异只住 Layout（`get_or` 分支探针）。

#### 批 3 行为点清单（§5.1 实例）

**cpython/objects.py**（旧 `pyobject.py`；MAX_STR_LEN=1<<20 从旧 memory.py 迁入——它是
远程对象尺寸的合理性上界，属对象层策略）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| O1 | read_long 3.12+：lv_tag 高位包 digit 数（size=tag>>3）；tag 读失败 → None | test_zero/single/two_digit | test_objects.py（版本矩阵 3.12–3.14） |
| O2 | read_long 3.11：ob_size 为普通计数（get_or 回退链 long_value.ob_digit→ob_digit；lv_tag 缺席走此分支） | （旧 L36-39） | 同（3.11） |
| O3 | size==0 → 0；size==1 → 单 digit（digit 读失败传播 None——旧 L44-45 语义） | test_zero/test_single_digit | 同 |
| O4 | size==2 → d0 \| d1<<30；任一 digit 失败 → None | test_two_digit/test_two_digit_large | 同 |
| O5 | size>2 → None（超出支持范围，不猜值） | test_too_large_returns_none | 同 |
| O6 | read_bytes：ob_size 上界 MAX_STR_LEN；数据读失败 → None；**A7 琐碎项落地：删除旧 `size < 0` 死检查**（read_u64 无符号） | test_pybytes 全量 | 同 |
| O7 | read_unicode：ASCII 头短读 → None；length<0 或 >MAX_STR_LEN → None | test_unreadable/oversize | 同 |
| O8 | state 字节位布局：compact=bit5、is_ascii=bit6、kind=bits2-4 | （旧 L78-81） | 同（各变体覆盖） |
| O9 | compact+ascii：数据 addr+ascii_sz，ascii/replace 解码 | test_ascii_compact 三条 | 同 |
| O10 | compact 非 ascii：数据 addr+compact_sz；kind→(latin-1,1)/(utf-16-le,2)/(utf-32-le,4)；未知 kind → None | test_utf16_compact | 同（kind 矩阵） |
| O11 | 非 compact：data_any 指针；NULL → None；按 kind 解码 | test_non_compact/null_data_ptr | 同 |

**cpython/dicts.py**（旧 `dict_iter.py`；`DictReader(view, layout)` 绑定布局）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| DI1 | from_dict：ma_keys==0 → False；dict 头不可读 → False | test_null_keys/unreadable_dict | test_dicts.py |
| DI2 | keys 头解析：dk_log2_index_bytes/dk_kind/dk_nentries；kind==0 → KeyEntry(24B, key_off=8)，否则 UnicodeEntry(16B, key_off=0) | TestFromDictCombined/Unicode | 同（版本矩阵） |
| DI3 | entries 基址 = keys_addr + (1<<dk_log2) + dictkeysobject_size；整块预读（0<total≤MAX_STR_LEN，否则 b""；读失败 → b""） | （旧 L39-48） | 同 |
| DI4 | next：跳过 k==0 空洞；split（values≠0）时 v=read_ptr(values+idx*8)，失败跳过；耗尽 → None | test_skip_holes/test_split_values | 同 |
| DI5 | from_managed_values：ht_cached_keys 读失败/为 0 → False；成功路径 values 来自独立数组 | test_from_managed_values | 同 |

**cpython/code.py**（旧 `linetable.py` + stack_dump 内 CodeObject 头读取；纯/侧效分离是 spec-oracle 前提）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| C1 | 纯函数 line_for_offset：PEP 626 解码——首字节 bit7 为条目标记；code=(b>>3)&15；15→无行号（ar_line=-1）；13/14→CPython signed varint（6 位块、bit6 续位；奇 uval 取 -(uval>>1)、偶取 uval>>1，**非 zigzag**——scan_signed_varint 权威语义，批次 3 验收经 C7 oracle 实证修正措辞）；10→+0、11→+1、12→+2；ar_end += ((b&7)+1)*2；续行字节（bit7==0）跳过 | TestAddr2Line 全量 | test_code.py |
| C2 | lasti 超出表末 → 最后计算行（循环 ar_end<=lasti 终止语义） | test_lasti_beyond_table | 同 |
| C3 | 无行号条目/空表 → firstlineno 回退 | test_all_no_line/no_line_entry/empty | 同 |
| C4 | addr2line 包装：co_linetable 指针或 bytes 读失败 → firstlineno | test_no_line_table/unreadable | 同 |
| C5 | read_code_header：co_firstlineno/co_filename/co_name 跨度合并读归本模块（A3：frames 不见 co_lo/co_hi 心算）；读失败 → None | （旧 stack_dump L58-94） | 同 |
| C6 | lasti = prev_instr − (code+co_code_adaptive)，负值钳 0（旧 stack_dump L100-102；配合 C1 对 lasti=0 的定义行为） | test_negative_lasti_treated_as_zero | 同 |
| C7 | **spec-oracle**：line_for_offset vs CPython `co_lines()` 地面真值——真实函数集（多行/无行号事件/嵌套）全指令偏移逐点一致（§10.2-1） | （新增，2026-10 审查验证过的方法） | 同 |

**cpython/frames.py**（旧 `stack_dump.collect_frames`；返回 dto.FrameInfo）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| F1 | 帧字段簇单次合并读（f_code/previous/prev_instr，帧宽=prev_instr_off+8 由 layout 导出）；不可读 → 终止 | test_unreadable_frame | test_frames.py（版本矩阵） |
| F2 | frame_addr==0 终止；MAX_FRAMES=100 上限（环形链防护） | test_null_frame/circular_chain | 同 |
| F3 | f_code==0 → 跳过续走 previous | test_skip_null_code | 同 |
| F4 | trampoline_addr≠0 且 f_code==trampoline → 跳过；trampoline==0 不跳过 | test_skip_trampoline/zero | 同 |
| F5 | code 头读失败 → 终止（截断已收集帧） | （旧 L82-84） | 同 |
| F6 | name/filename 地址为 0 → None；两者皆 None → 陈旧尾帧终止（3.13 datastack 残留教训，注释保留） | test_stale_tail_frame_filtered/none_name | 同 |
| F7 | 返回 FrameInfo 列表，内层在前 | test_single/two_frame_chain | 同 |
| F8 | 行号经 code.addr2line（lasti 钳位 C6） | （旧 L104） | 同（含行号断言） |

**cpython/runtime.py**（旧 `stack_dump.read_thread_chain`/`collect_thread` 的帧指针解析段 + `process.resolve_process` 的解释器/蹦床段）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| R1 | threads.head 读失败 → NoThreadState("failed to read threads.head")（消息复刻） | （旧 L214-215） | test_runtime.py |
| R2 | 链走：tstate 字段簇合并读（ts_lo..ts_hi 由 layout 导出）；不可读 → 截断返回已收集 | （旧 L224-227） | 同 |
| R3 | ThreadStateRef(tstate_addr, thread_id, native_tid)；MAX_THREADS=256 上限；next==0 终止 | （旧 L224-245） | 同（含上限） |
| R4 | current_frame_of：layout 探针 ThreadState.current_frame 直达（3.13/3.14）vs cframe→CFrame.current_frame 间接（3.11/3.12）；cframe==0 → 0 | test_null_cframe/test_build_threadinfo | 同（版本分支成对） |
| R5 | resolve_interpreter：main→head 回退；均失败 → NoInterpreterState() | （旧 process.py L96-104） | 同 |
| R6 | resolve_trampoline：get_or 缺席 → 0；读失败 → 0（3.12 专有键） | （旧 process.py L106-113） | 同（3.12 有值/3.13 为 0 成对） |

**cpython/names.py**（旧 `thread_names.py` 全量；任何失败 → 部分/空 dict，不抛）

| # | 行为点 | 旧测试出处 | 新测试 |
|---|--------|------------|--------|
| N1 | sys.modules 定位：3.12 走 InterpreterState.imports+_import_state.modules 直针；其余版本走 sysdict 字典找 "modules" 键 | （旧 L36-46 + _find_sys_modules） | test_names.py（版本分支成对） |
| N2 | modules 字典找 "threading" 模块；找不到 → 空 dict | （旧 L48-59） | 同 |
| N3 | 模块 __dict__：ob_type→tp_dictoffset→value+dictoffset；dictoffset==0/读失败 → 空 | （旧 L61-70） | 同 |
| N4 | "_active" 字典：键 read_long → tid；读失败跳过 | （旧 L85-95） | 同 |
| N5 | 实例字典解析（MANAGED_DICT 分支矩阵）：3.11 obj-3 dict 槽 / obj+pre_values 非标记 values；3.12 tagged&1 内联 values；3.13/3.14 tagged==0 → 对象内嵌（PyObject_size+dictvalues_header）；3.12 tagged==0 → False（dictvalues_header=0）；非 managed → tp_dictoffset | （旧 _get_instance_dict_iter 全量） | 同（3.11/3.12/3.13 三分支 + 非 managed） |
| N6 | "_name" 属性 read_unicode → names[tid]；None 跳过 | （旧 L107-113） | 同 |

#### 批 3 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_pyobject.py | 已覆盖（test_objects.py；O6 死检查删除入契约） | — |
| tests/test_dict_iter.py | 已覆盖（test_dicts.py，构造器改 Layout 驱动） | — |
| tests/test_linetable.py | 已覆盖（test_code.py；另增 C7 spec-oracle——旧树只有手工构造 linetable，无 CPython 地面真值对照） | — |
| tests/test_stack_dump.py | 帧/线程链部分已覆盖（test_frames/test_runtime）；idle 启发式、collect_thread、format/CLI 部分属批次 4/6 | 分层后职责在 observe/present |
| tests/test_thread_names.py | （不存在——旧树 names 仅集成测试覆盖） | 批次 3 新增单元级覆盖（test_names.py），集成验证批次 4 端到端 |

### 批次 4 — `observe/`（首个端到端里程碑：snapshot 对 live target 出栈）

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `observe/session.py` | 观测会话（绑定 target + view 一致性声明） | TBD | TBD |
| `observe/snapshot.py` | 单次快照栈采集 | TBD | TBD |
| `observe/sampling.py` | 采样器 | TBD | TBD |
| `observe/profile.py` | record 折叠栈 | TBD | TBD |
| `observe/topstats.py` | top 统计（A7 idle 修复） | TBD | TBD |

### 批次 5 — `observe/`（ptrace 消费方）

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `observe/syscalls.py` | syscall 追踪与渲染数据（A7 syscall 修复） | TBD | TBD |
| `observe/native.py` | native 回溯（libdw/libdwfl） | TBD | TBD |

### 批次 6 — `present/` + `cli` + 打包

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `present/text.py` | 全部文本格式化（A5 落地） | TBD | TBD |
| `present/jsonout.py` | JSON 输出（schema 复刻约束） | TBD | TBD |
| `present/color.py` | 颜色策略（A6 保留） | TBD | TBD |
| `cli.py` | argparse 薄壳 | TBD | TBD |

## 5. 行为点清单机制（§10.3 落地模板）

每批设计契约时，主代理对旧实现对应模块逐项过行为点，缺项即契约缺口。

### 5.1 行为点清单表（每批一张）

| 旧模块 | 行为点 | 旧测试出处 | 新契约条目 | 新测试 | 状态 |
|--------|--------|------------|------------|--------|------|
| （例）syscall_render.py | `-e trace=file,!openat` 排除语义 | （旧 §9 P0-3，无旧测试） | syscalls 契约 §x | test_syscalls.py::test_mixed_filter | 已覆盖 |

### 5.2 旧测试逐文件标注（替换前 100% 过一遍）

| 旧测试文件 | 覆盖判定 | 放弃理由（入 ADR） |
|------------|----------|--------------------|
| tests/test_linetable.py | 已覆盖（spec-oracle：vs `co_lines()`） | — |

### 5.3 测试数据纪律

权威来源 + 出处注释（旧 `_AT_FDCWD` 自指常量教训）、可辨识哨兵值（旧 `thread_id=0`
默认值掩蔽教训）、精确复现路径、边界与失败模式成对。

测试注释纪律（每条测试须注释目的/防护对象以证明必要性）：单一事实来源见
AGENTS.md「TDD 工作流」节；契约守护测试另须在注释中回链本文件条目编号（§10.5-5 验收项）。

## 6. 派发包模板（主代理 → 子代理）

> 子代理仅实现：禁改 tests/ docs/ 契约/ 脚本；契约或测试有误时**停手回报**，禁止就地
> 修测试迁就实现。主代理验收：diff 范围、全套件绿、lint、覆盖率、Red→Green 证据链、
> 文档同步。

```
批次：<N — 名称>
契约引文：next/docs/contracts.md §<锚点>（逐条引用，含 ADR 编号）
只读测试清单：next/tests/test_<x>.py（逐文件列出，含 Red 验证输出存档路径）
可写范围：next/pyprobe/<允许新建/修改的文件白名单>
验收标准：
  - diff 严格落在可写范围内
  - scripts/next.sh test 全绿、scripts/lint.sh 净、覆盖率棘轮不降
  - Red→Green 证据链：Red 输出（预期断言失败）→ 实现后 Green
  - 文档同步：contracts.md 对应条目填实
契约缺陷回报：发现契约/测试错误时停止实现，回报 <具体条目 + 复现路径>，等待主代理裁决
```
