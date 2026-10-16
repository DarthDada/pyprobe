# TODO

## 目录

1. [CLI 易用性与集成](#1-cli-易用性与集成)
2. [版本与架构覆盖](#2-版本与架构覆盖)
3. [native 栈输出对齐 gdb](#3-native-栈输出对齐-gdb)
4. [syscall 对齐 strace](#4-syscall-对齐-strace)
5. [深度检查与差异化](#5-深度检查与差异化)
6. [Web 监控后续迭代](#6-web-监控后续迭代)
7. [想法停车场](#7-想法停车场未评估未必开发)

> **定位**：本文件是任务索引 + 状态 + 当前目标（注意力锚点）——agent 应在任务执行中主动更新，
> 而非只在会话开始读一次。
> 完成即清理，不留尸体；条目带 `（YYYY-MM）` 日期。
> **优先级**：§1–§5 新功能逐项推进，aarch64（§2）为后续项。工程基础设施已完成（git hooks +
> CI 跨版本矩阵，见 git 历史）；变异/类型/属性测试三项经评估不采用，不再列入。
> 重构章节（原 §8/§9/§10）的条款编号定义见 [docs/contracts.md](docs/contracts.md) §7。
>
> **中长期开发模式**（2026-10）：需求 → agent 分析拆任务 → TDD + 父子 agent 协作
> （派发包模板见 contracts.md §6）。**可视化原则**：每个功能须附带 web 技术栈的
> 原理可视化解释（如 CPython 内存布局、libdw 工作机制），面向人类理解，形态随
> 批次契约定义。临时想法先入 §7 想法停车场，未必开发。

## 1. CLI 易用性与集成

> 库能力（`collect_*` 返回结构化 dataclass）与 CLI 能力对齐，打通 IDE/工具链/CI 程序化消费。`stack` 与 `syscall` 均已支持 `--json`。

- [ ] 1. `info` 子命令（2026-09）：解释器数、线程数、空闲/运行统计、`sys.argv`/`sys.executable`、GIL 状态等进程级概览，复用 `collect_python`
- [ ] 2. 按名称匹配目标进程（2026-09）：`-m/--match <name>`（扫 `/proc/*/cmdline`），省去先 `pgrep` 再传 `-p`；并可对"目标不是 CPython 进程"给出早期友好报错（目前要到符号查找失败才发现）

## 2. 版本与架构覆盖

> 现状：偏移量已验证 3.11–3.14 仅 x86-64（design.md §10）；syscall 追踪仅 x86-64；未验证版本回退 3.12 偏移量并告警。

- [ ] 1. aarch64 偏移量实际验证（2026-09）：与 x86-64 理论相同（均为 64 位 LP64），未实测
- [ ] 2. aarch64 syscall 追踪（2026-09）：目前 `UnsupportedArchitecture`（observe/syscall_abi.py 仅 x86-64 表）；需 aarch64 syscall 号表 + 解码元数据 + 寄存器 ABI 适配（GETREGS 结构不同）

## 3. native 栈输出对齐 gdb

> 背景：`pyprobe stack --native` 对齐 gdb `thread apply all bt`（2026-09 实测）。P0（libdw
> 加载回退 / 符号探测降级 / 回溯失败标注）与 P1（LLVM 后缀截断、file:line）已落地
> （2026-10，历史见 git）。以下为剩余项，全部基于现有技术栈
> （ctypes + libdw/libdwfl，无新依赖）。

- [ ] 1. 线程头格式（2026-09）：补 pthread 描述符地址（`Thread 1 (Thread 0x7f... (LWP 7101) "python3")`）——需评估获取方式（ptrace 读 pthread 结构 vs 保持现状仅 LWP）
- [ ] 2. 内联帧展开（2026-09）：`dwarf_getscopes` 作用域链上每个 `DW_TAG_inlined_subroutine` DIE 展开为逻辑帧，`DW_AT_abstract_origin` 回溯取函数名，`DW_AT_call_file`/`call_line` 取调用点
- [ ] 3. 参数名（2026-09）：subprogram DIE 遍历 `DW_TAG_formal_parameter` 取 `DW_AT_name`
- [ ] 4. 参数值（2026-09，最难，可只做子集）：`dwarf_cfi_addrframe` + `dwarf_frame_register` 求 CFI，`dwarf_getlocation` 位置表达式解释器（`DW_OP_fbreg`/`DW_OP_regN`/`DW_OP_addr` 等），远程读内存复用视图层（`kernel/views.py` 的 `LiveView`）；`@entry` 依赖 `DW_AT_call_site`/GNU 扩展，视成本取舍

## 4. syscall 对齐 strace

> 现状：引擎已支持 `-e trace=` 过滤、`--summary`、`--json`、`-v`、errno 名解码（`= -1 ENOENT (...)`）与 elapsed 计时（`<0.000123>`）；以下为与 strace 的剩余差距。

- [ ] 1. 启动模式（2026-09）：`pyprobe syscall -- CMD`——strace 支持从启动追踪；当前须先起进程再抢 PID，漏掉启动初期（如模块导入期）的系统调用。ptrace 引擎已在手，缺 fork/exec 入口
- [ ] 2. TRACEFORK 跟随子进程（2026-09）：当前仅 TRACECLONE（线程），不跟随 `fork()` 出的子进程
- [ ] 3. 输出细节（2026-09）：`-y` fd 解码（fd→路径，读 `/proc/<pid>/fd`）、`-e read=N/write=N`、`-e errno=`、`-tt` 时间戳列

## 5. 深度检查与差异化

> 高成本方向：混合栈补齐 py-spy `--native` 的语义，帧局部变量则超越 py-spy/gdb 现有能力。

- [ ] 1. Python 帧局部变量/参数读取（2026-09）：遍历 `_PyInterpreterFrame.localsplus`，dump 时显示调用参数。py-spy 无此能力，是真正差异化功能，但需完整 object graph 解析
- [ ] 2. Python/native 混合栈（2026-09）：py-spy `--native` 会把 Python 帧内联进 native 栈；当前两种栈割裂，`--native` 输出对 Python 用户语义有限

## 6. Web 监控后续迭代

> 首版已落地（批 8a–8d；契约见 contracts.md §4 批次 8 表）。
> 快照端点已落地（批 16，WM9/WA6）。

- [ ] 1. Web 监控迭代（2026-10）：火焰图（record 聚合 + 页面内嵌渲染）；syscall 事件流页面；交互控制（暂停采样/调频率——需 WebSocket 替换或补充 SSE）

## 7. 想法停车场（未评估，未必开发）

> 临时想到的问题/想法先停在这里，带日期；评估后要么转为 §1–§6 正式条目，要么删除。
