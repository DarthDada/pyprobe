# TODO

## 目录

2. [CLI 易用性与集成](#2-cli-易用性与集成)
3. [版本与架构覆盖](#3-版本与架构覆盖)
4. [native 栈输出对齐 gdb](#4-native-栈输出对齐-gdb)
5. [syscall 对齐 strace](#5-syscall-对齐-strace)
6. [深度检查与差异化](#6-深度检查与差异化)
7. [工程基础设施](#7-工程基础设施)
8. [代码模块化与解耦](#8-代码模块化与解耦)
9. [代码审查发现（2026-10）](#9-代码审查发现2026-10)

> **全局优先级**：功能（§2–§6）与基础设施（§7）两轨并行。上轮 P0（CPython 3.14 支持，§3）已于 2026-10 交付（3.14.4 x86-64：`_VERSION_EXTRA_KEYS` oracle TDD 接入 + `TARGET_PYTHON` 集成全绿；并修复 `offsets.json` 开发期覆盖合并泄漏 3.12 陈旧 key、`dump_python` 吞版本告警两个 bug）；syscall `--json`（§2.3）已于 2026-10 交付（`format_syscalls_json` + `dump_syscalls(json_output=)`，与 `--summary` CLI 互斥）；当前优先级 = §2 剩余项（info / 按名称匹配，低成本）+ §7 剩余 P1（远程 CI / git hooks / 文档增补）并行；其余按章节内次序推进，§4 P0（生产环境可加载）作为已发布功能的健壮性问题可随需插入，§9.1 P0（2026-10 审查发现的 4 个用户可见 bug）同为可随需插入的健壮性修复，aarch64（§3）待实际环境。

## 2. CLI 易用性与集成

> 库能力（`collect_*` 返回结构化 dataclass）与 CLI 能力对齐，打通 IDE/工具链/CI 程序化消费。`stack` 与 `syscall` 均已支持 `--json`。

- [ ] 1. `info` 子命令：解释器数、线程数、空闲/运行统计、`sys.argv`/`sys.executable`、GIL 状态等进程级概览，复用 `collect_python`
- [ ] 2. 按名称匹配目标进程：`-m/--match <name>`（扫 `/proc/*/cmdline`），省去先 `pgrep` 再传 `-p`；并可对"目标不是 CPython 进程"给出早期友好报错（目前要到符号查找失败才发现）

## 3. 版本与架构覆盖

> 现状：偏移量已验证 3.11–3.14 仅 x86-64（design.md §10）；syscall 追踪仅 x86-64；未验证版本回退 3.12 偏移量并告警。

- [ ] 1. aarch64 偏移量实际验证：与 x86-64 理论相同（均为 64 位 LP64），未实测
- [ ] 2. aarch64 syscall 追踪：目前 `UnsupportedArchitecture`（syscall_table.py 仅 x86-64 表）；需 aarch64 syscall 号表 + 解码元数据 + 寄存器 ABI 适配（GETREGS 结构不同）

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

> 现状：637 个测试（含参数化展开）+ FakeReader/对象构造器 + `target_pid`/`spin_pid` fixture（支持 `TARGET_PYTHON` 跨版本端到端）已具备；P0 已于 2026-09 落地——pytest-cov 覆盖率基线（`scripts/run_tests.sh --cov`，CI test 阶段强制，79% fail-under）、watch 模式（`scripts/run_tests.sh watch`，watchfiles 保存即重跑）、pytest 严格化（`-ra`/`--strict-markers`/`--strict-config` + `filterwarnings = ["error"]`）。以下为按红→绿→重构循环衡量的剩余缺口。TDD 是流程约束，靠自觉必退化。

### P1 — 流程纪律强制

- [ ] 1. 远程 CI：新增 `.github/workflows/ci.yml`，仅调用 `scripts/ci.sh`（与本地一致），强制"提交必须全绿"
- [ ] 2. git hooks：pre-commit/pre-push 跑单元测试（无裸命令，走 `scripts/run_tests.sh unit`）
- [ ] 3. AGENTS.md 增补 TDD 工作流约束条目（先写失败测试、red 阶段验证、测试与实现同提交）
- [ ] 4. design.md §13 增补测试架构对应变更（§13.1/13.2 已含 record/top/`--json` 测试架构；覆盖率目标、watch 模式、CI 阶段待补）

### P2 — "绿"的质量与重构安全网

- [ ] 5. golden file 测试：`format_process` / CLI 文本与 `--json` 输出的格式快照基线（tests/test_json_output.py 现为结构断言，非快照），保障格式化重构安全
- [ ] 6. 变异测试（mutmut）：解析二进制内存布局的代码测试"看似覆盖但抓不住错位偏移"风险高，用变异测试验证测试有效性
- [ ] 7. typecheck（mypy/pyright）：lint 已落地（`scripts/lint.sh` 接入 CI lint 阶段），ctypes 重度使用 typecheck 噪音大待评估
- [ ] 8. 属性测试（hypothesis）：`linetable` / `dict_iter` / `pyobject` 解析器代码的典型受益者

## 8. 代码模块化与解耦

> 2026-09 依赖审计结论：20 模块依赖图为干净 DAG（无循环），collect/format/dump 三层分离整体健康。P1/P2（重复消除与模块边界清理）与 P3 条目 9（types.py docstring 收窄）已于 2026-09 落地（480 个测试 + 端到端集成测试为安全网，全绿）；仅余 `offsets` 去 global 化（P3）显式延后——覆盖率基线已交付（`--cov`），需 §7.7 lint 作前置安全网。涉及模块增删或 API 转正时同步 design.md §2 与 `__init__.py` `__all__`。

### P3 — 高成本重构（独立 PR，需安全网护航）

- [ ] 1. `offsets` 去 global 化：`_active` 模块级可变单例（offsets.py:177），`get()` 未 configure 时隐式触发 configure（offsets.py:214-217，读路径带副作用）；71 处调用分布于 6 模块（stack_dump 30 / thread_names 12 / pyobject 11 / dict_iter 10 / sampler 7 / linetable 1）。改为 per-session 偏移量表对象随 reader/session 传递后：可同时探测不同 CPython 版本的进程、消除 tests/test_offsets.py 的手工复位（`offsets._active = None`）。改动面大（59 处 `get`），以 §7.7 lint 为前置（覆盖率基线已交付）

## 9. 代码审查发现（2026-10）

> 2026-10 全量审查（21 个模块 + 全部文档 + scripts/ + tests/）的问题汇总，审查时全量测试 651 个全绿。标注"实测"的条目均已派生子进程端到端复现（遵循 design.md §13.3 手动探测约束）；其余经代码阅读 + CPython 3.12/3.13 头文件与源码核实。§9.1 为代码 bug（按严重度分级），§9.2 为代码与文档不一致（按文档分组），与现有章节相关处已交叉引用。

### 9.1 代码 bug

#### P0 — 用户正常使用即可撞上

- [ ] 1. `top` idle 线程统计错误（top.py:46-53）：idle 守卫误查 `self.current`（idle 线程永不入该 dict），同一 tid 每个采样周期重复 append 进 `idle_threads`（实测 3 次采样 → `[5, 5, 5]`，渲染 `Idle threads: 5, 5, 5`，50Hz 下无限膨胀刷屏）；且曾活跃线程转 idle 后既不进 idle 列表也不从 `current` 移除，带着陈旧帧留在 "Active threads" 表。修：守卫改查 `idle_threads` 去重，idle 时从 `current` 移除或标记；补"同一 idle 线程连续采样"与"active→idle 转换"守护测试（test_top.py:100 只 update 一次恰好掩盖）
- [ ] 2. `stack --native` 文本输出打印两遍进程头（native_dump.py:252 + 287，实测复现）：`dump_native` collect 前打印头部（失败路径上下文），`format_native` 又自带 `Process: {cmdline}` 头部（无 pid/颜色，两处格式还不一致）。修：`format_native` 去掉自带头部；README native 示例只有一个头部
- [ ] 3. syscall CLI 路径绕过架构检查（syscall_tracer.py:335-337 vs 359-365）：`platform.machine()` 检查只在 `collect_syscalls`，`dump_syscalls` 直接实例化 `SyscallTracer`，其 `except UnsupportedArchitecture` 成死代码。aarch64 上 CLI 会 attach 成功并用 x86-64 `_UserRegs` 布局读寄存器输出垃圾，而非 README 承诺的 `UnsupportedArchitecture` 报错。修：检查下沉 `SyscallTracer.__init__`/`attach()`（关联 §3.2）
- [ ] 4. `TraceFilter` 混合包含/排除语义反转（syscall_render.py:284-290，实测复现）：`-e trace=file,!openat` 中 openat 被追踪（应排除），`-e trace=read,!write` 中 write 被追踪——`!` 项被并入包含集。纯排除（`-e trace=!futex`）正确，测试只覆盖了纯排除分支。修：改 `include - exclude` 语义 + 补混合表达式测试（关联 §5）

#### P1 — 边界路径与次要语义

- [ ] 5. `attach()` 重扫循环回滚缺口（syscall_tracer.py:166-170）：循环内 `_list_tids` 抛 `ProcessNotFound`（目标 attach 中途退出）不被 `except AttachFailed` 捕获，已 seize 线程不回滚；`self.tids` 第 182 行才赋值，此刻 `detach()` 为空操作。修：回滚改为捕获所有异常
- [ ] 6. `_handle_exec` 绕过用户过滤器（syscall_tracer.py:300）：补发 execve 事件用全新 `TraceFilter("")`（全匹配），`-e trace=!execve` 也输出。修：把 `flt` 传入
- [ ] 7. `timespec_out` 参数在 entry 时读取（syscall_render.py:202-203）：`clock_gettime` 输出参数读到的是调用前陈旧内容，`_fill_out_args` 只处理 `buf_in`/`buf_out` 从不重读（strace 在 exit 时读）。修：exit 阶段重读 `timespec_out`
- [ ] 8. `_fill_out_args` EOF 误读（syscall_render.py:234）：`ret == 0` 时按 32 字节读取并展示内核未写的缓冲区内容；同函数 `ret < 0` 分支为死代码（调用点保证 `ret >= 0`）。修：`ret == 0` 渲染空串
- [ ] 9. `TopStats.render` 无名顶帧 OWN% 恒 0（top.py:85 vs 57）：`update()` 把无名帧存 `"?"` 键，render 却用 `frame.name`（`None`）查询。修：render 侧同样 `or "?"` 归一
- [ ] 10. 琐碎项打包：`run()` 的 `wpid == 0` 死分支（阻塞式 waitpid 不返回 0，syscall_tracer.py:230）；`_list_tids` 捕获 `listdir` 不会抛的 `ProcessLookupError` 却漏 `PermissionError`（/proc 权限不足时用户看到 traceback 而非 `[!]` 错误，syscall_tracer.py:104-107）；`read_pybytes` 的 `size < 0` 死检查（pyobject.py:59，`read_u64` 无符号）；attach 时线程已在 syscall 内的相位判定可能产生一条伪事件（rax 中的 syscall 号被当返回值）——已知 ptrace 类问题，修复或记入 design.md §14.4 坑位清单

### 9.2 代码与文档不一致

#### README（用户可见）

- [ ] 1. Python 栈输出示例来自 C 参考实现而非 Python 主交付物（README.md:60-70）：示例帧带 `[qualname=...]` 后缀、线程头为裸 `Thread <tid>`；Python 实现无 qualname 输出（`co_qualname` 仅用于读取跨度计算，stack_dump.py:53-59），线程头带 `(idle)` 标记和 `:"name"`。design.md §1 同样声称输出含 qualname。**需先决策**：Python 侧补 qualname 显示（对齐 C 参考实现 py_stack_dump.c:969）还是文档换成真实 Python 输出
- [ ] 2. `--json` 示例 `cmdline` 是数组（README.md:82），实测为字符串（`/proc/<pid>/cmdline` 空格拼接，procmeta.py:21）
- [ ] 3. native 输出示例三处不符（README.md:109-118）：线程头 `Thread 1 (Thread 0x... (LWP ...) ...)` 是 gdb 目标形态（pthread 地址为 §4.6 未做项），实际为 `Thread 1 (LWP <tid>) "<comm>":`；示例模块为完整路径但缺省输出 basename（需 `-v`）；示例无双进程头（见 §9.1-2）
- [ ] 4. 流式示例 openat 只写 3 个参数（README.md:167），`_decode_args` 恒输出 6 个寄存器值（同块 clock_nanosleep 示例与代码一致，openat 不一致）
- [ ] 5. 快速开始 `uv run` + `pgrep -f fastapi_app` 取 PID（README.md:43-44）与 design.md §13.3/AGENTS.md 的 PID 错位警告自相矛盾（pgrep 同时命中 uv 包装进程与 Python 子进程）；§2.2（`-m/--match`）是正解，落地前 README 应至少提示该风险

#### design.md

- [ ] 6. §8.2 "结果按 tid 降序排列（匹配 gdb）"与代码相反（design.md:274）：native_dump.py:220 为升序，且系 1fbb785 的明确意图（主线程编号 Thread 1）——修复前残留
- [ ] 7. §12.1 两处 "Python 3.8+ 宿主" / `requires-python = ">=3.8"`（design.md:374,378），pyproject.toml:10 实为 `>=3.11`（代码 `str | None` 注解也需 3.10+；README 正确）；colors.py:5 docstring "Python 3.8+" 同样陈旧
- [ ] 8. §14.1 规模数字漂移：`SYSCALL_NAMES` "~362 条"实为 375，`DECODE` "~50"实为 98；syscall_table.py:9 docstring 与 README.md:181 的 "~50" 需同步
- [ ] 9. §5 仍把 `read_cmdline`/`decode_py_version` 描述为 elf.py 内容（design.md:187），§8.6 已迁至 procmeta.py（§2 模块表正确，仅此 bullet 残留）
- [ ] 10. §7.2 的 3.12 tagged 指针方向写反（design.md:242）：CPython 3.12 源码存储为 `values - 1`、恢复为 `tagged + 1`，代码（thread_names.py:144）与 C 参考实现均正确，文档"ptr-1 的 values 数组"方向相反
- [ ] 11. §11 "aarch64 需重新生成偏移量"（design.md:363）与 §6.1（LP64 偏移量相同、无需架构键）/§10 表格/TODO §3.1（理论相同、仅未实测）矛盾
- [ ] 12. §3.1 `ProfileData` 字段列表缺 `version_warning`（design.md:108；types.py:229 已落地并在 record.py:91 使用）

#### docstring 与工程脚本

- [ ] 13. `PermissionDenied` 在 §3.2 异常层级文档化并导出，但全代码库无 raise 点（errors.py:21）——权限失败实际以 `OSError`（dump_python 捕获）或 `AttachFailed` 呈现：补 raise 点或在 §3.2 标注"保留未用"
- [ ] 14. types.py:178 docstring 引用已改名模块 `syscall_trace._decode_args`（应为 `syscall_render`）
- [ ] 15. `__init__.py` docstring "Public API" 只列 stack 一族函数，与 `__all__` 完整导出（syscall/record/top/Sampler 等）不符
- [ ] 16. `scripts/sync.sh` pip 回退不装 ruff（sync.sh:21），无 uv 环境下 lint.sh（CI lint 阶段）不可用；README 离线流程说明未覆盖此缺口
- [ ] 17. 测试计数漂移：§7 现状 "637 个测试"实为 651——随 §9.1 修复新增守护测试后一并更新
