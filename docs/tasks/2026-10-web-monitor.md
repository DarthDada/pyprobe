# 任务：Web 监控模式（`pyprobe[web]`）

> 状态：进行中（2026-10 起）· 当前批次：8a（交付形态与 Monitor）· 交付形态已修订
> 契约 SSOT：ADR A13（[contracts.md](../contracts.md) §2）+ 批次 8 行为点清单
> （contracts.md §4，WM/WA/WS/WC/ST-W/PG/E2E-W/FE 系）；架构落点
> [design.md](../design.md) §2 模块结构 / §12.4 集成模式。
> 需求与选型决策（展示范围/后端/前端/目标/权限）见 ADR A13；
> **交付形态的补充约束与需求分析见下文「需求分析」节**（决策待补 ADR，本文件不复制 ADR 正文）。

## 进度

- [x] 1. 需求分析与选型确认（2026-10）
- [x] 2. 批 0 — ADR 与契约（2026-10）：ADR A13 入 contracts.md §2；批次 8 契约表入 §4（模块表 + 24 行为点）；§1 依赖方向铁律扩展为 `present → {cli, web}`；design.md 同步（§1 概述、§1.1 方向、§2 结构树、§3 API 表、§12.4、§13.5）；TODO/AGENTS.md 同步
- [ ] 3. 批 8a — 交付形态与 Monitor（2026-10）
  - 交付形态（打包期隔离，路线 A）：新增独立发行物 `pyprobe-web`（顶层包 `pyprobe_web/`）；核心 extra `web = ["pyprobe-web==<同版本>"]`；核心 `packages.find`/`package-data` 不含 web（详见下文「需求分析」节，决策待补 ADR）
  - `pyprobe_web/pyproject.toml`：发行名 `pyprobe-web`，依赖 `pyprobe` + `fastapi` + `uvicorn`，package-data 收 `static/*`、`static/assets/*`（显式两层，维持 `setuptools>=61` 下限）；核心 dev 组补 `fastapi`/`uvicorn`/`httpx`（不带 3.12 标记，3.11+ 全版本可测）
  - `scripts/`：`build_wheel.sh`/`build_wheels.sh` 出两个 wheel；`smoke.sh` 分 core-only 与 `[web]` 两段；`sync.sh` + `_common.sh` 认识 `pyprobe_web`（uv 路径 `--all-extras`、pip 回退路径 `.[web]`，两条路径同步改，AGENTS.md 铁律）
  - `pyprobe_web/monitor.py`（WM1–8，TDD，框架无关）：sampler_factory 注入喂构造 ThreadInfo 序列；绝对时间调度；有界队列扇出（满丢最旧保最新）；ProcessExited → exit 粘性终态；stop() 幂等
- [ ] 4. 批 8b — HTTP 服务与 CLI（2026-10）
  - `pyprobe_web/app.py`（WA1–5）：`create_app(monitor, *, static_dir=None)`；`GET /` / `/assets/*` / `/api/process` / `/api/events`（SSE 帧编码 pin）；fastapi 仅在本文件与 `serve.py` import
  - `pyprobe_web/serve.py`（WS1–5）：`serve_web(pid, *, rate, interval, host, port) -> int`；`[!]` 错误 rc 1 / 端口占用友好报错 / Ctrl-C 干净退出 rc 0
  - `cli.py` web 子命令（WC1–3）：`-p/-r/-i/--host/--port` 参数形态；dispatch 分支函数内 `from pyprobe_web.serve import serve_web`（WC2′ 覆盖「子包缺失 / 依赖缺失」两种缺席）
  - 结构/打包契约（ST-W1′/W3′/W5/W6、PG1–2、WC2′）：核心零 fastapi 引用 + 两 wheel 资产/依赖断言，`test_structure.py` + `test_package.py`/`smoke.sh` 机械强制
  - 端到端（E2E-W1）：真子进程 + 真 Monitor + TestClient（in-process）拉 SSE，首帧 stats 含目标函数名、terminate 后收 exit 帧（integration，≤15s）
- [ ] 5. 批 8c — React 前端（2026-10）
  - `frontend/` Vite + React 工程：进程头 + Active threads 表（点击展开完整调用栈）+ Idle 行 + Top functions 表；EventSource 消费，exit 帧显示终态横幅并 close() 不重连（FE1–2）
  - 产物构建至 `pyprobe_web/static/` 并入库
  - CI 前端校验任务：仅 `frontend/**` 变更触发，`npm ci && npm run build && git diff --exit-code pyprobe_web/static/`，与主流水线解耦（FE3）
- [ ] 6. 批 8d — 文档收尾（2026-10）：README（extras 安装、web 子命令用法、权限段补行；命令示例须来自真实输出，§10.5-4 精神）；design.md §12.4 示例重跑核对；本条勾销

## 需求分析：交付形态隔离（2026-10 补充约束）

> 补充约束原文：**引用 fastapi 技术栈的代码与 cli 版本代码必须隔离，避免只集成 cli 版本时 Python 代码 import 这些依赖。**
> 结论：取**打包期隔离 + 路线 A（独立发行包）**——web 代码移出 `pyprobe/`，独立顶层包 `pyprobe_web/`，
> 独立发行物 `pyprobe-web`；核心 wheel 物理上不含任何 fastapi 代码（源码集成同样隔离）。决策待补 ADR。

### 概念对齐

