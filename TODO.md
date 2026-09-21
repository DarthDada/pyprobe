# TODO

## 目录

1. [采样与性能分析](#1-采样与性能分析)
2. [CLI 易用性与集成](#2-cli-易用性与集成)
3. [版本与架构覆盖](#3-版本与架构覆盖)
4. [native 栈输出对齐 gdb](#4-native-栈输出对齐-gdb)
5. [syscall 对齐 strace](#5-syscall-对齐-strace)
6. [深度检查与差异化](#6-深度检查与差异化)
7. [工程基础设施](#7-工程基础设施)
8. [代码模块化与解耦](#8-代码模块化与解耦)

> **全局优先级**：功能（§1–§6）与基础设施（§7）两轨并行。上轮 P0（record/top + `--json`）已交付；当前 P0 = CPython 3.14（§3.2，时间敏感，以 §3.1 参数化为前置）+ §7 的 P0/P1（为 3.14 TDD 接入及后续开发提供快速反馈）；P1 = §2 剩余项（info / 按名称匹配 / syscall `--json`，低成本）；其余按章节内次序推进，§4 P0（生产环境可加载）作为已发布功能的健壮性问题可随需插入。

## 1. 采样与性能分析

> `stack` 是一次性快照，无法回答"时间花在哪"——这是与 py-spy 的本质差距。采样 = 循环调用 `collect_python`（复用 RemoteReader 页缓存 + 无 ptrace 读取路径），record 与 top 共享同一采样引擎。

- [x] 1. `record` 子命令：周期采样 + folded stacks 输出（2026-09 完成：Sampler 采样引擎 + folded 格式，见 design.md §15；speedscope/SVG 后续按需）
- [x] 2. `top` 子命令：持续采样 + 终端刷新，展示各线程当前帧/热点（2026-09 完成，见 design.md §15.3）

## 2. CLI 易用性与集成

> 库能力（`collect_*` 返回结构化 dataclass）与 CLI 能力对齐，打通 IDE/工具链/CI 程序化消费。`stack` 已支持 `--json`（§2.1）；`syscall` 的 `SyscallEvent` 已是 dataclass，扩展成本极低。

- [x] 1. `--json` 输出：`dataclasses.asdict` 序列化（2026-09 完成：`stack --json` / `--native --json`，见 design.md §15.5；golden file 测试范围见 §7.8）
- [ ] 2. `info` 子命令：解释器数、线程数、空闲/运行统计、`sys.argv`/`sys.executable`、GIL 状态等进程级概览，复用 `collect_python`
- [ ] 3. 按名称匹配目标进程：`-m/--match <name>`（扫 `/proc/*/cmdline`），省去先 `pgrep` 再传 `-p`；并可对"目标不是 CPython 进程"给出早期友好报错（目前要到符号查找失败才发现）
- [ ] 4. `--json` 扩展至 `syscall`：`SyscallEvent` 已含 errno 名/耗时字段，`dataclasses.asdict` 序列化即可，复用 §2.1 模式；record 的 folded 与 top 的 TUI 面向人/专用工具，暂不需要

## 3. 版本与架构覆盖

> 现状：偏移量已验证 3.11–3.13 仅 x86-64（design.md §10）；syscall 追踪仅 x86-64；未验证版本回退 3.12 偏移量并告警。

- [ ] 1. 多版本偏移量测试参数化：`TestSupportedVersions` 已参数化（tests/test_offsets.py:23），但 `TestOffsetsTable` 三处仍硬编码 `configure("3.12")`（:78/:84/:90，现有参数化仅 key 维度）；改为版本 × key 双维参数化覆盖 `_VERIFIED_OFFSETS` 全部版本（key fixture 分共享 key + 版本特有 key，如 3.11 `PyObject.pre_values`、3.13 `ThreadState.current_frame`），使新版本支持可 TDD 式开发
- [ ] 2. CPython 3.14 支持：时间敏感（已发布一年，用户迁移正在发生）；以前一条为前置，TDD 式接入：`scripts/gen_offsets.sh` 生成 → 先写 3.14 偏移量失败测试 → 验证后编入 `_VERIFIED_OFFSETS`；端到端可 `TARGET_PYTHON=python3.14` 走 conftest 集成 fixture（tests/conftest.py:43，无需改测试代码）
- [ ] 3. aarch64 偏移量实际验证：与 x86-64 理论相同（均为 64 位 LP64），未实测
- [ ] 4. aarch64 syscall 追踪：目前 `UnsupportedArchitecture`（syscall_table.py 仅 x86-64 表）；需 aarch64 syscall 号表 + 解码元数据 + 寄存器 ABI 适配（GETREGS 结构不同）

## 4. native 栈输出对齐 gdb

> 背景：`pyprobe stack --native` 与 gdb `thread apply all bt` 对比（2026-09 实测），已修复 `GElf_Word` 32 位截断致全 `??`（1fbb785）与线程降序两问题；以下为剩余差异的落地计划，全部基于现有技术栈（ctypes + libdw/libdwfl，无新依赖）。

### P0 — 生产环境可加载（前置条件）

- [ ] 1. libdw 加载名回退：`ctypes.CDLL` 依次尝试 `libdw.so.1` → `libdw.so`（或 `ctypes.util.find_library("dw")`），兼容 ldconfig 只注册其一的环境
- [ ] 2. 符号探测降级：加载后检查 `dwarf_getscopes` / `dwfl_module_getsrc` / `dwarf_cfi_addrframe` 等是否存在（最低需 elfutils 0.158，CFI 0.142+），缺失则标记对应特性不可用，保底符号名+回溯
- [ ] 3. 回溯失败标注对齐 gdb：`unwind_failed` 判定修正（native_dump.py:215 当前仅"无帧"才判失败，把 pc=0 单帧当成功，如 io_uring `iou-sqp-*` 线程应提示 `Backtrace stopped`）

### P1 — 展示层对齐（低成本高收益）

- [ ] 4. LLVM 后缀截断：`re.sub(r'\.llvm\.\d+$', '', name)` 对齐 gdb 短符号名（`run_mod.llvm`）
- [ ] 5. file:line：`dwfl_module_getsrc` + `dwarf_linesrc`/`dwarf_lineno`，输出 `at src/unix/linux.c:1432`（仅带 DWARF 的模块生效，如 uvloop；python 主程序无 `.debug_*` 与 gdb 持平）
- [ ] 6. 线程头格式：补 pthread 描述符地址（`Thread 1 (Thread 0x7f... (LWP 7101) "python3")`）——需评估获取方式（ptrace 读 pthread 结构 vs 保持现状仅 LWP）

### P2 — 完整 DWARF 解析（高成本）

- [ ] 7. 内联帧展开：`dwarf_getscopes` 作用域链上每个 `DW_TAG_inlined_subroutine` DIE 展开为逻辑帧，`DW_AT_abstract_origin` 回溯取函数名，`DW_AT_call_file`/`call_line` 取调用点
- [ ] 8. 参数名：subprogram DIE 遍历 `DW_TAG_formal_parameter` 取 `DW_AT_name`
- [ ] 9. 参数值（最难，可只做子集）：`dwarf_cfi_addrframe` + `dwarf_frame_register` 求 CFI，`dwarf_getlocation` 位置表达式解释器（`DW_OP_fbreg`/`DW_OP_regN`/`DW_OP_addr` 等），远程读内存复用 `RemoteReader`；`@entry` 依赖 `DW_AT_call_site`/GNU 扩展，视成本取舍

## 5. syscall 对齐 strace

> 现状：引擎已支持 `-e trace=` 过滤、`--summary`、`-v`、errno 名解码（`= -1 ENOENT (...)`）与 elapsed 计时（`<0.000123>`）；以下为与 strace 的剩余差距。

- [ ] 1. 启动模式 `pyprobe syscall -- CMD`：strace 支持从启动追踪；当前须先起进程再抢 PID，漏掉启动初期（如模块导入期）的系统调用。ptrace 引擎已在手，缺 fork/exec 入口
- [ ] 2. TRACEFORK 跟随子进程：当前仅 TRACECLONE（线程），不跟随 `fork()` 出的子进程
- [ ] 3. 输出细节：`-y` fd 解码（fd→路径，读 `/proc/<pid>/fd`）、`-e read=N/write=N`、`-e errno=`、`-tt` 时间戳列

## 6. 深度检查与差异化

> 高成本方向：混合栈补齐 py-spy `--native` 的语义，帧局部变量则超越 py-spy/gdb 现有能力。

- [ ] 1. Python 帧局部变量/参数读取：遍历 `_PyInterpreterFrame.localsplus`，dump 时显示调用参数。py-spy 无此能力，是真正差异化功能，但需完整 object graph 解析
- [ ] 2. Python/native 混合栈：py-spy `--native` 会把 Python 帧内联进 native 栈；当前两种栈割裂，`--native` 输出对 Python 用户语义有限

## 7. 工程基础设施

> 现状：480 个测试（含参数化展开）+ FakeReader/对象构造器 + `target_pid`/`spin_pid` fixture（支持 `TARGET_PYTHON` 跨版本端到端）已具备；以下为按红→绿→重构循环衡量的缺口。TDD 是流程约束，靠自觉必退化。

### P0 — 支撑红绿循环本身

- [ ] 1. 引入 `pytest-cov`：加入 dev 依赖组；`scripts/run_tests.sh` 支持 `--cov` 透传或新增 coverage 阶段；设定 `--cov-fail-under` 基线防覆盖回退
- [ ] 2. watch 模式：`scripts/run_tests.sh watch` 子命令，复用 venv 中已有的 `watchfiles`，实现保存即重跑（秒级反馈）
- [ ] 3. pytest 严格化：`pyproject.toml` 增加 `addopts = ["-ra", "--strict-markers", "--strict-config"]` 与 `filterwarnings = ["error"]`；同步 `scripts/run_tests.sh`

### P1 — 流程纪律强制

- [ ] 4. 远程 CI：新增 `.github/workflows/ci.yml`，仅调用 `scripts/ci.sh`（与本地一致），强制"提交必须全绿"
- [ ] 5. git hooks：pre-commit/pre-push 跑单元测试（无裸命令，走 `scripts/run_tests.sh unit`）
- [ ] 6. AGENTS.md 增补 TDD 工作流约束条目（先写失败测试、red 阶段验证、测试与实现同提交）
- [ ] 7. design.md §13 增补测试架构对应变更（§13.1/13.2 已含 record/top/`--json` 测试架构；覆盖率目标、watch 模式、CI 阶段待补）

### P2 — "绿"的质量与重构安全网

- [ ] 8. golden file 测试：`format_process` / CLI 文本与 `--json` 输出的格式快照基线（tests/test_json_output.py 现为结构断言，非快照），保障格式化重构安全
- [ ] 9. 变异测试（mutmut）：解析二进制内存布局的代码测试"看似覆盖但抓不住错位偏移"风险高，用变异测试验证测试有效性
- [ ] 10. 引入 lint/typecheck（当前 AGENTS.md 明确"无"）：重构安全网 = 测试 + 静态检查，二者缺一
- [ ] 11. 属性测试（hypothesis）：`linetable` / `dict_iter` / `pyobject` 解析器代码的典型受益者

## 8. 代码模块化与解耦

> 2026-09 依赖审计结论：20 模块依赖图为干净 DAG（无循环），collect/format/dump 三层分离整体健康；问题集中在局部——初始化逻辑复制、跨模块私有访问、全局可变状态、少数模块职责发散。以下按收益/成本排序；P1/P2 建议在 §7.8 golden file 与 §7.10 lint 就绪后实施（重构安全网）；涉及模块增删或 API 转正时同步 design.md §2 与 `__init__.py` `__all__`。

### P1 — 消除重复与跨模块私有访问（收益最大）

- [ ] 1. 提取共享进程初始化：`stack_dump.collect_python`（stack_dump.py:253-304）与 `Sampler.__init__`（sampler.py:52-97）约 40 行逐字重复（exe readlink → find_symbol → read_const → offsets.configure → interp_addr 解析（main→head 回退）→ trampoline → get_thread_names），且复制后已**行为漂移**——目标版本无法判定时 stack 路径打 stderr 警告（stack_dump.py:271-274）而 record/top 路径静默回退（sampler.py:66-69）。提取 `ProcessSession` / `resolve_process(pid)` 共用，`collect_python` 可实现为一次性 Sampler + 单次 sample；版本告警策略随提取统一（与第 5 条联动）
- [ ] 2. 转正事实公开 API：`sampler.py:26-28` 跨模块 import `stack_dump` 的 `_read_thread_chain` / `_is_thread_idle_by_stat`（采样热路径核心步骤）；`sampler.py:69` / `stack_dump.py:270` 跨模块读 `offsets._DEFAULT_VERSION`；tests/test_sampler.py:195 monkeypatch 的是 sampler 命名空间的再导出副本，补丁语义脆弱。去下划线转正并纳入公共 API 面

### P2 — 模块边界清理（机械操作，低风险）

- [ ] 3. 拆分 `syscall_trace.py`（682 行三合一：字符串渲染助手 + ptrace 引擎 + 公共 API）：syscall_trace.py:338-345 文件中部 import 是两文件拼接痕迹，syscall_trace.py:452 函数内延迟 import `memory`（两者间无循环依赖，延迟无必要）。拆为 `syscall_render.py`（纯函数，reader 注入）与 `syscall_tracer.py`（SyscallTracer 引擎），import 统一上移至文件头
- [ ] 4. 幽灵 import 清理：elf.py:7 / dict_iter.py:5 / pyobject.py:5 / thread_names.py:3 的 `RemoteReader`、stack_dump.py:22 的 `MAX_STR_LEN` 均未使用，在依赖图上制造虚假边
- [ ] 5. 死异常与库层打印收敛：`PermissionDenied` / `VersionNotSupported` 已定义并导出但全库无 raise 点；offsets.py:197-201（configure 内）与 stack_dump.py:271-274（collect 层）直接 print stderr，违反 collect 层"不打印"契约（stack_dump.py:5-7 docstring 自述）。要么用起来（未验证版本改 raise 或 collect 层返回告警、由 dump 层统一输出），要么删除
- [ ] 6. `elf.py` 职责收敛：`read_cmdline`（elf.py:156）/ `decode_py_version`（elf.py:165）与 ELF 解析无关，导致 stack_dump / sampler / native_dump 为读 cmdline 依赖"ELF 模块"；移入独立 proc 元数据模块
- [ ] 7. `cli.py:24` `from . import __version__` 反向依赖包根，import `pyprobe.cli` 即触发 `__init__.py` 全量加载；版本号下沉独立模块或改 `importlib.metadata`

### P3 — 高成本重构（独立 PR，需安全网护航）

- [ ] 8. `offsets` 去 global 化：`_active` 模块级可变单例（offsets.py:177），`get()` 未 configure 时隐式触发 configure（offsets.py:214-217，读路径带副作用）；71 处调用分布于 6 模块（stack_dump 30 / thread_names 12 / pyobject 11 / dict_iter 10 / sampler 7 / linetable 1）。改为 per-session 偏移量表对象随 reader/session 传递后：可同时探测不同 CPython 版本的进程、消除 tests/test_offsets.py:47 的手工复位。改动面大（59 处 `get`），以 §7.10 lint + §7.1 覆盖率基线为前置
- [ ] 9. `types.py` 数据层依赖 `colors` 表现层（types.py:16）：5 个 dataclass 的 `format()` 内嵌 ANSI 着色，与模块 docstring"plain data objects"定位冲突。实际影响小（JSON/`asdict` 路径已绕开），可选：format 方法移表现层，或收窄 docstring 接受现状
