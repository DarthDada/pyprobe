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

`kernel/ target/ cpython/ observe/ present/` + 横切（errors/dto/color）；import 方向
由结构契约测试机械强制（禁反向）。包名仍为 `pyprobe`，替换免改名。

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
| `kernel/mem.py` | 字节传输：远程读原语与错误语义 | TBD | TBD |
| `kernel/views.py` | A4：SnapshotView / LiveView | TBD | TBD |
| `kernel/procfs.py` | /proc 元信息（maps/stat/task 枚举） | TBD | TBD |
| `kernel/ptrace.py` | A2：统一 ptrace 状态机（批内最大件） | TBD | TBD |
| `errors.py`（横切） | 错误层级（保留旧语义） | TBD | TBD |
| `dto.py`（横切） | A5：纯数据 DTO | TBD | TBD |

### 批次 2 — `target/`

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `target/identity.py` | CPython 进程识别与版本判定 | TBD | TBD |
| `target/layout.py` | A1：resolve_layout / Layout 值对象 / dev override | TBD | TBD |
| `target/symbols.py` | ELF 符号与运行时结构定位 | TBD | TBD |

### 批次 3 — `cpython/`

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `cpython/objects.py` | PyLong/Unicode/Bytes typed reader | TBD | TBD |
| `cpython/dicts.py` | 字典遍历（split/combined 表） | TBD | TBD |
| `cpython/code.py` | Code 对象与 linetable | TBD | TBD |
| `cpython/frames.py` | Frame 遍历 | TBD | TBD |
| `cpython/runtime.py` | InterpreterState/ThreadState 图遍历 | TBD | TBD |
| `cpython/names.py` | 线程名等辅助 | TBD | TBD |

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