| 术语 | 含义 |
|------|------|
| cli 版本 | `pip install pyprobe`（不含 extras），仅标准库，含四种既有观测模式 + CLI |
| fastapi 技术栈 | `fastapi` / `starlette` / `uvicorn` 及传递依赖（`anyio`/`h11`…）；`httpx` 属测试 dev 依赖 |
| 隔离 | (a) 引用方向隔离：核心只经 cli 延迟分支触达 web；(b) 打包期隔离：核心 wheel 不含 web 代码 |

### 目标产物模型（路线 A）

| 发行物 | 发行名 | 包内容 | 运行时依赖 |
|--------|--------|--------|-----------|
| 核心 | `pyprobe` | 仅 `pyprobe/` | 无（纯标准库） |
| web | `pyprobe-web` | 仅 `pyprobe_web/` | `pyprobe==<同版本>` + `fastapi` + `uvicorn` |

- 核心 `pyproject.toml`：`optional-dependencies.web = ["pyprobe-web==<同版本>"]`；`packages.find` 不含 web。
- `pyprobe_web/pyproject.toml`：发行名 `pyprobe-web`；依赖 `pyprobe` + fastapi + uvicorn；package-data 收静态产物。
- 安装语义：`pip install pyprobe` 磁盘无 web 代码；`pip install 'pyprobe[web]'` 自动拉 `pyprobe-web`；源码集成复制 `pyprobe/` 目录天然只含核心。

### 依赖与命名边界

```
pyprobe(核心):  kernel → target → cpython → observe → present → cli
                                                                  └─(延迟)→ pyprobe_web
pyprobe_web:    → pyprobe.observe + pyprobe.{dto,errors}
                ✗ 禁 import pyprobe.present / cli / kernel / target / cpython
                ✓ 唯一允许 import fastapi/uvicorn 的树（且仅 app.py / serve.py）
```

- `cli.py` web 分支函数内 `from pyprobe_web.serve import serve_web`；core-only 下抛 `ModuleNotFoundError`，归 WC2′ 友好提示。
- `pyprobe_web/monitor.py` 保持框架无关（可无 fastapi 单测）；fastapi/uvicorn import 仅限 `app.py`/`serve.py`。

### 守护集合（按打包期隔离选取）

| 编号 | 命题 | 层级 |
|------|------|------|
| PG1 | 核心 wheel 归档不含 `pyprobe/web/**`、`pyprobe_web/**`；METADATA 无 fastapi 族无条件 `Requires-Dist` | 构建产物 |
| PG2 | `pyprobe[web]` extra 的 `pyprobe-web==X` 与项目版本锁步；构建脚本同时产出两个 wheel | 构建产物 |
| ST-W3′ | `pyprobe/**` 源码零 `fastapi`/`starlette`/`uvicorn`/`anyio`/`h11` import | 源码静态 |
| ST-W1′ | 能力/observe/present 不得 import `pyprobe_web`；`pyprobe_web` 不得 import `pyprobe.present`/`cli`/能力层 | 源码静态 |
| ST-W5 | `cli.py` 顶层 import 不含 `pyprobe_web`/fastapi 族（延迟 import 机械化） | 源码静态 |
| ST-W6 | `pyprobe/**` 无任何模块级 `import pyprobe_web`（core-only 可 import 保证） | 源码静态 |
| WC2′ | `pyprobe web` 在「子包缺失」或「依赖缺失」两情形均给同一条 `pip install 'pyprobe[web]'` + rc 2；只捕获目标模块的 `ModuleNotFoundError`，pyprobe 自身 bug 不吞 | 运行期（两分支成对） |

### 连带影响面

- 契约：新增 ADR（web 交付形态与打包隔离），A13 的 extras/打包两条 bullet 改为指针；批 8 模块表路径 `pyprobe/web/*` → `pyprobe_web/*`，补 ST-W′/PG/WC2′ 条目。
- 构建/脚本：新增 `pyprobe_web/pyproject.toml`；`build_wheel.sh`/`build_wheels.sh` 出两 wheel；`smoke.sh` 分 core-only 与 `[web]` 两段；`sync.sh` 认识 `pyprobe_web`（离线回退同步）；`ci.sh` 矩阵。
- 测试：`test_structure.py` 增 `pyprobe_web` 树方向与核心零引用；`test_package.py`/smoke 加 PG1/PG2；`test_cli.py` WC2′ 双分支；`test_web_*` 指向 `pyprobe_web.*`。
- 文档：design.md §1.1/§2/§3/§12.1/§12.4/§13.5；contracts.md 批 8；TODO.md 索引。`frontend/` 产物输出改 `pyprobe_web/static/`。
- 命名连锁：所有 `pyprobe.web` → `pyprobe_web`。

### 已识别的技术点

1. 同仓双发行物的构建编排（workspace / 多 pyproject）是 8a 主要新增成本，需同步 `scripts/sync.sh` 离线回退（AGENTS.md 铁律）。
2. `pyprobe[web] → pyprobe-web → pyprobe` 的版本必须锁步（PG2），否则 core/web 版本 skew 带出难查的 API 不匹配。

## 后续迭代（首版不含）

- 多 PID 监控（多采样器 + 前端进程切换）
- `GET /api/snapshot` 按需单次栈快照（增量路由，复用 collect_snapshot）
- 火焰图（record 聚合 + 页面内嵌渲染）
- syscall 事件流页面
- 交互控制（暂停采样 / 调频率）——需 WebSocket 替换或补充 SSE
