# TODO

## 目录

1. [CLI 易用性与集成](#1-cli-易用性与集成)
2. [版本与架构覆盖](#2-版本与架构覆盖)
3. [native 栈输出对齐 gdb](#3-native-栈输出对齐-gdb)
4. [syscall 对齐 strace](#4-syscall-对齐-strace)
5. [深度检查与差异化](#5-深度检查与差异化)
6. [工程基础设施](#6-工程基础设施)

> **全局优先级**：§1/§2/§3/§4/§5 新功能待逐项推进；§6 剩余工程项（git hooks、CI 版本矩阵、变异/类型/属性测试）与 aarch64（§2）为后续项。已完成的重构章节（原 §8 代码模块化、§9 代码审查遗留、§10 第一性原理重构）已从本文件移除，其条款编号（原 §10.x）定义迁移至 [docs/contracts.md](docs/contracts.md) §7。

## 1. CLI 易用性与集成

> 库能力（`collect_*` 返回结构化 dataclass）与 CLI 能力对齐，打通 IDE/工具链/CI 程序化消费。`stack` 与 `syscall` 均已支持 `--json`。

- [ ] 1. `info` 子命令：解释器数、线程数、空闲/运行统计、`sys.argv`/`sys.executable`、GIL 状态等进程级概览，复用 `collect_python`
- [ ] 2. 按名称匹配目标进程：`-m/--match <name>`（扫 `/proc/*/cmdline`），省去先 `pgrep` 再传 `-p`；并可对"目标不是 CPython 进程"给出早期友好报错（目前要到符号查找失败才发现）

## 2. 版本与架构覆盖

> 现状：偏移量已验证 3.11–3.14 仅 x86-64（design.md §10）；syscall 追踪仅 x86-64；未验证版本回退 3.12 偏移量并告警。

- [ ] 1. aarch64 偏移量实际验证：与 x86-64 理论相同（均为 64 位 LP64），未实测
- [ ] 2. aarch64 syscall 追踪：目前 `UnsupportedArchitecture`（observe/syscall_abi.py 仅 x86-64 表）；需 aarch64 syscall 号表 + 解码元数据 + 寄存器 ABI 适配（GETREGS 结构不同）

## 3. native 栈输出对齐 gdb

> 背景：`pyprobe stack --native` 对齐 gdb `thread apply all bt`（2026-09 实测；`GElf_Word` 32 位截断与线程排序两问题已修）。以下为剩余差异的落地计划，全部基于现有技术栈（ctypes + libdw/libdwfl，无新依赖）。P0 三项已落地（ADR A9/A10）；P1/P2 为增量展示 / DWARF 能力。

### P0 — 生产环境可加载（前置条件，已完成）

- [x] 1. libdw 加载名回退：`ctypes.CDLL` 依次尝试 `libdw.so.1` → `libdw.so`（或 `ctypes.util.find_library("dw")`），兼容 ldconfig 只注册其一的环境（ADR A9 已落地，contracts.md NA1）
- [x] 2. 符号探测降级：加载后检查 `dwarf_getscopes` / `dwfl_module_getsrc` / `dwarf_cfi_addrframe` 等是否存在（最低需 elfutils 0.158，CFI 0.142+），缺失则标记对应特性不可用，保底符号名+回溯（ADR A9 已落地，contracts.md NA10）
- [x] 3. 回溯失败标注对齐 gdb：`unwind_failed` 判定修正（observe/native.py 现判 `fr != 0 且 0 帧` 或 lone `pc=0` 哨兵帧；如 io_uring `iou-sqp-*` 线程输出 `Backtrace stopped`）（ADR A10 已落地，contracts.md NA6/TX5）

### P1 — 展示层对齐（低成本高收益）

- [x] 4. LLVM 后缀截断：`re.sub(r'\.llvm\.\d+$', '', name)` 对齐 gdb 短符号名（`run_mod.llvm.1234` → `run_mod`）（ADR A11 已落地，contracts.md TX5；仅呈现层，DTO/JSON 保持原值）
- [x] 5. file:line：`dwfl_module_getsrc` + `dwarf_linesrc`/`dwarf_lineno`，输出 `at src/unix/linux.c:1432`（仅带 DWARF 的模块生效，如 uvloop；python 主程序无 `.debug_*` 与 gdb 持平）（ADR A12 已落地，contracts.md NA11/TX5/D1/JS2；新增 `-g` mini `.so` 集成 fixture）
- [ ] 6. 线程头格式：补 pthread 描述符地址（`Thread 1 (Thread 0x7f... (LWP 7101) "python3")`）——需评估获取方式（ptrace 读 pthread 结构 vs 保持现状仅 LWP）

### P2 — 完整 DWARF 解析（高成本）

- [ ] 7. 内联帧展开：`dwarf_getscopes` 作用域链上每个 `DW_TAG_inlined_subroutine` DIE 展开为逻辑帧，`DW_AT_abstract_origin` 回溯取函数名，`DW_AT_call_file`/`call_line` 取调用点
- [ ] 8. 参数名：subprogram DIE 遍历 `DW_TAG_formal_parameter` 取 `DW_AT_name`
- [ ] 9. 参数值（最难，可只做子集）：`dwarf_cfi_addrframe` + `dwarf_frame_register` 求 CFI，`dwarf_getlocation` 位置表达式解释器（`DW_OP_fbreg`/`DW_OP_regN`/`DW_OP_addr` 等），远程读内存复用视图层（kernel/views.py 的 `LiveView`）；`@entry` 依赖 `DW_AT_call_site`/GNU 扩展，视成本取舍

## 4. syscall 对齐 strace

> 现状：引擎已支持 `-e trace=` 过滤、`--summary`、`--json`、`-v`、errno 名解码（`= -1 ENOENT (...)`）与 elapsed 计时（`<0.000123>`）；以下为与 strace 的剩余差距。

- [ ] 1. 启动模式 `pyprobe syscall -- CMD`：strace 支持从启动追踪；当前须先起进程再抢 PID，漏掉启动初期（如模块导入期）的系统调用。ptrace 引擎已在手，缺 fork/exec 入口
- [ ] 2. TRACEFORK 跟随子进程：当前仅 TRACECLONE（线程），不跟随 `fork()` 出的子进程
- [ ] 3. 输出细节：`-y` fd 解码（fd→路径，读 `/proc/<pid>/fd`）、`-e read=N/write=N`、`-e errno=`、`-tt` 时间戳列

## 5. 深度检查与差异化

> 高成本方向：混合栈补齐 py-spy `--native` 的语义，帧局部变量则超越 py-spy/gdb 现有能力。

- [ ] 1. Python 帧局部变量/参数读取：遍历 `_PyInterpreterFrame.localsplus`，dump 时显示调用参数。py-spy 无此能力，是真正差异化功能，但需完整 object graph 解析
- [ ] 2. Python/native 混合栈：py-spy `--native` 会把 Python 帧内联进 native 栈；当前两种栈割裂，`--native` 输出对 Python 用户语义有限

## 6. 工程基础设施

> 现状：912 个测试（含参数化展开；`scripts/run_tests.sh --cov` 实测 911 passed + 1 skipped，覆盖率 97.0%）+ FakeReader 构造内存/`Layout` 驱动对象构造器 + `target_pid`/`fast_syscall_pid` fixture（就绪握手 + `TARGET_PYTHON` 跨版本端到端）已具备；覆盖率基线（`scripts/run_tests.sh --cov`，CI test 阶段强制，79% fail-under）、远程 CI（`.github/workflows/ci.yml`，PR/push 触发、仅调用 `scripts/ci.sh`）、`slow` marker（真实采样/sleep 测试可经 `-m 'not slow'` 跳过）、watch 模式、pytest 严格化（`-ra`/`--strict-markers`/`--strict-config` + `filterwarnings = ["error"]`）、ruff lint（`scripts/lint.sh`）均已落地。以下为剩余缺口。TDD 是流程约束，靠自觉必退化。

### P1 — 流程纪律强制

- [ ] 1. git hooks：pre-commit/pre-push 跑单元测试（无裸命令，走 `scripts/run_tests.sh unit`）；CI 版本矩阵（3.11–3.14 × `TARGET_PYTHON`，机制已在 conftest 而 CI 闲置）随之一并落地

### P2 — "绿"的质量与重构安全网

- [ ] 2. 变异测试（mutmut）：解析二进制内存布局的代码测试"看似覆盖但抓不住错位偏移"风险高，用变异测试验证测试有效性
- [ ] 3. typecheck（mypy/pyright）：ctypes 重度使用 typecheck 噪音大待评估
- [ ] 4. 属性测试（hypothesis）：`linetable` / `dict_iter` / `pyobject` 解析器代码的典型受益者
