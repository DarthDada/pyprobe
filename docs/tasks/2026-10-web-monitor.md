# 任务：Web 监控模式（`pyprobe[web]`）

> 状态：已完成（2026-10，批 8a–8d 全部落地）——本文件留存为批次验收档案
> 契约 SSOT：ADR A13（[contracts.md](../contracts.md) §2）+ 批次 8 行为点清单
> （contracts.md §4，WM/WA/WS/WC/ST-W/E2E-W/FE 系）；架构落点
> [design.md](../design.md) §2 模块结构 / §12.4 集成模式。
> 需求与选型决策（展示范围/后端/前端/安装形态/目标/权限）见 ADR A13，本文件不复制。

## 进度

- [x] 1. 需求分析与选型确认（2026-10）
- [x] 2. 批 0 — ADR 与契约（2026-10）：ADR A13 入 contracts.md §2；批次 8 契约表入 §4（模块表 + 24 行为点）；§1 依赖方向铁律扩展为 `present → {cli, web}`；design.md 同步（§1 概述、§1.1 方向、§2 结构树、§3 API 表、§12.4、§13.5）；TODO/AGENTS.md 同步
- [x] 3. 批 8a — extras 与 Monitor（2026-10，已落地；Red/Green 见 docs/red/batch8a-{red,green}.txt）
  - `pyproject.toml`：`[project.optional-dependencies] web = ["fastapi>=0.141.1", "uvicorn>=0.30"]`；dev 组补 `fastapi`/`uvicorn`/`httpx`（不带 3.12 标记，3.11+ 全版本可测）；`package-data` 收 `web/static/*`、`web/static/assets/*`（显式两层，维持 `setuptools>=61` 下限）
  - `scripts/sync.sh` + `scripts/_common.sh`：uv 路径 `--all-extras`、pip 回退路径 `.[web]`，两条路径同步改（AGENTS.md 铁律）
  - `web/monitor.py`（WM1–8，TDD）：sampler_factory 注入喂构造 ThreadInfo 序列；绝对时间调度；有界队列扇出（满丢最旧保最新）；ProcessExited → exit 粘性终态；stop() 幂等
  - 实现期契约精化（已回写 contracts.md 批 8 表）：WM4 top_functions 仅 own 字典成员入榜、`(-own, name)` 排序、截 15；WM5 订阅重放最近帧（WA3 支点）；start() 清停止标志（stop-before-start 非砖死）
  - 验证：全套件 934 passed、覆盖 97%（monitor.py 98%）、lint 绿、smoke wheel 含 `pyprobe/web/` 子包
- [x] 4. 批 8b — HTTP 服务与 CLI（2026-10，已落地；Red/Green 见 docs/red/batch8b-{red,green}.txt）
  - `web/app.py`（WA1–5）：`create_app(monitor, *, static_dir=None)`；`GET /` / `/assets/*` / `/api/process` / `/api/events`（SSE 帧编码 pin）
  - `web/serve.py`（WS1–5）：`serve_web(pid, *, rate, interval, host, port) -> int`；`[!]` 错误 rc 1 / 端口占用友好报错 / Ctrl-C 干净退出 rc 0
  - `cli.py` web 子命令（WC1–3）：`-p/-r/-i/--host/--port` 参数形态；dispatch 分支函数内延迟 import
  - 结构契约（ST-W1–2）：web 层 import 方向 + `__init__.py` 不 import web，test_structure.py 机械强制
  - 端到端（E2E-W1）：真子进程 + 真 Monitor in-process 拉 SSE，首帧 stats 含目标函数名、terminate 后收 exit 帧（integration，≤15s）
  - 实施期发现与契约修订（已回写 contracts.md）：starlette 1.6 TestClient HTTP 传输全量缓冲——/api/events 内容断言按完整流读取（exit 帧收尾），增量语义（断连退订/首帧送达/E2E 驱动）改原生 ASGI（E2E-W1 机制修订，意图不变）；A13 窄例外 web 可 import `present.color`（WS1 红 `[!]` 与 web→present 禁令冲突）；dev 依赖 httpx → httpx2（starlette 1.6 偏好）
- [x] 5. 批 8c — React 前端（2026-10，已落地；FE1–2 手动验证出口记录下述）
  - `frontend/` Vite + React 工程（react 19 / vite 7，package-lock 入库）：进程头（对齐 TX3 + Elapsed 行 + version_warning 行）+ Active threads 表（点击行展开完整帧链，▸/▾ 手风琴）+ Idle 行 + Top functions 表；EventSource 消费 /api/events，exit 帧显示「目标进程已退出」终态横幅并 close() 不重连（FE1–2）
  - 产物构建至 `pyprobe/web/static/`（index.html + assets/ 哈希文件，布局对齐 WA1 伺服契约）并入库；`.gitignore` 仅忽略 `node_modules/`
  - CI 前端校验任务 `.github/workflows/frontend.yml`：仅 `frontend/**`（及 static/、workflow 自身）变更触发，`npm ci && npm run build && git diff --exit-code pyprobe/web/static/`（补 untracked porcelain 兜底——vite 换哈希名时新文件不入 git diff）；主 ci.yml paths-ignore 排除 frontend/ 与 static/，双向解耦（FE3）
  - FE1/FE2 手动验证（出口记录）：真实目标（spin_forever/burn_cycle 忙线程 + bg-sleeper 闲线程）+ in-process Monitor/create_app/uvicorn（serve_web 同款装配；ptrace_scope=1 下 pyprobe 须为目标祖先，故非 CLI 直起——CLI 分发与 rc 语义已由批 8b 单测覆盖）。16 项断言全过：/api/process 键集、index.html + 哈希 asset 伺服、产物内含 FE1 各区标题与终态横幅文案、首帧 stats（top_functions 含 burn_cycle、idle/active 拆分、帧链含 burn_cycle→spin_forever、键集 pin）、terminate 后 exit 帧 `data: {}` 收尾、uvicorn 干净关闭、monitor.exited 粘性
  - 本批无新增 pytest（FE1–2 契约定为手动验证、FE3 为 CI 任务，批 0 已定）；无 docs/red/batch8c 存档
- [x] 6. 批 8d — 文档收尾（2026-10，已落地）
  - README：命令清单补 `web` 行；新增「Web 监控（web）」章节（extras 安装、真实启动行示例、参数表、页面结构/终态/缺依赖行为说明、前端产物重建命令）；权限段首条（process_vm_readv 系）补 `web`
  - design.md §12.4：标题去批次进度注记；示例重跑核对（2026-10 真实 CLI 默认端口起服：启动行 `[i] pyprobe web: monitoring pid … at http://127.0.0.1:9470/`、页面与 /api/process、/api/events 首帧均可复现），用法细节指向 README（SSOT 不重复）
  - TODO：§1 web 条目勾销清除、§6 首版勾销改写为后续迭代索引（DOC4 预算内，66→64 行）；本文件状态 → 已完成
  - 验证：lint 绿（check_docs DOC1–6）+ 全套件 966 passed

## 后续迭代

首版不含的迭代项已随批 8d 移交 [TODO「Web 监控后续迭代」](../../TODO.md#6-web-监控后续迭代)（单一事实来源，本文件不保留副本）。
