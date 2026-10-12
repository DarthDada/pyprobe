# pyprobe 模块与 ADR 契约

> 本文件记录第一性原理重建（原 TODO §10，2026-10 原子替换完成）的**架构决策（ADR A1–A13）**、
> 分层与依赖规则、模块契约骨架、行为点清单与派发包模板。架构叙述 SSOT 见
> [design.md](design.md)，本文是其 ADR / 模块契约明细；tests/ 的契约守护测试注释回链
> 本文条目编号（如 T12、V8、SY9）。
>
> 替换状态：`next/` 已原子替换旧树（`git rm -r pyprobe tests` +
> `git mv next/{pyprobe,tests} …`，§10.6）。下文"旧树"指替换前实现，作为行为对齐的
> 运行时 oracle 与知识来源。迁移期台账（旧测试逐文件标注、行为点表「旧测试出处」列）
> 已归档 [docs/migration-2026-10.md](migration-2026-10.md)（ADR A14）。
>
> 铁律（延续）：**ADR 纪律**（架构或行为变更必须先成 ADR 记入本文件再实施）、
> **复刻约束**（怪但文档化的行为一律保留，禁止"顺手优化"——批 7 对齐门禁实证过其必要性）。

## 1. 第一性原理与分层

pyprobe 的本质是**外部观测者问题**：仅凭 PID 与内核接口重建 CPython 运行时状态并呈现。
七项不可约能力各有唯一归属包；每包至少归属一项，否则不建包（防过度拆分）。
能力归属表见 [design.md §1.1](design.md#11-七项不可约能力与分层)（架构 SSOT，不在此重复）。

**依赖方向铁律**：`kernel → target → cpython → observe → present → {cli, web}`
（cli 与 web 平级、互不依赖；cli→web 合法且仅经函数内延迟 import，见 A13），仅横切层
（`errors` / `dto` / `color`）可被任意层引用且自身不依赖任何层。import 方向由结构契约
测试机械强制（ADR A8），禁反向。

## 2. 架构决策记录（ADR——重构允许的全部架构变更）

> 决策已确认（2026-10）：A1、A2、A3/A4/A5 三件套、A8 全部纳入；A9–A12 为替换后
> 增量解禁（native P0-1/P0-2/P0-3/P1-4/P1-5）；A13 为替换后新功能扩展（web 监控
> 模式）；A14 为文档契约机械化（docs lint）。每项 ADR 带状态行，落地时须有
> 守护测试，且测试注释回链本文件对应条目（§10.5-5 验收项）。

### A1. Layout 值对象化（吞并旧 §8.1）

**状态**：landed（2026-10，b11fc84 原子替换）

`resolve_layout(version) -> Layout` 显式传递，消灭旧 `offsets._active` 全局单例与
`get()` 读路径副作用（隐式 configure）。收益：可同时观测不同 CPython 版本的进程；
测试无需手工复位全局态。dev override（offsets.json）语义保留。库 API 面新增
`resolve_layout` 为 §10.5-2 的显式例外。

### A2. 统一 ptrace 引擎（`kernel/ptrace.py`）

**状态**：landed（2026-10，b11fc84 原子替换）

一套 seize/interrupt/wait 状态机 + GETREGS + detach，syscall 追踪与 native 回溯共享；
native 侧 ATTACH→SEIZE（与旧 design.md §14.2 相同的安全 detach 理由）；消灭旧
native_dump 与 syscall_tracer 两套 attach 逻辑。native 行为有变，§10.5-1 的
`stack --native --json` 旧新差异对比为其强制验证项。

### A3. CPython 对象模型层（`cpython/`）

**状态**：landed（2026-10，b11fc84 原子替换）

PyLong/Unicode/Bytes/Dict/Code/Frame/ThreadState/InterpreterState 各有绑定 Layout 的
typed reader；遍历代码零裸偏移运算（消灭旧 stack_dump 内 co_lo/co_hi 式跨度心算）；
版本差异只住 Layout。

### A4. 内存视图分离

**状态**：landed（2026-10，b11fc84 原子替换）

`SnapshotView`（页级 LRU，单次快照语义）/ `LiveView`（不缓存，长时观测）替代旧树
"RemoteReader 缓存 + read_uncached + _UncachedReader + 每 sample 手工新建 reader"
四个零散机制；每种观测声明自己的一致性需求。

### A5. DTO 去 format() 化

**状态**：landed（2026-10，b11fc84 原子替换）

数据与呈现严格分离（反转旧 §8.9 妥协）：格式化全部入 `present/text.py`，dto 只存数据。

### A6. 保留决策（第一性原理验证后保留，非惯性）

**状态**：landed（2026-10，b11fc84 原子替换）

collect/format/dump 三层语义、CLI 薄壳、errors 层级、clicolors 颜色策略维持不变——
在新分层中以 session→snapshot→present 与 `present/color.py` 的形态重现。

### A7. 行为修复（吞并旧 §9 P0/P1）

**状态**：landed（2026-10，b11fc84 原子替换）

随对应批次落地，旧树不单独修：

- TopStats idle 统计（旧 §9 P0-1）：守卫查 idle 列表去重，active→idle 从 current 移除；
  守护测试覆盖"同一 idle 线程连续采样"与"active→idle 转换"（批次 4）
- arch 检查下沉到 ptrace 引擎 attach 路径（旧 §9 P0-2；批次 1/5）
- TraceFilter 混合包含/排除改 `include - exclude` 语义（旧 §9 P0-3；批次 5）
- 旧 §9 P1 批次：timespec_out exit 重读、EOF 空渲染、execve 走用户过滤器、attach 全
  异常回滚、无名帧 OWN% 归一、琐碎项打包（批次 4/5）

### A8. 子包化与依赖规则

**状态**：landed（2026-10，b11fc84 原子替换）

`kernel/ target/ cpython/ observe/ present/` + 横切（errors/dto）；import 方向
由结构契约测试机械强制（禁反向）。包名仍为 `pyprobe`，替换免改名。
颜色策略属呈现层（`present/color.py`，仅 present/cli 可引用），非横切——
A5 落地后 dto 不再依赖颜色（2026-10 批次 1 契约细化，修订 §10.1 A8 原文
"横切含 color" 的表述）。

### A9. native 冻结期新功能解禁（原 TODO §3 P0-1 / P0-2）

**状态**：landed（2026-10，e7533db）

原子替换后，下述两项曾作为 native 的"冻结期不实施"复刻项（NA1 备注）保留；
现解禁，作为替换**之后**的增量特性落地（2026-10，非复刻约束违反）：

- **P0-1 libdw 加载名回退**：`_load_libdw` 依次尝试 `libdw.so.1` → `libdw.so`
  → `find_library("dw")`，首个可加载者胜出；全部失败抛最后的 `OSError`
  （不静默吞掉加载失败）。有 `libdw.so.1` 的常见环境行为与旧树一致，
  故对成功路径非行为变更。
- **P0-2 可选符号探测**：加载后探测 `dwarf_getscopes` / `dwfl_module_getsrc`
  / `dwarf_cfi_addrframe` 是否导出，记入 `available_symbols`；后续 P1/P2
  消费者按能力位跳过缺失特性，核心"符号名 + 回溯"路径（NA4）不依赖可选符号。
  当前无消费者，为 P1/P2 的前置能力位。

**不改动**：本 ADR 批内不改 NA6 的 `unwind_failed` 判定与 TX5 输出；P0-3 随后由
A10 解禁修正，P1-4（符号后缀截断）仍为契约冻结项。

### A10. native 回溯失败判定对齐 gdb（原 TODO §3 P0-3）

**状态**：landed（2026-10，e7533db）

NA6 此前冻结旧判定 `unwind_failed = (fr != 0 且 0 帧)`。该判定把"回溯在地址 0
即停"的线程当成成功：dwfl 返回单个 `pc == 0` 哨兵帧（如 io_uring `iou-sqp-*`
内核工作线程），pyprobe 显示一帧即告成功，而 gdb `thread apply all bt` 会打印
`#0  0x0000000000000000 in ?? ()` 后接 `Backtrace stopped: Cannot access memory
at address 0x0`。现解禁并修正：

- **判定**：`unwind_failed` 为真当 `fr != 0 且 0 帧`，**或**结果为单个 `pc == 0`
  的哨兵帧。
- **呈现**（TX5）：`unwind_failed` 时恒在帧行**之后**输出红色
  `Backtrace stopped: Cannot access memory at address 0x0`；无帧时退化为仅有该行
  （与旧行为一致）。lone `pc == 0` 帧**保留**（对齐 gdb 显示 `#0 0x0`）。

这是对 NA6/TX5 复刻约束的有意解禁（记录于本 ADR）；原批 7 差异豁免不再涵盖该项。
NA9 的集成 oracle 不受影响。

### A11. native 符号 LLVM 后缀截断（原 TODO §3 P1-4）

**状态**：landed（2026-10，e7533db）

gdb 展示 LLVM 生成的局部符号时用**短符号名**（截断尾部 `.llvm.<数字>` 判别后缀）。
pyprobe 在**呈现层**（`present/text.py`）施加同一截断：`_shorten_symbol` =
`re.sub(r'\.llvm\.\d+$', '', name)`（如 `run_mod.llvm.1234` → `run_mod`）。

- 仅改文本输出（TX5）；DTO 与 `--json` 保持 `dwfl_module_addrname` 原值
  （数据面完整，与 Python 帧路径缩短同规则——design.md §3.1）。
- 无该后缀的名字（含 `??`）原样保留。

### A12. native 帧源码位置 `at file:line`（原 TODO §3 P1-5）

**状态**：landed（2026-10，e7533db）

对齐 gdb：native 帧在能解析出源码位置时输出 `at <file>:<line>`，否则维持
`from <module>`（互斥，gdb 语义）。

- **解析**：frame_cb 在符号查找后，经 `dwfl_module_getsrc(mod, lookup)` +
  `dwfl_lineinfo(line, …)` 取 `(filename, lineno)`；能力位 gate 于 NA10 的
  `available_symbols`（探测集新增 `dwfl_lineinfo`），缺则跳过、保底 `from module`。
- **数据**：`dto.NativeFrame` 增 `filename: str | None`、`line: int | None`
  （D1 键集与 JS1/JS2 native schema 随之扩展——本 ADR 覆盖 §3「JSON schema
  逐字节兼容」约束的**有意变更**，`--json` 面新增这两个键）。
- **呈现**：TX5 帧行有 source → `  #j  0x<pc16> in <sym> () at <file>:<line>`；
  无 source 但有模块 → `... from <module>`；皆无 → `... in <sym> ()`。
- 仅带 DWARF 行表的模块（如 `-g` 编译的扩展 / uvloop）生效；无调试信息的模块
  与 gdb 一致地只有 `from`。

### A13. Web 监控模式（`pyprobe[web]` extras 与 `web/` 顶层消费者包）

**状态**：accepted（2026-10；批 8 已全落地 8a–8d，后续迭代见 TODO「Web 监控后续迭代」）

新增 web 服务器模式：`pyprobe web -p <pid>` 起 HTTP 服务器监控单个目标进程，
浏览器页面实时展示热点视图（top 信息结构）与**完整线程调用栈**（活跃线程帧链
随推送帧下发，不丢弃——Web 形态相对终端的增强）。决策已确认（2026-10）：

- **定位**：web 不是第五种观测模式，而是与 cli 平级的**顶层消费形态**——复用
  observe 采样模式（Sampler + TopStats）与 dto 数据面；七项不可约能力归属不变
  （web/monitor 是采样能力的消费编排，非新能力包，不违反 §1 建包铁律）。
- **extras 隔离（核心零依赖红线不破）**：`[project.optional-dependencies] web =
  ["fastapi>=0.141.1", "uvicorn>=0.30"]`（最小集，不拉 `fastapi[standard]` 全家
  桶）；核心 wheel 无新运行时依赖。`pyprobe/__init__.py` **不得 import web**
  （否则 ImportError 传染核心包；API1 名单不变），web 只经 `pyprobe.web` 显式
  触达。
- **依赖方向扩展**：`kernel → target → cpython → observe → present → {cli, web}`；
  cli→web 合法（dispatch 消费），web→cli 禁止，任何能力层→web 禁止；web 仅可依赖
  observe 与横切层（dto/errors）——**web→present 禁止**（web 的 JSON 面经
  `dataclasses.asdict` 直出，不复用 CLI 呈现层；present 保持 CLI 呈现语义纯净）。
  结构契约测试机械强制（A8 机制延伸，ST-W 系条目）。
- **批 8b 实施期修订（2026-10-12）**：WS1 的红色 `[!]` 错误行需要色码原语
  （`red`/`should_color`），其唯一归宿自 A8 起在 `present/color.py`——「web→present
  绝对禁止」与 WS1 无法同时满足。窄例外：web 可 import `pyprobe.present.color`
  （仅此一个 present 子模块；文本/JSON 呈现函数 `format_*`/`render_*` 仍禁），
  ST-W1 以白名单机械强制。
- **延迟 import 与缺依赖语义**：cli.py dispatch 分支函数体内 import；web 依赖
  （fastapi/uvicorn 及其传递依赖）缺席 → stderr
  `[!] the web subcommand requires extra dependencies — install with: pip install 'pyprobe[web]'`
  + **rc 2**（对齐 DP5 top 非 tty 的"环境不满足"语义）；pyprobe 自身代码的
  ImportError 不得被该分支吞掉（仅依赖缺席转提示）。
- **目标退出语义**：ProcessExited → 广播 exit 终态事件后采样线程终止；服务器
  **继续运行**（页面保活显示终态）；新 SSE 连接立即收到粘性 exit 帧。
- **前端产物入库**：React + Vite 构建产物（`pyprobe/web/static/`）**git 跟踪**——
  wheel 构建不依赖 Node，README 离线构建承诺不变；CI 前端校验任务仅
  `frontend/**` 变更时触发（产物新鲜度 diff 校验），与主流水线解耦。
- **打包**：package-data 显式列 `web/static/*`、`web/static/assets/*` 两层——
  不用 `**` 递归 glob，维持 `setuptools>=61` 下限（避免连带 `_common.sh` 回退与
  README 离线预装说明的散弹式修改）。
- **安全默认**：`--host` 默认 127.0.0.1（不暴露公网）；`--port` 默认 9470
  （避开 examples/fastapi_app.py 的 8000 与常见开发端口）。
- **权限模型**：与 record/top 相同（`process_vm_readv(2)`，无 ptrace attach）——
  README 权限段随批次落地补一行。

### A14. 文档契约机械化（文档规则 lint 化）

**状态**：landed（2026-10-11）

文档纪律此前全靠文字约定维持（"改 X 时同步 Y"、跨文档随手引用章节号），
已实证失效：AGENTS.md 引用已删除的 TODO 旧 §6.3（typecheck 条目评估结论
"不采用"后引用未更新）、§6 派发包模板引用已删除的 `next.sh`（原 scripts 入口）、
design.md §3 与本文 §4 的函数签名双写后各自漂移（`collect_syscalls` 默认值、
`collect_profile` 的 `sampler_factory` 参数两处不一致）。决策：

- **DOC1–DOC6 检查器**：新增 `scripts/check_docs.sh`（实现
  `scripts/check_docs.py`，零依赖），随 `scripts/lint.sh` 强制执行：
  DOC1 md 相对链接（含 `#锚点`）必须可解析；DOC2 文档引用的 `scripts/*.sh`
  必须存在；DOC3 禁止裸章节号引用 TODO（编号随条目增删漂移，历史指称须写作
  "原 TODO §<n>"；扫描 md + py/sh/yml，docs/red 证据档案豁免）；DOC4 常驻文档
  预算棘轮（AGENTS.md / TODO.md / design.md §2 地图行数基线只降不升）；DOC5
  TODO 未勾条目必须带 `（YYYY-MM` 日期锚；DOC6 代码中回链的契约编号
  （T12、NA11、WM1、ST-W1…）必须在本文档有定义行（检查器规则命名用 DOC 前缀，
  与行为点编号 D1/D2 区分）。
- **消灭同步而非承诺同步**：文档中的"同步"字样即技术债——同一事实只留
  一份，其余放指针；函数签名/字段名单以代码为准（agent grep 现场取，
  不再双写）。
- **生命周期入验收**：§6 派发包模板验收标准补"TODO 勾销并清理尸体"与
  "临时资产登记或删除"；ADR 逐项带状态行（本节已回填）。
- **地图实体化**：design.md §2 收敛为四字段架构地图（路径 | 职责 | 入口
  （文件：符号） | 禁令/依赖）+ "以代码为准，验证于" 末行；仅描述已落地
  结构（未落地形态留在对应 ADR，如 A13），`tests/test_structure.py` 机械
  校验地图条目与文件系统一致。
- **迁移期台账归档**：批 1–6 的"旧测试出处"列与"旧测试标注"节迁至
  `docs/migration-2026-10.md`；行为点编号（T12、WM1…）保留原地——测试
  注释回链不失效。

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

| # | 行为点 | 新测试 |
|---|--------|--------|
| M1 | 读循环直至填满；部分成功继续读 | test_mem.py::test_partial_then_complete |
| M2 | 任何错误返回 None（不抛异常） | test_mem.py::test_error_returns_none |
| M3 | 读到 0 字节返回 None | test_mem.py::test_zero_bytes_returns_none |
| M4 | 短读如实返回（长度由调用方校验） | test_mem.py::test_short_read_returned |
| M5 | EINTR（errno 4）重试 | test_mem.py::test_eintr_retried |
| M6 | length==0 返回 b"" 且无 syscall | test_views.py（视图层拦截，传输层同语义） |

**kernel/views.py**（旧 `memory.py` RemoteReader 缓存 + `_UncachedReader` + `read_uncached` + sampler 手工新建 reader 四机制合并为 A4）

| # | 行为点 | 新测试 |
|---|--------|--------|
| V1 | 单页读命中缓存，第二次同页无 syscall | test_views.py::TestSnapshotView |
| V2 | 不可读页返回 None | 同 |
| V3 | 部分页（映射末尾）不缓存、透传 | 同 |
| V4 | 跨页装配；任一缺失页 → None | 同 |
| V5 | ≥PAGE_SIZE 读绕过缓存 | 同 |
| V6 | LRU 超 CACHE_MAX_PAGES(256) 逐出最旧 | 同 |
| V7 | read_ptr/u32/u64/int 小端解码；短读 → None | 两视图各一组 |
| V8 | LiveView 每次读都走传输层（长时观测一致性，A4）；读粒度为页对齐整页取读后切片（与 SnapshotView 共享装配路径；页内读不跨映射，无正确性差异） | test_views.py::TestLiveView |
| V9 | 常量：PAGE_SIZE=4096 / PTR_SIZE=8 / BYPASS==PAGE_SIZE / CACHE_MAX_PAGES=256 | test_views.py::TestConstants |

**kernel/procfs.py**（旧 `procmeta.py` + `syscall_tracer._list_tids` + `native_dump._read_comm`）

| # | 行为点 | 新测试 |
|---|--------|--------|
| P1 | cmdline NUL→空格、去尾空格、UTF-8 replace 解码；OSError → None | test_procfs.py |
| P2 | list_tids 升序 int 列表；进程缺失 → ProcessNotFound | test_procfs.py |
| P3 | PermissionError → PermissionDenied（**A7 琐碎项修复**：旧树漏捕获，用户见 traceback）；不捕获 ProcessLookupError（旧死代码） | test_procfs.py::test_permission_denied |
| P4 | read_comm 去空白；OSError → "" | test_procfs.py |

**kernel/ptrace.py**（旧 `syscall_tracer.py` 引擎 + `native_dump.py` `_attach_all_threads`/`_detach_all` 合并为 A2）

| # | 行为点 | 新测试 |
|---|--------|--------|
| T1 | seize 全线程 + 重扫循环补抓新线程；options 随 SEIZE 传递 | test_ptrace.py::TestSeize |
| T2 | seize 失败回滚：已 seize 线程全部 DETACH；**A7 P1-4 修复**：任意异常（非仅 AttachFailed）都回滚 | TestSeize::test_rollback_on_any_exception |
| T3 | 主线程 seize 失败 → AttachFailed；非主线程序列中死亡跳过 | TestSeize |
| T4 | stop_all：INTERRUPT 每线程 + 收集停止（死亡线程容忍） | TestSeize |
| T5 | wait_event：syscall-stop → SyscallStop | TestWaitEvent |
| T6 | group-stop（PTRACE_EVENT_STOP）吞咽不转发 | TestWaitEvent |
| T7 | 真实信号经 restart_op data 转发 | TestWaitEvent |
| T8 | 新线程 SIGSTOP delivery-stop 吞咽并注册 tid | TestWaitEvent |
| T9 | SIGTRAP 非事件陷阱转发 | TestWaitEvent |
| T10 | PTRACE_EVENT_EXEC → ExecEvent 返回（消费方处理后 resume） | TestWaitEvent |
| T11 | PTRACE_EVENT_CLONE 父进程直接续行，不产事件 | TestWaitEvent |
| T12 | ECHILD → None；InterruptedError 继续等；Exited **立即返回**并注销 tid（逐停处理语义同旧树 run 循环；机械停在一次 wait_event 调用内吞咽，SyscallStop/ExecEvent/Exited 均一停一返，消费方跨调用推进序列） | TestWaitEvent |
| T13 | **A7 琐碎项**：删除阻塞 waitpid 的 `wpid == 0` 死分支 | （无测试——代码不存在，结构保证） |
| T14 | detach：INTERRUPT + 有界 WNOHANG 收集 + DETACH 每线程；幂等；tids 清空 | TestDetach |
| T15 | getregs 失败 → None | TestGetregs |
| T16 | **A7 P0-2 修复**：arch 检查下沉引擎 `__init__`，aarch64 → UnsupportedArchitecture（feature 名由消费方传入以保消息语义） | test_ptrace.py::test_unsupported_arch |
| T17 | UserRegs x86-64 布局 27 字段 + ARG_REGS=(rdi,rsi,rdx,r10,r8,r9) | TestGetregs |

**errors.py**（旧 `errors.py` 全量——消息是 CLI `[!] {e}` 用户可见输出，逐字复刻）

| # | 行为点 | 新测试 |
|---|--------|--------|
| E1 | 9 子类全部继承 PyProbeError；属性同旧（pid/symbol/exe_path/version/fallback/feature/arch/supported） | test_errors.py |
| E2 | 消息逐字复刻（含默认 detail 文案、SymbolNotFound 无路径省略 "in"、AttachFailed 无 detail 省略冒号） | test_errors.py（消息全 pin） |

**dto.py**（旧 `types.py` 数据部分；A5 剥离 format()/颜色/errno 表/路径缩短——后者批次 6 入 present/text.py）

| # | 行为点 | 新测试 |
|---|--------|--------|
| D1 | 7 个 dataclass 字段名/顺序/默认值同旧（asdict 键集 = JSON schema 复刻约束）；**例外**：`NativeFrame` 增 `filename`/`line`（A12，JSON schema 有意扩展） | test_dto.py（键集 pin） |
| D2 | 无 format()/format_header() 方法；不 import 颜色（A5 守护） | test_dto.py + test_structure.py |

### 批次 2 — `target/`

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `target/identity.py` | CPython 进程识别与版本判定 | `exe_path_of(pid) -> str`；`detect_version(exe_path) -> str`；`decode_py_version(hexval) -> str` | 见 §5.1 批 2 表 |
| `target/layout.py` | A1：resolve_layout / Layout 值对象 / dev override | `Layout(version, key, verified, table)`（`.get`/`.get_or`）；`resolve_layout(version_str, *, overrides_path=None) -> Layout`；`supported_versions()`；`DEFAULT_VERSION` | 见 §5.1 批 2 表 |
| `target/symbols.py` | ELF 符号与运行时结构定位 | `find_symbol(exe_path, name, pid) -> int`；`read_const(exe_path, name, length) -> bytes \| None`；`ElfImage.open(path)`（`.lookup`/`.read_at_symbol`/`.e_type`） | 见 §5.1 批 2 表 |
| `kernel/procfs.py`（增补） | maps 行 → 加载基址（ELF 重定位所需 /proc 知识） | `parse_load_base(maps_text, exe_path) -> int`；`load_base(pid, exe_path) -> int` | P5（见批 2 表） |

#### 批 2 行为点清单（§5.1 实例）

**target/layout.py**（旧 `offsets.py`；A1 核心：消灭 `_active` 全局单例与 `get()` 读路径副作用。契约变更记录：旧 `configure()` 对未验证版本"先填 fallback 后抛 VersionNotSupported"，新 API 不抛——`Layout.verified=False` 标记，警告串由批次 4 session 用 `VersionNotSupported` 消息组装（消息文本复刻已由 errors.py E2 钉住））

| # | 行为点 | 新测试 |
|---|--------|--------|
| L1 | 已验证版本（"3.12.13" 全串与 "3.12" 短串）→ verified=True，table 为该版本表 | test_layout.py |
| L2 | version_key 提取（`_version_key` 私有函数直钉）："3.12.13"→"3.12"、"3.11"→"3.11"、"3"→"3"、""→""；**Layout.key 恒为 backing 表的版本**——verified 取其键、fallback 取 DEFAULT_VERSION、override 命中取 override 版本（无 minor 段的输入不原样保留，防误导诊断） | 同 |
| L3 | 未验证版本 → DEFAULT_VERSION 表、verified=False、requested 版本保留；**不抛异常**（A1 契约变更，见上）；override 命中未验证版本时 verified 仍为 False（旧"raise 照发"语义） | 同 |
| L4 | dev override：`_version` 匹配 → 整表替换（对 verified 与 fallback 基座同规则）；不匹配 → 忽略；`overrides_path` 显式传参 | 同 |
| L5 | get 缺键 → KeyError；get_or 返回 default | 同 |
| L6 | supported_versions 升序、含 3.11–3.14 | 同 |
| L7 | DEFAULT_VERSION == "3.12" | 同 |
| L8 | 表键集独立 oracle（_SHARED_KEYS + _VERSION_EXTRA_KEYS；防表回归而非自指确认；TDD 流程注释保留） | 同 |
| L9 | spec-oracle：内置表[V] == 入库 offsets.json（去 `_version`）当 json._version==V（vs gen_offsets 真实头文件产物，§10.2-1） | 同 |
| L10 | Layout 表不可变（MappingProxy；旧 test_active_is_copy 的"防污染验证表"语义由不可变性彻底保证） | 同 |
| L11 | 多版本 Layout 并存互不干扰（A1 核心收益：per-session 布局，可同时观测不同版本进程） | 同 |
| L12 | override 隔离：默认 overrides_path 可被 conftest 指向不存在路径（§10.2-7 落地——套件验证内置表而非 tracked json） | conftest  autouse fixture + 守护测试 |

**target/identity.py**（旧 `procmeta.decode_py_version` + `process.resolve_process` 的 exe/版本段）

| # | 行为点 | 新测试 |
|---|--------|--------|
| I1 | decode_py_version 位提取（major 24-31 / minor 16-23 / micro 8-15，release level 丢弃） | test_identity.py |
| I2 | spec-oracle：运行中解释器 sys.version_info 重组 hexval 解码回同串 | 同 |
| I3 | exe_path_of：readlink /proc/pid/exe；OSError → ProcessNotFound(pid)（from 链保留） | 同 |
| I4 | detect_version：read_const Py_Version 8 字节小端解码；读不到 → "?" 哨兵（ProcessInfo.python_version 默认值语义来源） | 同 |

**target/symbols.py**（旧 `elf.py` 全量）

| # | 行为点 | 新测试 |
|---|--------|--------|
| S1 | ELF 校验：非 ELF → ValueError("not an ELF file")；非 ELF64 → ValueError("not ELF64") | test_symbols.py（合成 ELF） |
| S2 | ehdr 字段定位：e_type@16 / e_shoff@40 / e_shnum@60 / e_shstrndx@62 | 同 |
| S3 | 符号搜索序：.symtab（SHT_SYMTAB=2）先于 .dynsym（SHT_DYNSYM=11） | 同 |
| S4 | 符号类型过滤：仅 STT_NOTYPE(0)/STT_OBJECT(1) 参与匹配 | 同 |
| S5 | ET_DYN(3) → st_value + load_base（/proc/pid/maps 首个匹配映射，含 " (deleted)" 后缀）；ET_EXEC 不加 | 同 |
| S6 | 找不到 → find_symbol 返回 0 | 同 |
| S7 | read_const：file_off = st_value − sh_addr + sh_offset；**以节剩余字节（sh_size）为界**，越界/短读/缺符号 → None（批次 2 验收澄清：旧树仅 EOF 界，节后会读到节头垃圾——新语义更严且消费方 Py_Version 8 字节不受影响） | 同 |
| S8 | 解析缓存：(path, mtime) 键，二次调用不重读文件 | 同 |
| S9 | spec-oracle：真实解释器 _PyRuntime 非 0；Py_Version 8 字节解码 == platform 版本 | 同 |

**kernel/procfs.py 增补**（旧 `elf._get_load_base` 的 /proc 侧——maps 行解析属 procfs 职责）

| # | 行为点 | 新测试 |
|---|--------|--------|
| P5 | parse_load_base：取**首个** path 匹配行（含 " (deleted)" 后缀）的起始地址 hex 解码；无匹配 → 0；load_base 包装 /proc/pid/maps 读取，OSError → 0；**字面匹配，调用方负责提供内核解析后的路径**（生产路径来自 /proc/pid/exe readlink，天然已解析；批次 2 验收驳回 realpath 内建——路径解析策略不入 kernel 层） | test_procfs.py 增补 + 真实 /proc/self/maps spec 测试 |

### 批次 3 — `cpython/`

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `cpython/objects.py` | PyLong/Unicode/Bytes typed reader | `read_long(view, layout, addr) -> int \| None`；`read_bytes(...) -> bytes \| None`；`read_unicode(...) -> str \| None`；`read_name(...) -> str \| None`；`MAX_STR_LEN` | 见 §5.1 批 3 表 |
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

| # | 行为点 | 新测试 |
|---|--------|--------|
| O1 | read_long 3.12+：lv_tag 高位包 digit 数（size=tag>>3）；tag 读失败 → None | test_objects.py（版本矩阵 3.12–3.14） |
| O2 | read_long 3.11：ob_size 为普通计数（get_or 回退链 long_value.ob_digit→ob_digit；lv_tag 缺席走此分支） | 同（3.11） |
| O3 | size==0 → 0；size==1 → 单 digit（digit 读失败传播 None——旧 L44-45 语义） | 同 |
| O4 | size==2 → d0 \| d1<<30；任一 digit 失败 → None | 同 |
| O5 | size>2 → None（超出支持范围，不猜值） | 同 |
| O6 | read_bytes：ob_size 上界 MAX_STR_LEN；数据读失败 → None；**A7 琐碎项落地：删除旧 `size < 0` 死检查**（read_u64 无符号） | 同 |
| O7 | read_unicode：ASCII 头短读 → None；length<0 或 >MAX_STR_LEN → None | 同 |
| O8 | state 字节位布局：compact=bit5、is_ascii=bit6、kind=bits2-4 | 同（各变体覆盖） |
| O9 | compact+ascii：数据 addr+ascii_sz，ascii/replace 解码 | 同 |
| O10 | compact 非 ascii：数据 addr+compact_sz；kind→(latin-1,1)/(utf-16-le,2)/(utf-32-le,4)；未知 kind → None | 同（kind 矩阵） |
| O11 | 非 compact：data_any 指针；NULL → None；按 kind 解码 | 同 |
| O12 | read_name（显示名严格读取）：read_unicode 成功后做内容可信度校验——含不可打印字符（isprintable() False，覆盖 C0/C1/DEL）或 U+FFFD（replace 有损解码痕迹）→ None；键比较场景仍用 read_unicode。动因：无暂停采样撞上帧栈中间态时，回收复用内存可恰好解析为合法 PyUnicode 头，O9 的 replace 解码将二进制垃圾放行为「函数名」进入 top/record 统计（4 并发压测实测，乱码进面板即不可用）；消费方：frames co_name/co_filename（并入 F6 不可读判定）与 names 线程名（N6） | test_objects.py 增补 + test_frames/test_names 联动 |

**cpython/dicts.py**（旧 `dict_iter.py`；`DictReader(view, layout)` 绑定布局）

| # | 行为点 | 新测试 |
|---|--------|--------|
| DI1 | from_dict：ma_keys==0 → False；dict 头不可读 → False | test_dicts.py |
| DI2 | keys 头解析：dk_log2_index_bytes/dk_kind/dk_nentries；kind==0 → KeyEntry(24B, key_off=8)，否则 UnicodeEntry(16B, key_off=0) | 同（版本矩阵） |
| DI3 | entries 基址 = keys_addr + (1<<dk_log2) + dictkeysobject_size；整块预读（0<total≤MAX_STR_LEN，否则 b""；读失败 → b""） | 同 |
| DI4 | next：跳过 k==0 空洞；split（values≠0）时 v=read_ptr(values+idx*8)，失败跳过；耗尽 → None | 同 |
| DI5 | from_managed_values：ht_cached_keys 读失败/为 0 → False；成功路径 values 来自独立数组 | 同 |

**cpython/code.py**（旧 `linetable.py` + stack_dump 内 CodeObject 头读取；纯/侧效分离是 spec-oracle 前提）

| # | 行为点 | 新测试 |
|---|--------|--------|
| C1 | 纯函数 line_for_offset：PEP 626 解码——首字节 bit7 为条目标记；code=(b>>3)&15；15→无行号（ar_line=-1）；13/14→CPython signed varint（6 位块、bit6 续位；奇 uval 取 -(uval>>1)、偶取 uval>>1，**非 zigzag**——scan_signed_varint 权威语义，批次 3 验收经 C7 oracle 实证修正措辞）；10→+0、11→+1、12→+2；ar_end += ((b&7)+1)*2；续行字节（bit7==0）跳过 | test_code.py |
| C2 | lasti 超出表末 → 最后计算行（循环 ar_end<=lasti 终止语义） | 同 |
| C3 | 无行号条目/空表 → firstlineno 回退 | 同 |
| C4 | addr2line 包装：co_linetable 指针或 bytes 读失败 → firstlineno | 同 |
| C5 | read_code_header：co_firstlineno/co_filename/co_name 跨度合并读归本模块（A3：frames 不见 co_lo/co_hi 心算）；读失败 → None | 同 |
| C6 | lasti = prev_instr − (code+co_code_adaptive)，负值钳 0（旧 stack_dump L100-102；配合 C1 对 lasti=0 的定义行为） | 同 |
| C7 | **spec-oracle**：line_for_offset vs CPython `co_lines()` 地面真值——真实函数集（多行/无行号事件/嵌套）全指令偏移逐点一致（§10.2-1） | 同 |

**cpython/frames.py**（旧 `stack_dump.collect_frames`；返回 dto.FrameInfo）

| # | 行为点 | 新测试 |
|---|--------|--------|
| F1 | 帧字段簇单次合并读（f_code/previous/prev_instr，帧宽=prev_instr_off+8 由 layout 导出）；不可读 → 终止 | test_frames.py（版本矩阵） |
| F2 | frame_addr==0 终止；MAX_FRAMES=100 上限（环形链防护） | 同 |
| F3 | f_code==0 → 跳过续走 previous | 同 |
| F4 | trampoline_addr≠0 且 f_code==trampoline → 跳过；trampoline==0 不跳过 | 同 |
| F5 | code 头读失败 → 终止（截断已收集帧） | 同 |
| F6 | name/filename 地址为 0 → None；两者皆 None → 陈旧尾帧终止（3.13 datastack 残留教训，注释保留）；批 15 起 name/filename 经 read_name（O12）读取——乱码内容同计不可读，「两者皆乱码」并入本条终止 | 同 |
| F7 | 返回 FrameInfo 列表，内层在前 | 同 |
| F8 | 行号经 code.addr2line（lasti 钳位 C6） | 同（含行号断言） |
| F9 | co_name 形态校验（O12 之后第二道）：read_name 通过但内容既非标识符（isidentifier()）也非编译器 "<...>" 特殊形态（<lambda>/<module>/<listcomp> 等）→ 按不可读处理。动因：latin-1 可打印垃圾是 O12 唯一拦不住的类（无控制字符、无 U+FFFD），批 15 E2E 复现的最后一个乱码样本（'ÿ...'，1917 中 1 例）即此形态；真实 co_name 必为二者之一（Python 源码标识符或编译器生成），误伤面仅剩第三方 C 代码以怪名创建 code object 的罕见情形 | test_frames.py 增补 |

**cpython/runtime.py**（旧 `stack_dump.read_thread_chain`/`collect_thread` 的帧指针解析段 + `process.resolve_process` 的解释器/蹦床段）

| # | 行为点 | 新测试 |
|---|--------|--------|
| R1 | threads.head 读失败 → NoThreadState("failed to read threads.head")（消息复刻） | test_runtime.py |
| R2 | 链走：tstate 字段簇合并读（ts_lo..ts_hi 由 layout 导出）；不可读 → 截断返回已收集 | 同 |
| R3 | ThreadStateRef(tstate_addr, thread_id, native_tid)；MAX_THREADS=256 上限；next==0 终止 | 同（含上限） |
| R4 | current_frame_of：layout 探针 ThreadState.current_frame 直达（3.13/3.14）vs cframe→CFrame.current_frame 间接（3.11/3.12）；cframe==0 → 0 | 同（版本分支成对） |
| R5 | resolve_interpreter：main→head 回退；均失败 → NoInterpreterState() | 同 |
| R6 | resolve_trampoline：get_or 缺席 → 0；读失败 → 0（3.12 专有键） | 同（3.12 有值/3.13 为 0 成对） |

**cpython/names.py**（旧 `thread_names.py` 全量；任何失败 → 部分/空 dict，不抛）

| # | 行为点 | 新测试 |
|---|--------|--------|
| N1 | sys.modules 定位：3.12 走 InterpreterState.imports+_import_state.modules 直针；其余版本走 sysdict 字典找 "modules" 键 | test_names.py（版本分支成对） |
| N2 | modules 字典找 "threading" 模块；找不到 → 空 dict | 同 |
| N3 | 模块 __dict__：ob_type→tp_dictoffset→value+dictoffset；dictoffset==0/读失败 → 空 | 同 |
| N4 | "_active" 字典：键 read_long → tid；读失败跳过 | 同 |
| N5 | 实例字典解析（MANAGED_DICT 分支矩阵）：3.11 obj-3 dict 槽 / obj+pre_values 非标记 values；3.12 tagged&1 内联 values；3.13/3.14 tagged==0 → 对象内嵌（PyObject_size+dictvalues_header）；3.12 tagged==0 → False（dictvalues_header=0）；非 managed → tp_dictoffset | 同（3.11/3.12/3.13 三分支 + 非 managed） |
| N6 | "_name" 属性 read_name（O12：乱码内容按不可读）→ names[tid]；None 跳过 | 同 |

### 批次 4 — `observe/`（首个端到端里程碑：snapshot 对 live target 出栈）

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `observe/session.py` | 观测会话：进程存活期不变量一次性解析 + view 工厂注入 | `Session`（pid/exe_path/runtime_addr/interp_addr/trampoline_addr/layout/proc_info/names/version_warning/view_factory）；`open_session(pid, *, view_factory=None) -> Session` | 见 §5.1 批 4 表 |
| `observe/snapshot.py` | 单次快照栈采集 + 空闲双启发式 | `collect_snapshot(session) -> list[dto.ThreadInfo]`；`build_thread(...)`；`is_thread_idle_by_stat(pid, tid)`；`is_thread_idle_by_frames(frames)` | 同上 |
| `observe/sampling.py` | 采样器（热路径） | `Sampler(pid, *, view_factory=None)`（`.sample()`/`.refresh_names()`；`.session`；`.proc_info`——批次 6 验收补登：dump 层消费面，旧树同款公开属性） | 同上 |
| `observe/profile.py` | record 折叠栈聚合 | `fold_key(thread) -> str`；`collect_profile(pid, *, rate=50, duration=None, sampler_factory=Sampler) -> dto.ProfileData` | 同上 |
| `observe/topstats.py` | top 增量聚合（A7 P0-1 修复；纯数据，render 归 present） | `TopStats`（`.update(threads)`；状态：own/total/samples/idle_samples/current/idle_threads） | 同上 |
| `kernel/procfs.py`（增补） | /proc stat 状态字段 | `read_stat_state(pid, tid) -> str \| None` | P6（见批 4 表） |

A4 一致性声明落点：session 不持有 view，只持 `view_factory`；每次快照/采样新建
SnapshotView（单次快照语义），长时追踪（批次 5 syscalls）用 LiveView——每种观测
声明自己的一致性需求。

#### 批 4 行为点清单（§5.1 实例）

**observe/session.py**（旧 `process.py` resolve_process/ProcessSession；A1 警告组装落点）

| # | 行为点 | 新测试 |
|---|--------|--------|
| SE1 | open 流程：exe_path_of → find_symbol(_PyRuntime)（0 → SymbolNotFound("_PyRuntime", exe_path)）→ detect_version → resolve_layout → interp/trampoline/names/proc_info | test_session.py |
| SE2 | version_warning 组装（A1 契约变更的承接，**逐字复刻**）："?" → "[!] Warning: cannot determine target CPython version, using default offsets — output may be incorrect."；未验证 → "[!] " + VersionNotSupported 消息；已验证 → None | 同（两种文案 pin） |
| SE3 | read_cmdline 为 None → proc_info.cmdline 回退 exe_path | 同 |
| SE4 | Session 携带 layout 与 view_factory（A1/A4 落点）；存活期不变量一次性解析 | 同 |
| SE5 | view_factory 注入点（测试）；默认 SnapshotView(Transport(pid)) | 同 |
| SE6 | interp 经 runtime.resolve_interpreter（R5）、trampoline 经 resolve_trampoline（R6）、names 经 cpython.names（N 系） | 同（组合测试） |

**observe/snapshot.py**（旧 `stack_dump.py` collect_python/_collect_threads_from_session/collect_thread/idle 启发式）

| # | 行为点 | 新测试 |
|---|--------|--------|
| SN1 | collect_snapshot(session)：每次调用新建 SnapshotView（A4）；NoThreadState 原样传播（快照语义；ProcessExited 转换是 sampling 的策略 SA2，两层不混） | test_snapshot.py |
| SN2 | names 按 thread_id 贴名；native_tid 升序（复刻约束 线程升序） | 同 |
| SN3 | build_thread：current_frame_of → walk_frames → ThreadInfo；idle_hint=True → 空帧 idle ThreadInfo 不走帧遍历（采样剪枝入口）；thread_id 传递 | 同 |
| SN4 | **空闲双启发式**（复刻约束）：by_stat（/proc stat state != "R" → idle；OSError/IndexError → False 保守——旧 docstring 语义）+ by_frames（wait+threading.py / select+selectors.py / poll+(asyncore\|zmq\|gevent\|tornado)；filename None → False；仅查顶帧） | 同 |
| SN5 | by_stat 经 procfs.read_stat_state（P6 增补：stat 末 ")" 后字段；OSError/解析失败 → None） | 同（P6 测试随 procfs） |

**observe/sampling.py**（旧 `sampler.py`）

| # | 行为点 | 新测试 |
|---|--------|--------|
| SA1 | sample() 每次新建 view（A4：跨样本复用会供陈旧数据） | test_sampling.py |
| SA2 | NoThreadState → ProcessExited(pid) from e（目标退出语义化） | 同 |
| SA3 | stat-idle 线程 idle_hint 剪枝不走帧遍历（热路径性能守护） | 同 |
| SA4 | native_tid 升序 + names 按 thread_id 贴名 | 同 |
| SA5 | sample() 不刷新 names（record 聚合键稳定性）；refresh_names() 显式刷新并回写 session.names | 同 |
| SA6 | 空线程链 → [] | 同 |

**observe/profile.py**（旧 `record.py` collect_profile/_fold_key；format/dump 归批次 6）

| # | 行为点 | 新测试 |
|---|--------|--------|
| PF1 | fold_key：name → `"name"`，无名 → `tid-<native_tid>`；帧反转 leaf→root 成 root→leaf；None 帧名 → "?"（复刻约束 folded 键形） | test_profile.py |
| PF2 | 绝对时间调度（next += interval 不漂移；落后 >1 interval 重置基线不补发） | 同 |
| PF3 | idle 线程计 idle_samples 不入 counts；active 入 counts+samples | 同 |
| PF4 | KeyboardInterrupt/ProcessExited 吸收 → 部分 ProfileData（elapsed/version_warning 保留） | 同 |
| PF5 | duration=None 采样至中断；有 duration 到点停止 | 同 |

**observe/topstats.py**（旧 `top.py` TopStats；**A7 P0-1 修复落地处**；render 归 present 批次 6，A7 P1-8 "?" 归一随之）

| # | 行为点 | 新测试 |
|---|--------|--------|
| TS1 | own=叶子帧名计数（None→"?"归一——update 侧已有）；total=栈内去重名计数 | test_topstats.py |
| TS2 | **A7 P0-1 修复**：idle 守卫查 idle_threads 去重（旧误查 current——idle 线程永不入 current，同 tid 每周期重复 append 无限膨胀）；idle 线程同时从 current 移除（曾活跃转 idle 不带陈旧帧留 Active 表） | 同（同 tid 连续 idle 两次不重复 + active→idle 转换两条守护） |
| TS3 | active：从 idle_threads 移除；samples++；current[tid]=(top\|None, name) | 同 |
| TS4 | 空帧 active：current[tid]=(None,name)，samples 计数 | 同 |
| TS5 | TopStats 纯数据状态，无 render（A5；渲染与 A7 P1-8 "?" 归一归 present 批次 6） | test_structure.py 机械强制 |

**kernel/procfs.py（增补）**（旧 `stack_dump` by_stat 内联 /proc stat 解析的归属）

| # | 行为点 | 新测试 |
|---|--------|--------|
| P6 | read_stat_state：读 `/proc/<pid>/task/<tid>/stat`，取末 ")" 后首字段（进程状态字符）；OSError / 解析失败 → None（供 SN5 by_stat 空闲判定） | test_procfs.py |

**端到端里程碑（§10.2-4 首批）**

| # | 行为点 | 新测试 |
|---|--------|--------|
| E2E1 | target fixture：子进程经 stdout 就绪行握手（**就绪轮询替代固定 sleep，§10.2-7 子项落地**），finally terminate+wait(5)；TARGET_PYTHON 跨版本机制保留 | tests/conftest.py + targets/target_app.py |
| E2E2 | open_session 对 live target：版本已验证、warning 为 None、interp 非 0 | test_session_snapshot.py（integration） |
| E2E3 | collect_snapshot 对 live target：≥2 线程、bg-worker 名解析、目标函数名/文件名出现在帧、native_tid 升序、主线程 idle | 同（integration，≤15s 预算） |

### 批次 5 — `observe/`（ptrace 消费方）

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `observe/syscalls.py` | syscall 追踪（消费批 1 PtraceEngine）+ 参数解码 + TraceFilter（A7 syscall 修复） | `collect_syscalls(pid, *, trace=None, max_events=None, verbose=False) -> list[dto.SyscallEvent]`；`TraceFilter`；渲染助手（escape_bytes/truncate_escaped/render_str_arg 等，STR_MAX=32） | 见 §5.1 批 5 表 |
| `observe/syscall_abi.py` | x86-64 syscall ABI 数据（批次 5 契约增补——旧 syscall_table.py 的归属；号表/flags/DECODE/TRACE_GROUPS 权威来源为系统头文件，逐字复刻） | `SYSCALL_NAMES`/`SYSCALL_NRS`/`OPEN_FLAGS`/`MAP_FLAGS`/`PROT_FLAGS`/`DECODE`/`TRACE_GROUPS` | 同上 |
| `observe/native.py` | native 回溯（libdw/libdwfl，共享批 1 ptrace 引擎——A2 迁移：ATTACH→SEIZE） | `collect_native(pid) -> list[dto.NativeThreadInfo]` | 同上 |

#### 批 5 行为点清单（§5.1 实例）

**observe/syscalls.py**（旧 `syscall_tracer.py` 消费侧 + `syscall_render.py` 采集期解码 + `types.SyscallEvent` 渲染前提；format_summary/format_syscalls_json 归批次 6）

| # | 行为点 | 新测试 |
|---|--------|--------|
| SY1 | collect_syscalls：PtraceEngine(feature="syscall tracing") seize(TRACESYSGOOD\|TRACECLONE\|TRACEEXEC) → stop_all → restart_all → wait_event 循环 → finally detach；arch 检查经引擎（T16 已落地，CLI/库同径——A7 P0-2 的验证点） | test_syscalls.py |
| SY2 | entry/exit 配对：stash[tid]=(nr, args, t_entry)；syscall stop → getregs（失败跳过并 resume）；entry：nr=orig_rax&0xFFFFFFFF、args=6 寄存器（**全显**，复刻约束）；exit：配对发射 | 同 |
| SY3 | emit：name=SYSCALL_NAMES 查表（未知 → "sys_<nr>"）；ret >0xFFFFFFFF00000000 → 减 1<<64；-4096<ret<0 → error=-ret、ret=-1；args 存 &0xFFFFFFFFFFFFFFFF；rendered=entry 解码 + exit 填充 | 同 |
| SY4 | 参数读取经 LiveView（A4：长时追踪不缓存——旧 _UncachedReader 语义） | 同（类型断言守护） |
| SY5 | ExecEvent：stash 中 nr==execve → 补发 exit 事件（ret=0，elapsed 计时），**走用户过滤器 flt（A7 P1-5 修复：旧用 TraceFilter("") 全匹配，-e trace=!execve 也输出）**；处理后 resume | 同（过滤守护对：!execve 不输出 / 默认输出） |
| SY6 | Exited：主线程退出结束循环；wait_event None（ECHILD）结束循环 | 同 |
| SY7 | max_events 到量即停 | 同 |
| SY8 | **TraceFilter（A7 P0-3 契约变更）**：空 spec → 全追踪；纯排除（"!a,!b"）→ 除 exclude 外全；混合（"file,!openat"）→ **include − exclude**（旧并入 include 致语义反转，实测复现）；组展开（TRACE_GROUPS）；未知名静默不匹配；空白容忍 | 同（新增混合语义对） |
| SY9 | 参数渲染（复刻约束）：escape（可打印保留、" 与 \ 转义、其余 \NNN 八进制）；32 字符截断 + "..."（verbose 不截）；cstr 读取（chunk 256 / 上限 4096 / 全不可读 → 0x 地址 / 边缘映射二分 _read_available）；timespec "{sec, nsec}"；AT_FDCWD 识别（fd/dirfd 以寄存器**无符号**值与魔数 0xFFFFFFFFFFFFFF9C 直比，其余 `str(value)`——**不做 32 位符号扩展**）；**addr/rem 无特殊指针渲染**（走「int 范围 str 否则 hex」同一标量规则，不得渲染为 hex/NULL——批 7 对齐门禁回退批 5 引入的顺手优化）；OPEN/MAP/PROT flags OR 解码 + 残余 hex；mode 八进制；signal SIG<n>；int 范围 str 否则 hex；未识别 syscall 全 hex | 同（新增 fd/addr/rem 复刻守护） |
| SY10 | exit 填充（**A7 P1-6/P1-7**）：buf_in/buf_out 拼接 0xaddr/"内容"（min(ret,4096) 字节）；**ret==0 且 buf_in → 渲染空串 ""**（P1-7：旧按 32 字节读内核未写的陈旧缓冲区；buf_out 的 ret 是状态码不受影响）；ret<0 分支不存在（死代码，契约删除）；**timespec_out 于 exit 重读并替换该段**（P1-6：旧 entry 读到调用前陈旧值——strace 在 exit 读） | 同（新增 timespec_out 重读与 ret==0 空串守护） |
| SY11 | **spec-oracle**：SYSCALL_NAMES 全量 vs /usr/include/x86_64-linux-gnu/asm/unistd_64.h 解析（§10.2-1；头文件缺失 skip）；**按交集校验**——头文件已知的号名号须一致，容忍内核头文件与表两向版本漂移；OPEN/MAP/PROT flags 抽样 vs fcntl.h/mman.h | 同 |
| SY12 | **spec-oracle**：渲染样例 vs strace 真实输出（§10.2-1；ptrace_scope=1 下 strace 包裹同一程序的独立实例——兄弟进程非祖先后不可 attach）。**归一化的是文档化差异而非行为差异**（§10.3 复刻约束）：pyprobe 全显 6 参数、timespec 无名花括号、常量数值渲染、NULL 指针走标量 `0`；strace 按真实元数、{tv_sec=…} 具名、常量名解码、指针作 `NULL`——归一化规则在测试中（§10.7：oracle 归一化属主代理资产） | 同（integration） |
| SY13 | 集成：collect_syscalls 对 fast-syscall target（0.2s 级 nanosleep，**§10.2-7 fast target 子项落地**）捕获 clock_nanosleep 且 elapsed≥0.1 | 同（integration，≤15s 预算） |
| SY14 | **批次 6 契约增补**：`collect_syscalls(..., on_event=None)`——发射即回调（dump 层流式输出与 KeyboardInterrupt 部分结果的通道；默认 None 不改变批 5 语义） | test_syscalls.py 增补 + DP3 |

**observe/native.py**（旧 `native_dump.py` collect 侧；format/dump 归批次 6；A2 迁移使 native 行为有变，§10.5-1 的 `stack --native --json` 旧新差异对比为其强制验证项（批 7 出口））

| # | 行为点 | 新测试 |
|---|--------|--------|
| NA1 | libdw 加载（A9/P0-1）：依次 `libdw.so.1` → `libdw.so` → `find_library("dw")`，首个可加载者胜出、全败抛最后 `OSError`；随后装配全量函数签名。有 `libdw.so.1` 时与旧树一致 | test_native.py::TestLibdwLoad |
| NA2 | dwfl 回调装配（find_elf/find_debuginfo/section_address/debuginfo_path）；dwfl_begin 失败 → AttachFailed(pid, "dwfl_begin failed: …") | 同 |
| NA3 | dwfl_linux_proc_report/attach 失败 → AttachFailed(pid, strerror) | 同 |
| NA4 | 帧回调：pc 经 dwfl_frame_pc（非激活帧 lookup=pc−1）；符号 dwfl_module_addrname（缺 → "??"）；模块路径 dwfl_module_info mainfile | 同（stub dwfl 守护） |
| NA5 | 单线程帧上限 _MAX_FRAMES=256 | 同 |
| NA6 | 线程回调：tid/comm（procfs.read_comm）；unwind_failed 判定（**A10 修正**）：`fr != 0 且 0 帧` **或** lone `pc == 0` 哨兵帧 → True | test_native.py::TestFrameMapping::test_unwind_failed_flag |
| NA7 | 线程结果按 tid 升序 | 同 |
| NA8 | **A2 迁移**：PtraceEngine(feature="native stack dump") seize(options=0) → stop_all → dwfl（assume_ptrace_stopped=True）→ finally detach；不再使用 PTRACE_ATTACH | 同（引擎调用序列守护） |
| NA9 | 集成：collect_native 对 live target 出 native 帧（libdw 缺失/权限不足时 skip——旧 test_collect_native_or_skip 语义） | 同（integration） |
| NA10 | 可选符号探测（A9/P0-2，A12 扩展）：`available_symbols` = 加载库实际导出的 `dwarf_getscopes`/`dwfl_module_getsrc`/`dwarf_cfi_addrframe`/`dwfl_lineinfo` 子集，`_init_libs` 经 `_load_libdw`+`_probe_optional` 装配；缺失不致命（保底 NA4 符号+回溯）。消费者：NA11（getsrc/lineinfo 作能力位） | test_native.py::TestOptionalSymbolProbe + TestInitLibs |
| NA11 | 源码位置（A12/P1-5）：frame_cb 经 `dwfl_module_getsrc`+`dwfl_lineinfo` 取 `(filename, lineno)` 填入 `NativeFrame.filename/line`；gate 于 `available_symbols`，缺/无 DWARF → 两者 None（保底 `from module`，NA4） | test_native.py::TestFrameMapping + 集成 |

### 批次 6 — `present/` + `cli` + 打包

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `present/color.py` | 颜色策略（A6 保留，clicolors 规则；仅 present/cli 可引用，A8 细化） | `RESET/BOLD/DIM/RED/GREEN/YELLOW/CYAN`；`yellow_bold/green/cyan/dim/red(text, color=False)`；`should_color(stream)` | 见 §5.1 批 6 表 |
| `present/text.py` | 全部文本格式化（A5 落地；旧 types.format()、stack_dump/native_dump/record/top/syscall_render 的 format 侧全收） | `format_frame/frame 等帧/线程/进程头`、`format_process`、`format_native`、`format_syscall_event`、`format_summary`（+`SyscallStat`）、`format_folded`、`render_top(stats, proc_info, *, elapsed, color, top_n)`；`HIDE_CURSOR/SHOW_CURSOR/CLEAR_SCREEN`；`_shorten_path`；errno 名/描述表 | 同上 |
| `present/jsonout.py` | JSON 输出（schema 复刻约束） | `format_process_json`、`format_native_json`、`format_syscalls_json` | 同上 |
| `present/dumps.py` | dump_* 编排（批次 6 契约增补：collect + 颜色决策 + 打印 + 退出码 + 版本警告 surfacing；cli 保持纯 argparse 薄壳） | `dump_python/dump_native/dump_syscalls/dump_record/dump_top` | 同上 |
| `cli.py` + `__main__.py` | argparse 薄壳（A6 保留） | `build_parser()`；`main(argv=None) -> int` | 同上 |
| `__init__.py` + `offsets.py` facade | 库 API 面（§10.5-2 预落地：__all__ 名单一致 + resolve_layout 例外） | 旧 `__all__` 全名（绑定新实现）+ `resolve_layout` | 同上 |

#### 批 6 行为点清单（§5.1 实例）

**present/color.py**（旧 `colors.py` 逐字语义）

| # | 行为点 | 新测试 |
|---|--------|--------|
| CL1 | color=False 恒等返回（非着色路径与无颜色输出逐字节一致） | test_color.py |
| CL2 | 五色包装：yellow_bold=BOLD+YELLOW（PID/TID）、green（函数/符号）、cyan（文件/模块）、dim（行号/PC/idle 标记）、red（错误行）；空串照包 | 同 |
| CL3 | should_color 优先级（clicolors spec）：CLICOLOR_FORCE≠"0"→True（压 NO_COLOR/非 tty）；NO_COLOR 非空→False（空串不失效）；非 tty→False；TERM=dumb→False；CLICOLOR=="0"→False；否则 True | 同 |

**present/text.py**（A5 落地——旧 `types.py` format 方法 + `stack_dump.format_process` + `native_dump.format_native` + `record.format_folded` + `top.TopStats.render` + `syscall_render.format_summary`；errno 表与 _shorten_path 自 types.py 迁入）

| # | 行为点 | 新测试 |
|---|--------|--------|
| TX1 | 帧行：`  #<i> <name\|?> (<filename\|?>:<line>)`；非 verbose 路径末 2 级缩短（**复刻约束 Python 帧末 2 级**）、verbose 全路径；≤2 级不动；颜色包装点位 | test_text.py |
| TX2 | 线程块：`Thread <tid>[ (idle)][: "name"]` + 帧行；无帧 → `  (no Python frame — thread may be in C code or idle)`；颜色点位 | 同 |
| TX3 | 进程头：`Process <pid>: <cmdline>\nPython v<ver> (<exe>)\n` | 同 |
| TX4 | format_process：进程头 + 逐线程块 + 空行分隔 | 同 |
| TX5 | native 线程：`Thread <i> (LWP <tid>) "<comm>":`；帧 `  #<j>  0x<pc16> in <sym> ()`，有源码位置 → ` at <file>:<line>`（A12）、否则有模块 → ` from <module>`；非 verbose 模块 basename（**复刻约束 native basename**）、符号尾部 `.llvm.\d+` 截断（A11）；unwind_failed → 帧行之后 red "Backtrace stopped: Cannot access memory at address 0x0"（无帧时仅有该行）；lone pc=0 帧保留（A10） | 同 |
| TX6 | syscall 事件行：`<tid>  <name>(<rendered>) = <ret>`；error → `= -1 <ENAME> (<edesc>)`（未知 errno → ERRNO_<n>/Unknown error）；elapsed 非 0 → ` <0.000123>`（**复刻约束 elapsed 格式**）；errno 名/描述表自旧 types.py 逐字 | 同 |
| TX7 | format_summary：表头列 `syscall/calls/errors/total/total/s/per-call`（**复刻约束 total/s 列**）、82 连字符分隔、按 total_time 降序、总计行；SyscallStat 累加器 | 同 |
| TX8 | format_folded：计数降序 + 键升序确定性排序；空 → ""；单行尾换行 | 同 |
| TX9 | render_top：进程头 rstrip + `Elapsed <t>s \| <n> samples (idle <m>)` + Active 表（OWN% 按顶帧计）+ Idle threads 行 + Top functions 表（own%/total%/time 列、top_n 截断）；**A7 P1-8 修复：无名顶帧 OWN% 以 "?" 归一查表（旧用 frame.name=None 查询恒 0）** | 同（新增无名帧 OWN% 非 0 守护） |
| TX10 | 终端控制序列：HIDE_CURSOR/SHOW_CURSOR/CLEAR_SCREEN 逐字 | 同（常量 pin） |

**present/jsonout.py**（旧三处 json 输出合并；schema 复刻约束）

| # | 行为点 | 新测试 |
|---|--------|--------|
| JS1 | 三函数均 `json.dumps(asdict, indent=2, ensure_ascii=False) + "\n"`；键集 = dto asdict（D1 已 pin 的数据面，此处 pin 包装形态） | test_jsonout.py |
| JS2 | format_process_json：{"process":…,"threads":[…]}；format_native_json：process 仅 pid/cmdline（无 Python 元数据——旧注释语义）、帧键集含 `filename`/`line`（A12）；format_syscalls_json：{"events":[…]} | 同 |

**present/dumps.py**（旧各 dump_* 编排段；collect/format/dump 三层契约的 dump 层）

| # | 行为点 | 新测试 |
|---|--------|--------|
| DP1 | 错误打印：`[!] <err>` 经 red 到 stderr，rc=1；颜色按流独立判定（stdout/stderr 各自 should_color；--color 强制时两流一致） | test_dumps.py |
| DP2 | dump_python：版本警告先于线程收集打印（警告是后续失败根因，不得被提前 return 吞掉——旧 stack_dump L338-342 语义）；JSON 时无警告色、走 jsonout | 同 |
| DP3 | dump_syscalls：attach 失败捕获（AttachFailed/ProcessNotFound/UnsupportedArchitecture）→ rc=1；流式打印（非 summary/json 时逐事件 ev 行）；KeyboardInterrupt 仍出 summary；json 优先于 summary 且永不着色 | 同 |
| DP4 | dump_record：folded 文本只去 stdout（或 -o 文件），进度/摘要 `[i] pyprobe recorded …` 只去 stderr（管道洁净，复刻约束）；版本警告打印；目标错误 rc=1 | 同 |
| DP5 | dump_top：非 tty → 提示 + **rc=2**（非交互无降级，README 承诺）；正常退出（Ctrl-C/ProcessExited）rc=0 且恢复光标（finally SHOW_CURSOR）；启动即渲染首屏（不等首个 interval）；摘要 `[i] pyprobe top: …` 到 stderr | 同 |

**cli.py**（旧 `cli.py` 逐字结构；A6 薄壳）

| # | 行为点 | 新测试 |
|---|--------|--------|
| CLI1 | 四子命令分发与全部选项形态（-p/--pid、--native、-v、--json、--color auto/always/never、-e/--trace、--max-events、--summary/-json 互斥、-r/--rate、-d/--duration、-o/--output、-i/--interval）；`trace=` 前缀剥离（strace 兼容） | test_cli.py（复刻） |
| CLI2 | 参数错误：缺子命令/未知子命令/缺 pid/pid 非整数/rate、duration、interval 越界（`_positive_float(lo,hi]` 消息形态）→ SystemExit 2 | 同 |
| CLI3 | --version 输出 `pyprobe <版本>`（importlib.metadata，失败回退 0.0.0+unknown） | 同 |
| CLI4 | 退出码透传 dump_* 返回值 | 同 |

**API 面与打包**（§10.5-2 预落地 + package-data）

| # | 行为点 | 新测试 |
|---|--------|--------|
| API1 | `__all__` 与旧树名单**逐项一致**（名单 pin 在测试中）+ 显式例外 `resolve_layout`；star-import 全部可解析 | test_api_exports.py |
| API2 | 兼容映射：dto 七类型、errors 十异常、collect_python（open_session+collect_snapshot 组合）、collect_native/collect_syscalls/collect_profile/Sampler/TopStats/TraceFilter/SyscallStat、format_*/dump_*（present）、ProcessSession=Session、resolve_process=open_session、collect_frames=walk_frames、collect_thread=build_thread、read_thread_chain、is_thread_idle_by_stat、DEFAULT_VERSION、offsets（facade 模块）、colors（=present.color） | 同（语义抽查） |
| API3 | offsets facade：`get`/`get_or`/`DEFAULT_VERSION`/`supported_versions`/`resolve_layout` 只读可用（默认布局为 DEFAULT_VERSION 的不可变 Layout——无 configure 全局副作用；新代码不得引用，结构测试守护） | 同 |
| PK1 | wheel 打包含 `pyprobe/offsets.json` 与全部子包（setuptools find + package-data；§10.6-3 smoke 的前置验证——本批以构建产物检查落地） | 打包脚本验证（验收记录） |

### 批次 8 — `web/`（Web 监控模式，ADR A13；新功能无旧树对应物）

| 模块 | 职责 | 公开 API | 行为点清单 |
|------|------|----------|------------|
| `web/monitor.py` | 采样调度 + TopStats 聚合 + 订阅扇出（纯 Python，不 import fastapi/uvicorn——框架无关可单测） | `Monitor(pid, *, rate=50, interval=1.0, sampler_factory=Sampler)`；`.start()`/`.stop()`；`.subscribe() -> Queue`/`.unsubscribe(q)`；`.proc_info`/`.version_warning`/`.exited` | 见批 8 表 |
| `web/app.py` | FastAPI 路由 + SSE 编码 + 静态伺服（monitor 注入，无全局状态） | `create_app(monitor, *, static_dir=None) -> FastAPI` | 同上 |
| `web/serve.py` | 编排（对齐 present/dumps.py 的 dump_* 语义位）：monitor 生命周期 + uvicorn + 异常→退出码 | `serve_web(pid, *, rate=50, interval=1.0, host="127.0.0.1", port=9470) -> int` | 同上 |
| `cli.py`（增补） | web 子命令（参数解析 + 延迟 import 分发；A6 薄壳不破） | — | 同上 |

#### 批 8 行为点清单

**web/monitor.py**（订阅队列内事件载体为 dict `{type: "stats"\|"exit", data: …}`——monitor 不知 SSE 存在，编码归 app 层）

| # | 行为点 | 新测试 |
|---|--------|--------|
| WM1 | 构造即解析目标（Sampler 初始化，SE1 语义承接）：ProcessNotFound/SymbolNotFound 等 PyProbeError 子类构造期上抛，不进后台线程 | test_web_monitor.py |
| WM2 | start() 起后台采样线程：按 rate 绝对时间调度（PF2 语义承接：落后重置不补发）；每样本 TopStats.update 并留存该样本 ThreadInfo 列表为最新快照 | 同 |
| WM3 | 每 interval 广播一帧 `{type: "stats", data: …}` 至全部订阅队列；data.threads 为最新一次采样的完整 ThreadInfo 序列化——完整帧链不丢弃（A13：实时调用栈增强点） | 同 |
| WM4 | stats data 键集 pin：`process={pid, cmdline, exe_path, python_version, version_warning}`；`elapsed`: float；`samples`/`idle_samples`: int；`top_functions: [{name, own, total, own_pct, total_pct, time}]`（仅 own 字典成员入榜——render_top 同口径；按 `(-own, name)` 排序、截 15 条对齐 top_n；pct 为占 samples 百分比、time=own/rate 秒；samples==0 时 pct/time 为 0）；`threads: [{native_tid, thread_id, name, idle, own_pct, frames: [{name, filename, line}]}]`（own_pct 为该线程顶帧函数的 own 占比，无顶帧为 0；idle 线程 frames 为 []） | 同（键集 + 边界值成对） |
| WM5 | 订阅扇出：subscribe() 返回有界队列（maxsize=64）；满时丢最旧保最新（监控取最新语义；广播 put_nowait 不阻塞采样线程）；unsubscribe 幂等；订阅时若已有广播帧立即重放最近一帧（WA3「首帧立即到达」的 monitor 侧支点；无帧不投递） | 同 |
| WM6 | ProcessExited → 广播 `{type: "exit", data: {}}` → 采样线程终止；exit 粘性（新订阅立即收到一帧 exit）；`.exited` 属性为 True | 同 |
| WM7 | stop() 幂等：置停止标志 + join(timeout=5)；重复调用无副作用 | 同 |
| WM8 | sampler_factory 注入点（对齐 Sampler view_factory 测试模式）；version_warning 自 session 透出（SE2 承接），Monitor 本身不打印 | 同 |

**web/app.py**

| # | 行为点 | 新测试 |
|---|--------|--------|
| WA1 | GET / → static/index.html（缺失 → 503 纯文本提示构建产物缺席，不 traceback）；GET /assets/* 经 StaticFiles 挂载（目录穿越防护由其实现承担）；static_dir 注入点 | test_web_app.py（TestClient） |
| WA2 | GET /api/process → `asdict(proc_info)` 四键 + `version_warning` 键（None → null）；键集 pin（非 ASCII 值不 pin 转义形态——前端透明） | 同 |
| WA3 | GET /api/events → StreamingResponse：media_type `text/event-stream`；`Cache-Control: no-cache` + `X-Accel-Buffering: no`；进入即 subscribe，首帧立即到达（stats 或粘性 exit）；客户端断开 → unsubscribe 且生成器结束 | 同（starlette 1.6 TestClient HTTP 传输全量缓冲——内容断言按完整流读取（exit 帧收尾），断连/首帧增量语义走原生 ASGI 驱动；批 8b 实施期发现） |
| WA4 | SSE 帧编码：`event: stats\ndata: <单行 json>\n\n`；exit：`event: exit\ndata: {}\n\n`；exit 帧发出后服务器正常结束该流（前端收 exit 后 close() 不重连，防无限重连循环，FE2 配套）；15s 无帧输出 `: ping\n\n` 心跳 | 同 |
| WA5 | 路由无全局状态：monitor 经 create_app 注入（stub monitor + TestClient 单测，无真实目标进程） | 同 |

**web/serve.py**（对齐 dump_* 编排语义位；uvicorn 细节以注入/monkeypatch 单测）

| # | 行为点 | 新测试 |
|---|--------|--------|
| WS1 | Monitor 构造失败（PyProbeError 子类）→ stderr `[!] <err>`（red，DP1 语义承接）+ rc 1 | test_web_serve.py |
| WS2 | version_warning 非 None → 启动前打 stderr（DP2 承接；web 无 JSON 模式，恒打印） | 同 |
| WS3 | 启动行 `[i] pyprobe web: monitoring pid <n> at http://<host>:<port>/` 走 stderr（record/top 摘要惯例）；uvicorn log_level 压 warning（访问日志噪声不入 stderr 惯例） | 同 |
| WS4 | 端口占用 → `[!] port <port> already in use` + rc 1（uvicorn 启动失败的包装，用户可见语义 pin） | 同 |
| WS5 | Ctrl-C → monitor.stop() + 服务器关闭，rc 0（DP5 干净退出语义承接）；目标退出后服务器继续运行（A13：页面保活显示终态） | 同 |

**cli.py 增补**（A6 薄壳不破：仅参数解析 + 分发）

| # | 行为点 | 新测试 |
|---|--------|--------|
| WC1 | web 子命令参数形态：`-p/--pid` 必填 int；`-r/--rate`（`_positive_float(0,1000]`，默认 50）；`-i/--interval`（`_positive_float(0,60]`，默认 1.0）；`--host` 默认 127.0.0.1；`--port` int 默认 9470（避开 examples 8000 与常见端口，A13） | test_cli.py 增补 |
| WC2 | 延迟 import（dispatch 分支函数体内）：web 依赖（fastapi/uvicorn 及传递依赖）缺席 → stderr `[!] the web subcommand requires extra dependencies — install with: pip install 'pyprobe[web]'` + **rc 2**（DP5 环境不满足语义）；pyprobe 自身代码的 ImportError 不得被吞（仅依赖缺席转提示） | 同（monkeypatch 模拟依赖缺席/自身 bug 成对） |
| WC3 | 返回值透传 serve_web 返回值（CLI4 同语义） | 同 |

**结构契约增补**（A8 机制延伸，tests/test_structure.py 机械强制）

| # | 行为点 | 新测试 |
|---|--------|--------|
| ST-W1 | web 层 import 方向：能力层（kernel/target/cpython/observe/present）不得 import web；web 仅可 import observe 与横切 + `present.color` 窄例外（A13 批 8b 修订；禁 kernel/target/cpython/present 其余/cli）；cli→web 合法且仅经函数内延迟 import | test_structure.py |
| ST-W2 | pyprobe/__init__.py 不得 import web（核心零依赖红线，A13）；API1 名单不变 | test_structure.py + test_api_exports.py |

**端到端**（integration，≤15s 预算）

| # | 行为点 | 新测试 |
|---|--------|--------|
| E2E-W1 | 真实子进程（conftest target 模式）+ 真 Monitor + in-process 不经 uvicorn 拉 /api/events（批 8b 实施期修订：原生 ASGI 驱动替代 TestClient——starlette 1.6 后者全量缓冲，增量 SSE 不可观测；「in-process、不经 uvicorn」意图不变）：首帧 stats 含目标函数名/线程名；terminate 目标后收到 exit 帧 | test_web_live.py（integration） |

**前端**（`frontend/`，React + Vite；产物入库 `pyprobe/web/static/`）

| # | 行为点 | 新测试 |
|---|--------|--------|
| FE1 | 页面信息结构对齐 top：进程头 + Active threads 表 + Idle threads 行 + Top functions 表；Active 表点击线程展开完整实时调用栈（A13） | 手动验证（批 8c 出口记录） |
| FE2 | EventSource 消费 /api/events：stats 帧整表刷新；exit 帧显示"目标进程已退出"终态横幅并 close() 不重连（WA4 配套） | 同 |
| FE3 | 产物入库新鲜度：`npm run build` 后 `git diff --exit-code pyprobe/web/static/`（CI 前端任务，仅 frontend/** 变更触发，与主流水线解耦） | CI 任务 |

## 5. 行为点清单机制（§10.3 落地模板）

每批设计契约时，主代理对旧实现对应模块逐项过行为点，缺项即契约缺口。

### 5.1 行为点清单表（每批一张）

迁移期表含「旧模块 / 旧测试出处」列，已随台账归档（docs/migration-2026-10.md，ADR A14）；现行表形：

| 行为点 | 新测试 | 状态 |
|--------|--------|------|
| （例）`-e trace=file,!openat` 排除语义 | test_syscalls.py::test_mixed_filter | 已覆盖 |

### 5.2 旧测试逐文件标注（替换前 100% 过一遍）

迁移期的逐文件标注（批 1–6 实例）已归档 [docs/migration-2026-10.md](migration-2026-10.md)，
行为点表「旧测试出处」列随之移除（ADR A14）；机制保留，未来大迁移仍适用：

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
契约引文：docs/contracts.md §<锚点>（逐条引用，含 ADR 编号）
只读测试清单：tests/test_<x>.py（逐文件列出，含 Red 验证输出存档路径）
可写范围：pyprobe/<允许新建/修改的文件白名单>
验收标准：
  - diff 严格落在可写范围内
  - scripts/run_tests.sh 全绿、scripts/lint.sh 净、覆盖率棘轮不降
  - Red→Green 证据链：Red 输出（预期断言失败）→ 实现后 Green
  - 文档同步：contracts.md 对应条目填实；TODO 勾销并移除尸体（详情归档 docs/tasks/）
  - 临时资产（门禁脚本/证据存档）登记于 AGENTS.md 或架构地图，否则删除
契约缺陷回报：发现契约/测试错误时停止实现，回报 <具体条目 + 复现路径>，等待主代理裁决
```

## 7. 重构条款编号（原 TODO §10）

> 第一性原理重构（`next/` 重建 + 原子替换）已于 2026-10 完成，计划条目从 TODO 迁出。
> 本节保留原 §10 的条款编号定义，供本文及其它文档/测试/代码中的 `§10.x` 引用解析——
> 除本文件外一律写作 `contracts.md §10.x`。ADR 正文见 §2，行为点清单机制见 §5，
> 复刻约束见 §3。

### §10.1 架构决策记录（ADR）

A1–A8 全文见 §2。原决策范围：A1 Layout 值对象化；A2 统一 ptrace 引擎；A3 CPython 对象
模型层；A4 内存视图分离；A5 DTO 去 format() 化；A6 保留决策（第一性原理验证后保留）；
A7 行为修复；A8 子包化与依赖规则。

### §10.2 测试架构重设计（按 oracle 类型组织，非按模块镜像）

- **§10.2-1** spec-oracle 测试（最高价值）：解析/常量/渲染 vs 权威来源（`co_lines()`、
  gen_offsets 真实头文件产物、系统头文件、strace 真实输出、gdb 样例）
- **§10.2-2** 构造内存单测：FakeMemory 由 Layout 驱动（禁从被测常量照抄），版本 × 对象
  变体矩阵参数化
- **§10.2-3** 状态机单测：ptrace 引擎以 stubbed libc/waitpid 覆盖全状态路径
- **§10.2-4** 存活集成测试：`target`/`spin`/`fast-syscall` 三 fixture + session→snapshot→
  sampling→tracing 端到端（integration ≤15s 预算）
- **§10.2-5** 差异对比测试：旧新 CLI 子进程（见 §10.5-1）
- **§10.2-6** 结构契约测试：公共 API 面 + 子包 import 方向（A8）机械强制
- **§10.2-7** conftest/fixture 重设计：override 隔离、就绪轮询替代固定 `sleep`、fast
  syscall target、`run_tests.sh` mode 与透传 `-m` 合并为 and 表达式

### §10.3 行为点清单机制（"参考旧代码"的落地）

机制与模板见 §5。三原则：主代理逐模块产出行为点清单（缺项即契约缺口）；旧测试逐文件
标注"已覆盖/已放弃（理由入 ADR）"，100% 过一遍；复刻约束（§3）禁止"顺手优化"。

### §10.4 批次计划（按依赖方向，每批一个派发包）

批次 0 脚手架 → 1 `kernel/` + 横切 → 2 `target/` → 3 `cpython/` → 4 `observe/`（session/
snapshot/sampling/profile/topstats）→ 5 `observe/`（syscalls/native）→ 6 `present/` + cli +
打包 → 7 对齐验收 + 替换。

### §10.5 对齐验收门禁（批 7 出口；行为对齐是约束，全部量化）

- **§10.5-1** CLI 差异对比：同一存活目标上旧新 CLI 对比 `stack --json` / `stack --native
  --json` / `syscall --json`——归一化后逐字节一致，豁免仅 A7 涉及字段；`record` folded 比
  键集合；文本输出对固定 fixture 快照一致
- **§10.5-2** 库 API 面对齐：`__all__` 名单一致（A1 新增 `resolve_layout` 为显式例外）
- **§10.5-3** 全套件 unit+integration 绿、lint 净、覆盖率 ≥80%（分树度量）、import 方向
  契约测试绿
- **§10.5-4** README 全部命令示例对新 CLI 重跑核对（示例必须来自真实输出）
- **§10.5-5** 行为点清单 100% 标注；ADR 逐项有守护测试且注释回链契约条目

### §10.6 替换 runbook（单提交原子化）

- **§10.6-1** `git rm -r pyprobe tests`；`git mv next/pyprobe pyprobe`；`git mv next/tests tests`
- **§10.6-2** 根 pyproject/scripts/CI 去掉 next 专属配置，lint 范围还原
- **§10.6-3** `scripts/ci.sh full` 全绿（含 smoke wheel 验证 package-data）后单提交落盘
- **§10.6-4** design.md 重写为新架构 SSOT；回滚 = `git revert` 该提交

### §10.7 风险与控制

第二系统效应 → ADR 纪律（架构变更仅限 §10.1，新想法先成 ADR）；行为点清单遗漏 → §10.3 +
差异对比 + README 重跑三重兜底；双树漂移 → 冻结铁律 + 紧急修复单入口镜像；子包化过度
拆分 → 每包须有七项不可约能力中至少一项的明确归属；差异对比 oracle 自身 bug → 归一化
规则属主代理资产并接受 diff 审查；替换提交过大 → 批次逐个验收 + `git mv` 逐文件可追。
