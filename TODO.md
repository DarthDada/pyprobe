# TODO

## 目录

2. [CLI 易用性与集成](#2-cli-易用性与集成)
3. [版本与架构覆盖](#3-版本与架构覆盖)
4. [native 栈输出对齐 gdb](#4-native-栈输出对齐-gdb)
5. [syscall 对齐 strace](#5-syscall-对齐-strace)
6. [深度检查与差异化](#6-深度检查与差异化)
7. [工程基础设施](#7-工程基础设施)
8. [代码模块化与解耦](#8-代码模块化与解耦)
9. [代码审查遗留（2026-10）](#9-代码审查遗留2026-10)
10. [第一性原理重构（next/ 重建 + 原子替换）](#10-第一性原理重构next-重建--原子替换)

> **全局优先级**：**当前主轨 = §10 第一性原理重构**（批次 0→7 顺序推进；旧树冻结，§2/§4/§5/§6 新功能暂停至替换完成）。§9 剩余 bug 随 §10 ADR A7 落地，旧树不单独修；§8.1 并入 §10 ADR A1；§7 剩余项中 git hooks 与 CI 版本矩阵可随 §10 批次顺带落地；aarch64（§3）待实际环境。

## 2. CLI 易用性与集成

> 库能力（`collect_*` 返回结构化 dataclass）与 CLI 能力对齐，打通 IDE/工具链/CI 程序化消费。`stack` 与 `syscall` 均已支持 `--json`。

- [ ] 1. `info` 子命令：解释器数、线程数、空闲/运行统计、`sys.argv`/`sys.executable`、GIL 状态等进程级概览，复用 `collect_python`
- [ ] 2. 按名称匹配目标进程：`-m/--match <name>`（扫 `/proc/*/cmdline`），省去先 `pgrep` 再传 `-p`；并可对"目标不是 CPython 进程"给出早期友好报错（目前要到符号查找失败才发现）

## 3. 版本与架构覆盖

> 现状：偏移量已验证 3.11–3.14 仅 x86-64（design.md §10）；syscall 追踪仅 x86-64；未验证版本回退 3.12 偏移量并告警。

- [ ] 1. aarch64 偏移量实际验证：与 x86-64 理论相同（均为 64 位 LP64），未实测
- [ ] 2. aarch64 syscall 追踪：目前 `UnsupportedArchitecture`（syscall_table.py 仅 x86-64 表）；需 aarch64 syscall 号表 + 解码元数据 + 寄存器 ABI 适配（GETREGS 结构不同）

## 4. native 栈输出对齐 gdb

> 背景：`pyprobe stack --native` 对齐 gdb `thread apply all bt`（2026-09 实测；`GElf_Word` 32 位截断与线程排序两问题已修）。以下为剩余差异的落地计划，全部基于现有技术栈（ctypes + libdw/libdwfl，无新依赖）。

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

> 现状：引擎已支持 `-e trace=` 过滤、`--summary`、`--json`、`-v`、errno 名解码（`= -1 ENOENT (...)`）与 elapsed 计时（`<0.000123>`）；以下为与 strace 的剩余差距。

- [ ] 1. 启动模式 `pyprobe syscall -- CMD`：strace 支持从启动追踪；当前须先起进程再抢 PID，漏掉启动初期（如模块导入期）的系统调用。ptrace 引擎已在手，缺 fork/exec 入口
- [ ] 2. TRACEFORK 跟随子进程：当前仅 TRACECLONE（线程），不跟随 `fork()` 出的子进程
- [ ] 3. 输出细节：`-y` fd 解码（fd→路径，读 `/proc/<pid>/fd`）、`-e read=N/write=N`、`-e errno=`、`-tt` 时间戳列

## 6. 深度检查与差异化

> 高成本方向：混合栈补齐 py-spy `--native` 的语义，帧局部变量则超越 py-spy/gdb 现有能力。

- [ ] 1. Python 帧局部变量/参数读取：遍历 `_PyInterpreterFrame.localsplus`，dump 时显示调用参数。py-spy 无此能力，是真正差异化功能，但需完整 object graph 解析
- [ ] 2. Python/native 混合栈：py-spy `--native` 会把 Python 帧内联进 native 栈；当前两种栈割裂，`--native` 输出对 Python 用户语义有限

## 7. 工程基础设施

> 现状：653 个测试（含参数化展开）+ FakeReader/对象构造器 + `target_pid`/`spin_pid` fixture（支持 `TARGET_PYTHON` 跨版本端到端）已具备；覆盖率基线（`scripts/run_tests.sh --cov`，CI test 阶段强制，79% fail-under）、远程 CI（`.github/workflows/ci.yml`，PR/push 触发、仅调用 `scripts/ci.sh`）、`slow` marker（真实采样/sleep 测试可经 `-m 'not slow'` 跳过）、watch 模式、pytest 严格化（`-ra`/`--strict-markers`/`--strict-config` + `filterwarnings = ["error"]`）、ruff lint（`scripts/lint.sh`）均已落地。以下为剩余缺口。TDD 是流程约束，靠自觉必退化。

### P1 — 流程纪律强制

- [ ] 2. git hooks：pre-commit/pre-push 跑单元测试（无裸命令，走 `scripts/run_tests.sh unit`）
- [x] 3. AGENTS.md 增补 TDD 工作流约束条目（先写失败测试、red 阶段验证、测试与实现同提交）——并入 §10 批次 0（双代理分工下"同提交"细化为"同验收批"）
- [ ] 4. design.md §13 增补测试架构对应变更（覆盖率目标、watch 模式、CI 阶段待补）

### P2 — "绿"的质量与重构安全网

- [ ] 5. golden file 测试：`format_process` / CLI 文本与 `--json` 输出的格式快照基线（tests/test_json_output.py 现为结构断言，非快照），保障格式化重构安全
- [ ] 6. 变异测试（mutmut）：解析二进制内存布局的代码测试"看似覆盖但抓不住错位偏移"风险高，用变异测试验证测试有效性
- [ ] 7. typecheck（mypy/pyright）：ctypes 重度使用 typecheck 噪音大待评估
- [ ] 8. 属性测试（hypothesis）：`linetable` / `dict_iter` / `pyobject` 解析器代码的典型受益者

## 8. 代码模块化与解耦

> 2026-09 依赖审计：20 模块依赖图为干净 DAG，collect/format/dump 三层分离健康；重复消除、模块边界清理与 types.py docstring 收窄均已落地。仅余 `offsets` 去 global 化（高成本重构，独立 PR，覆盖率基线与 lint 已交付作安全网）。涉及模块增删或 API 转正时同步 design.md §2 与 `__init__.py` `__all__`。

- [ ] 1. `offsets` 去 global 化：`_active` 模块级可变单例（offsets.py:177），`get()` 未 configure 时隐式触发 configure（offsets.py:214-217，读路径带副作用）；71 处调用分布于 6 模块（stack_dump 30 / thread_names 12 / pyobject 11 / dict_iter 10 / sampler 7 / linetable 1）。改为 per-session 偏移量表对象随 reader/session 传递后：可同时探测不同 CPython 版本的进程、消除 tests/test_offsets.py 的手工复位（`offsets._active = None`）。改动面大（59 处 `get`）。**并入 §10 ADR A1**——随 next/ 第一性原理重建落地，旧树不再单独执行此重构

## 9. 代码审查遗留（2026-10）

> 2026-10 全量审查（21 个模块 + 全部文档 + scripts/ + tests/）发现的代码 bug；同批文档不一致问题已全部修复归档。标注"实测"的条目均已派生子进程端到端复现（遵循 design.md §13.3 手动探测约束）；其余经代码阅读 + CPython 3.12/3.13 头文件与源码核实。**下列 P0/P1 项随 §10 ADR A7 在 next/ 重建中落地，旧树冻结期间不单独修复。**

### P0 — 用户正常使用即可撞上

- [ ] 1. `top` idle 线程统计错误（top.py:46-53）：idle 守卫误查 `self.current`（idle 线程永不入该 dict），同一 tid 每个采样周期重复 append 进 `idle_threads`（实测 3 次采样 → `[5, 5, 5]`，渲染 `Idle threads: 5, 5, 5`，50Hz 下无限膨胀刷屏）；且曾活跃线程转 idle 后既不进 idle 列表也不从 `current` 移除，带着陈旧帧留在 "Active threads" 表。修：守卫改查 `idle_threads` 去重，idle 时从 `current` 移除或标记；补"同一 idle 线程连续采样"与"active→idle 转换"守护测试（test_top.py:100 只 update 一次恰好掩盖）
- [ ] 2. syscall CLI 路径绕过架构检查（syscall_tracer.py:335-337 vs 359-365）：`platform.machine()` 检查只在 `collect_syscalls`，`dump_syscalls` 直接实例化 `SyscallTracer`，其 `except UnsupportedArchitecture` 成死代码。aarch64 上 CLI 会 attach 成功并用 x86-64 `_UserRegs` 布局读寄存器输出垃圾，而非 README 承诺的 `UnsupportedArchitecture` 报错。修：检查下沉 `SyscallTracer.__init__`/`attach()`（关联 §3.2）
- [ ] 3. `TraceFilter` 混合包含/排除语义反转（syscall_render.py:284-290，实测复现）：`-e trace=file,!openat` 中 openat 被追踪（应排除），`-e trace=read,!write` 中 write 被追踪——`!` 项被并入包含集。纯排除（`-e trace=!futex`）正确，测试只覆盖了纯排除分支。修：改 `include - exclude` 语义 + 补混合表达式测试（关联 §5）

### P1 — 边界路径与次要语义

- [ ] 4. `attach()` 重扫循环回滚缺口（syscall_tracer.py:166-170）：循环内 `_list_tids` 抛 `ProcessNotFound`（目标 attach 中途退出）不被 `except AttachFailed` 捕获，已 seize 线程不回滚；`self.tids` 第 182 行才赋值，此刻 `detach()` 为空操作。修：回滚改为捕获所有异常
- [ ] 5. `_handle_exec` 绕过用户过滤器（syscall_tracer.py:300）：补发 execve 事件用全新 `TraceFilter("")`（全匹配），`-e trace=!execve` 也输出。修：把 `flt` 传入
- [ ] 6. `timespec_out` 参数在 entry 时读取（syscall_render.py:202-203）：`clock_gettime` 输出参数读到的是调用前陈旧内容，`_fill_out_args` 只处理 `buf_in`/`buf_out` 从不重读（strace 在 exit 时读）。修：exit 阶段重读 `timespec_out`
- [ ] 7. `_fill_out_args` EOF 误读（syscall_render.py:234）：`ret == 0` 时按 32 字节读取并展示内核未写的缓冲区内容；同函数 `ret < 0` 分支为死代码（调用点保证 `ret >= 0`）。修：`ret == 0` 渲染空串
- [ ] 8. `TopStats.render` 无名顶帧 OWN% 恒 0（top.py:85 vs 57）：`update()` 把无名帧存 `"?"` 键，render 却用 `frame.name`（`None`）查询。修：render 侧同样 `or "?"` 归一
- [ ] 9. 琐碎项打包：`run()` 的 `wpid == 0` 死分支（阻塞式 waitpid 不返回 0，syscall_tracer.py:230）；`_list_tids` 捕获 `listdir` 不会抛的 `ProcessLookupError` 却漏 `PermissionError`（/proc 权限不足时用户看到 traceback 而非 `[!]` 错误，syscall_tracer.py:104-107）；`read_pybytes` 的 `size < 0` 死检查（pyobject.py:59，`read_u64` 无符号）；attach 时线程已在 syscall 内的相位判定可能产生一条伪事件（rax 中的 syscall 号被当返回值）——已知 ptrace 类问题，修复或记入 design.md §14.4 坑位清单

## 10. 第一性原理重构（next/ 重建 + 原子替换）

> 最大目的：**参考旧代码、从第一性原理重新设计代码架构与测试用例**。旧实现是知识来源与运行时 oracle，**不是移植来源**——不机械搬运旧代码与旧测试；行为对齐（§10.5 差异对比门禁）是替换的约束而非目标。三条铁律：**冻结旧树**（`pyprobe/`、`tests/` 不接受新功能；紧急修复由主代理以契约更新镜像进 next/）、**无新功能**（§2 `info` / `-m` 等替换后再做）、**ADR 纪律**（架构变更仅限 §10.1 清单，新想法必须先成 ADR 经主代理批准，防第二系统效应）。
>
> 第一性原理推导——pyprobe 的本质是"外部观测者问题"：仅凭 PID 与内核接口重建 CPython 运行时状态并呈现。不可约能力七项：字节传输 / 符号定位 / 布局知识 / 图遍历 / 动态控制 / 时间维度（快照·采样·事件流）/ 呈现。新架构按此分层，依赖方向唯一合法（横切层除外）：`kernel → target → cpython → observe → present → cli`。
>
> 作业方式（主/子代理 TDD）：主代理分析需求、维护架构与契约（高内聚低耦合、design.md 为 SSOT）、编写测试；严格**先契约后测试、先测试后实现**，禁止反向。循环：契约 → 测试 → Red 验证（失败原因须为预期断言，输出存档）→ 派发包（契约引文 + 只读测试清单 + 可写范围 + 验收标准 + 契约缺陷回报通道）→ 子代理仅实现（禁改 tests/docs/契约/脚本；契约或测试有误时停手回报，禁止就地修测试迁就实现）→ 主代理验收（diff 范围、全套件绿、lint、覆盖率、Red→Green 证据链、文档同步）。测试数据纪律：权威来源 + 出处注释（`_AT_FDCWD` 自指常量教训）、可辨识哨兵值（`thread_id=0` 默认值掩蔽教训）、精确复现路径、边界与失败模式成对。
>
> **"参考旧代码"的具体机制**（§10.3）：每批设计契约时，主代理把旧实现对应模块与旧测试的覆盖点列成"行为点清单"，新契约逐项确认覆盖或显式放弃（理由入 ADR）；旧测试文件逐文件标注"已覆盖/已放弃"，不重写不搬运。
>
> **决策已确认（2026-10）**：A1 Layout 值对象化纳入；A2 统一 ptrace 引擎纳入（native 行为有变，§10.5-1 的 `stack --native --json` 旧新差异对比为其强制验证项）；A3/A4/A5 代码形态三件套全部纳入；A8 子包化布局纳入。
>
> 与既有章节关系：§8.1 并入 A1；§9 P0/P1 并入 A7；§7.3 随批次 0 落地；§7.5 golden file 并入 §10.5 对齐验收；§7.2 git hooks 与 CI 版本矩阵（3.11–3.14 × `TARGET_PYTHON`，机制已在 conftest 而 CI 闲置）随批次顺带落地。

### 10.1 架构决策记录（ADR——重构允许的全部架构变更）

- [ ] A1. **Layout 值对象化**（吞并 §8.1）：`resolve_layout(version) -> Layout` 显式传递，消灭 `_active` 全局单例与读路径副作用；可同时观测不同 CPython 版本进程；dev override（offsets.json）语义保留
- [ ] A2. **ptrace 统一引擎**（`kernel/ptrace.py`）：一套 seize/interrupt/wait 状态机 + GETREGS + detach，syscall 追踪与 native 回溯共享；native 侧 ATTACH→SEIZE（与 design.md §14.2 相同的安全 detach 理由）；消灭 native_dump 与 syscall_tracer 两套 attach 逻辑
- [ ] A3. **CPython 对象模型层**（`cpython/`）：PyLong/Unicode/Bytes/Dict/Code/Frame/ThreadState/InterpreterState 各有绑定 Layout 的 typed reader；遍历代码零裸偏移运算（消灭 stack_dump 内 co_lo/co_hi 式跨度心算），版本差异只住 Layout
- [x] A4. **内存视图分离**：`SnapshotView`（页级 LRU，单次快照语义）/ `LiveView`（不缓存，长时观测）替代"RemoteReader 缓存 + read_uncached + _UncachedReader + 每 sample 手工新建 reader"四个零散机制；每种观测声明自己的一致性需求（批次 1 落地 `kernel/views.py`）
- [ ] A5. **DTO 去 format() 化**：数据与呈现严格分离（反转 §8.9 妥协），格式化全部入 `present/text.py`；dto 只存数据
- [ ] A6. **保留决策**（第一性原理验证后保留，非惯性）：collect/format/dump 三层、CLI 薄壳、errors 层级、clicolors 颜色策略
- [ ] A7. **行为修复**（吞并 §9 P0/P1）：TopStats idle 统计（§9 P0-1）、arch 检查下沉（§9 P0-2）、TraceFilter 混合表达式（§9 P0-3）、§9 P1 批次（timespec_out exit 重读 / EOF 空渲染 / execve 走用户过滤器 / attach 全异常回滚 / 无名帧 OWN% 归一 / 琐碎项）
- [ ] A8. **子包化与依赖规则**：`kernel/ target/ cpython/ observe/ present/` + 横切（errors/dto/color）；import 方向由结构契约测试机械强制（禁反向）

### 10.2 测试架构重设计（按 oracle 类型组织，非按模块镜像）

- [ ] 1. **spec-oracle 测试**（最高价值，新增）：linetable 解析 vs CPython 自带 `co_lines()`（2026-10 审查验证过的方法）；layout 表 vs gen_offsets 真实头文件产物；errno/flag/syscall 号常量 vs 系统头文件；syscall 渲染样例 vs strace 真实输出；native 线程头/符号样例 vs gdb 输出
- [ ] 2. **构造内存单测**：FakeMemory 构造器由 Layout 驱动（禁止从被测常量照抄）；版本布局 × 对象变体矩阵参数化
- [x] 3. **状态机单测**：统一 ptrace 引擎以 stubbed libc/waitpid 覆盖全状态路径（CLONE/EXEC/信号转发/detach 幂等）——批次 1 落地（next/tests/test_ptrace.py）
- [ ] 4. **存活集成测试**：target/spin/fast-syscall 三 fixture；session→snapshot→sampling→tracing 端到端；integration ≤15s 预算
- [ ] 5. **差异对比测试**：旧新 CLI 子进程（§10.5-1）
- [ ] 6. **结构契约测试**：公共 API 面 + 子包 import 方向（A8）机械强制
- [ ] 7. conftest/fixture 重设计（原 D6）：override 隔离（`_OVERRIDES_PATH` 指不存在路径，防套件静默验证 tracked offsets.json 而非内置表）、就绪轮询替代固定 `sleep(0.5)`、fast syscall target（0.2s 级 sleep，保住 elapsed≥0.1s 断言）、`run_tests.sh` mode 与透传 `-m` 合并为 and 表达式（pytest 多个 `-m` 后者胜的静默覆盖坑）

### 10.3 行为点清单机制（"参考旧代码"的落地）

- [ ] 1. 主代理逐模块产出行为点清单（旧模块 → 行为点 → 新契约条目 → 新测试）；缺项即契约缺口
- [ ] 2. 旧测试逐文件标注"已覆盖 / 已放弃（理由入 ADR）"，100% 过一遍
- [ ] 3. 复刻约束（非差异，禁止"顺手优化"）：6 寄存器参数全显、summary `total/s` 列、32 字符截断、线程升序、路径缩短规则（Python 帧末 2 级 / native basename）、空闲双启发式、JSON schema 与退出码等"怪但文档化"行为一律保留

### 10.4 批次计划（按新架构依赖方向，每批一个派发包）

- [ ] 0. 脚手架：`next/{pyprobe,tests,pyproject.toml}` 布局（包名仍 `pyprobe` 替换免改名；两套件独立 pytest 进程隔离，旧↔新对比走 CLI 子进程）、`scripts/next.sh`、CI next 作业、AGENTS.md TDD 工作流节（§7.3 并入）、`next/docs/contracts.md`（ADR 全文 + 模块契约骨架 + 行为点清单模板）、`next/pyprobe/offsets.json` 从根拷贝
- [x] 1. `kernel/` + errors + dto：mem 传输 / Snapshot·Live 视图（A4）/ procfs / **统一 ptrace 引擎**（A2，批内最大件，状态机 stub 测试全绿）
- [ ] 2. `target/`：identity / layout（A1 落地）/ symbols
- [ ] 3. `cpython/`：objects / dicts / code / frames / runtime / names（A3 落地；spec-oracle 测试同步上）
- [ ] 4. `observe/`：session / snapshot / sampling / profile / topstats（A7 中 TopStats 修复；首个端到端里程碑：snapshot 对 live target 出栈）
- [ ] 5. `observe/`：syscalls / native（共享批 1 ptrace 引擎；A7 中 syscall 修复）
- [ ] 6. `present/` + `cli` + 打包：text / jsonout / color（A5 落地）/ argparse 薄壳 / package-data
- [ ] 7. 对齐验收（§10.5 全量）+ 替换（§10.6 runbook）

### 10.5 对齐验收门禁（批 7 出口；行为对齐是约束，全部量化）

- [ ] 1. CLI 差异对比：同一存活目标上旧新 CLI 对比 `stack --json` / `stack --native --json` / `syscall --json`——归一化（pid/tid/thread_id/pc/elapsed/rendered 内地址正则抹零）后逐字节一致，豁免仅 A7 涉及字段；`record` folded 比键集合（计数天然抖动）；文本输出对固定 fixture 快照一致
- [ ] 2. 库 API 面对齐：`__all__` 名单一致（A1 新增 `resolve_layout` 为显式例外）
- [ ] 3. 全套件 unit+integration 绿、lint 净、覆盖率 ≥80%（分树度量）、import 方向契约测试绿
- [ ] 4. README 全部命令示例对新 CLI 重跑核对（示例必须来自真实输出，文档漂移教训）
- [ ] 5. 行为点清单 100% 标注完成；ADR 逐项有守护测试且注释回链契约条目

### 10.6 替换 runbook（单提交原子化）

- [ ] 1. `git rm -r pyprobe tests`；`git mv next/pyprobe pyprobe`；`git mv next/tests tests`
- [ ] 2. 根 pyproject/scripts/CI 去掉 next 专属配置，lint 范围还原
- [ ] 3. `scripts/ci.sh full` 全绿（含 smoke wheel 验证 package-data）后单提交落盘（消息列 A1–A8 与验收证据）
- [ ] 4. design.md 重写为新架构 SSOT（§2 模块表/§3 分层/§13 测试架构）；勾掉 §8.1 与 §9 对应项；回滚 = `git revert` 该提交

### 10.7 风险与控制

- 第二系统效应（重设计无度）→ ADR 纪律：架构变更仅限 §10.1，新想法先成 ADR 经主代理批准；行为对齐门禁兜底——任何过度设计都会在差异对比中显形
- 行为点清单遗漏导致功能回退 → §10.3 机制 + 差异对比 + README 示例重跑三重兜底
- 双树漂移 → 冻结铁律 + 紧急修复主代理单入口镜像为契约更新
- 子包化过度拆分 → 每包须有七项不可约能力中至少一项的明确归属，否则不建包
- 差异对比 oracle 自身 bug → 归一化规则与对比脚本属主代理资产，先入 tests 并接受 diff 审查
- 替换提交过大难审查 → 批次逐个验收后替换批纯目录移动 + 配置还原，`git mv` 逐文件可追
