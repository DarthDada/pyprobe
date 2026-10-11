# TODO

## 目录

1. [CLI 易用性与集成](#1-cli-易用性与集成)
2. [版本与架构覆盖](#2-版本与架构覆盖)
3. [native 栈输出对齐 gdb](#3-native-栈输出对齐-gdb)
4. [syscall 对齐 strace](#4-syscall-对齐-strace)
5. [深度检查与差异化](#5-深度检查与差异化)
6. [Web 监控模式（pyprobe[web]，进行中）](#6-web-监控模式pyprobeweb进行中)

> **全局优先级**：§1/§2/§3/§4/§5 新功能待逐项推进；aarch64（§2）为后续项。工程基础设施已完成（git hooks + CI 跨版本矩阵，见 git 历史）；变异/类型/属性测试三项经评估不采用，不再列入。已完成的重构章节（原 §8 代码模块化、§9 代码审查遗留、§10 第一性原理重构）已从本文件移除，其条款编号（原 §10.x）定义迁移至 [docs/contracts.md](docs/contracts.md) §7。

## 1. CLI 易用性与集成

> 库能力（`collect_*` 返回结构化 dataclass）与 CLI 能力对齐，打通 IDE/工具链/CI 程序化消费。`stack` 与 `syscall` 均已支持 `--json`。

- [ ] 1. `info` 子命令：解释器数、线程数、空闲/运行统计、`sys.argv`/`sys.executable`、GIL 状态等进程级概览，复用 `collect_python`
- [ ] 2. 按名称匹配目标进程：`-m/--match <name>`（扫 `/proc/*/cmdline`），省去先 `pgrep` 再传 `-p`；并可对"目标不是 CPython 进程"给出早期友好报错（目前要到符号查找失败才发现）
- [ ] 3. Web 监控模式：`pyprobe web -p <pid>`（可选 extras `pyprobe[web]`）——浏览器实时热点 + 完整线程调用栈；计划与进度详见 [§6](#6-web-监控模式pyprobeweb进行中)（进行中）

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

## 6. Web 监控模式（`pyprobe[web]`，进行中）

> 监控单个指定 PID，浏览器页面实时展示热点视图与**完整线程调用栈**。扩展功能，
> 核心 wheel 零依赖红线不变。**契约 SSOT**：ADR A13（[contracts.md §2](docs/contracts.md)）
> + 批次 8 行为点清单（contracts.md §4，WM/WA/WS/WC/ST-W/E2E-W/FE 系共 24 条）；
> 架构落点：design.md §2 模块结构 / §12.4 集成模式。本文只记计划与进度，不复制契约内容。

### 需求与选型决策（2026-10 已确认）

| 决策点 | 结论 |
|--------|------|
| 展示范围 | top 信息结构（进程头 / Active threads / Idle 行 / Top functions）+ 活跃线程**完整实时调用栈**（点击展开，帧链随 SSE 帧下发不丢弃） |
| 后端 | FastAPI + uvicorn **最小集**（不拉 `fastapi[standard]` 全家桶）；SSE 推送（stats / exit / ping 帧） |
| 前端 | React + Vite，**产物入库 git**（`pyprobe/web/static/`）——wheel 构建不依赖 Node，离线构建承诺不变 |
| 安装形态 | extras：`pip install 'pyprobe[web]'`；缺依赖时 `pyprobe web` 友好报错 rc 2 |
| 目标 | 单 PID；`--host` 默认 127.0.0.1，`--port` 默认 9470（避开 examples 的 8000） |
| 权限 | 同 record/top（`process_vm_readv`，无 ptrace attach）；目标退出后服务器保活显示终态 |

### 进度

- [x] 1. 需求分析与选型确认（2026-10；上表全部决策点逐项确认）
- [x] 2. **批 0 — ADR 与契约**：ADR A13 入 contracts.md §2；批次 8 契约表入 §4（模块表 + 24 行为点）；§1 依赖方向铁律扩展为 `present → {cli, web}`；design.md 同步（§1 概述、§1.1 方向、§2 结构树、§3 API 表、§12.4、§13.5）；TODO/AGENTS.md 同步
- [ ] 3. **批 8a — extras 与 Monitor**：
  - `pyproject.toml`：`[project.optional-dependencies] web = ["fastapi>=0.141.1", "uvicorn>=0.30"]`；dev 组补 `fastapi`/`uvicorn`/`httpx`（不带 3.12 标记，3.11+ 全版本可测）；`package-data` 收 `web/static/*`、`web/static/assets/*`（显式两层，维持 `setuptools>=61` 下限）
  - `scripts/sync.sh` + `scripts/_common.sh`：uv 路径 `--all-extras`、pip 回退路径 `.[web]`，两条路径同步改（AGENTS.md 铁律）
  - `web/monitor.py`（WM1–8，TDD）：sampler_factory 注入喂构造 ThreadInfo 序列；绝对时间调度；有界队列扇出（满丢最旧保最新）；ProcessExited → exit 粘性终态；stop() 幂等
- [ ] 4. **批 8b — HTTP 服务与 CLI**：
  - `web/app.py`（WA1–5）：`create_app(monitor, *, static_dir=None)`；`GET /` / `/assets/*` / `/api/process` / `/api/events`（SSE 帧编码 pin）
  - `web/serve.py`（WS1–5）：`serve_web(pid, *, rate, interval, host, port) -> int`；`[!]` 错误 rc 1 / 端口占用友好报错 / Ctrl-C 干净退出 rc 0
  - `cli.py` web 子命令（WC1–3）：`-p/-r/-i/--host/--port` 参数形态；dispatch 分支函数内延迟 import
  - 结构契约（ST-W1–2）：web 层 import 方向 + `__init__.py` 不 import web，test_structure.py 机械强制
  - 端到端（E2E-W1）：真子进程 + 真 Monitor + TestClient（in-process）拉 SSE，首帧 stats 含目标函数名、terminate 后收 exit 帧（integration，≤15s）
- [ ] 5. **批 8c — React 前端**：
  - `frontend/` Vite + React 工程：进程头 + Active threads 表（点击展开完整调用栈）+ Idle 行 + Top functions 表；EventSource 消费，exit 帧显示终态横幅并 close() 不重连（FE1–2）
  - 产物构建至 `pyprobe/web/static/` 并入库
  - CI 前端校验任务：仅 `frontend/**` 变更触发，`npm ci && npm run build && git diff --exit-code pyprobe/web/static/`，与主流水线解耦（FE3）
- [ ] 6. **批 8d — 文档收尾**：README（extras 安装、web 子命令用法、权限段补行；命令示例须来自真实输出，§10.5-4 精神）；design.md §12.4 示例重跑核对；本条勾销

### 后续迭代（首版不含）

- 多 PID 监控（多采样器 + 前端进程切换）
- `GET /api/snapshot` 按需单次栈快照（增量路由，复用 collect_snapshot）
- 火焰图（record 聚合 + 页面内嵌渲染）
- syscall 事件流页面
- 交互控制（暂停采样 / 调频率）——需 WebSocket 替换或补充 SSE
