# 任务：Web 监控模式（`pyprobe[web]`）

> 状态：进行中（2026-10 起）· 当前批次：8a（extras 与 Monitor）
> 契约 SSOT：ADR A13（[contracts.md](../contracts.md) §2）+ 批次 8 行为点清单
> （contracts.md §4，WM/WA/WS/WC/ST-W/E2E-W/FE 系）；架构落点
> [design.md](../design.md) §2 模块结构 / §12.4 集成模式。
> 需求与选型决策（展示范围/后端/前端/安装形态/目标/权限）见 ADR A13，本文件不复制。

## 进度

- [x] 1. 需求分析与选型确认（2026-10）
- [x] 2. 批 0 — ADR 与契约（2026-10）：ADR A13 入 contracts.md §2；批次 8 契约表入 §4（模块表 + 24 行为点）；§1 依赖方向铁律扩展为 `present → {cli, web}`；design.md 同步（§1 概述、§1.1 方向、§2 结构树、§3 API 表、§12.4、§13.5）；TODO/AGENTS.md 同步
- [ ] 3. 批 8a — extras 与 Monitor（2026-10）
  - `pyproject.toml`：`[project.optional-dependencies] web = ["fastapi>=0.141.1", "uvicorn>=0.30"]`；dev 组补 `fastapi`/`uvicorn`/`httpx`（不带 3.12 标记，3.11+ 全版本可测）；`package-data` 收 `web/static/*`、`web/static/assets/*`（显式两层，维持 `setuptools>=61` 下限）
  - `scripts/sync.sh` + `scripts/_common.sh`：uv 路径 `--all-extras`、pip 回退路径 `.[web]`，两条路径同步改（AGENTS.md 铁律）
  - `web/monitor.py`（WM1–8，TDD）：sampler_factory 注入喂构造 ThreadInfo 序列；绝对时间调度；有界队列扇出（满丢最旧保最新）；ProcessExited → exit 粘性终态；stop() 幂等
- [ ] 4. 批 8b — HTTP 服务与 CLI（2026-10）
  - `web/app.py`（WA1–5）：`create_app(monitor, *, static_dir=None)`；`GET /` / `/assets/*` / `/api/process` / `/api/events`（SSE 帧编码 pin）
  - `web/serve.py`（WS1–5）：`serve_web(pid, *, rate, interval, host, port) -> int`；`[!]` 错误 rc 1 / 端口占用友好报错 / Ctrl-C 干净退出 rc 0
  - `cli.py` web 子命令（WC1–3）：`-p/-r/-i/--host/--port` 参数形态；dispatch 分支函数内延迟 import
  - 结构契约（ST-W1–2）：web 层 import 方向 + `__init__.py` 不 import web，test_structure.py 机械强制
  - 端到端（E2E-W1）：真子进程 + 真 Monitor + TestClient（in-process）拉 SSE，首帧 stats 含目标函数名、terminate 后收 exit 帧（integration，≤15s）
- [ ] 5. 批 8c — React 前端（2026-10）
  - `frontend/` Vite + React 工程：进程头 + Active threads 表（点击展开完整调用栈）+ Idle 行 + Top functions 表；EventSource 消费，exit 帧显示终态横幅并 close() 不重连（FE1–2）
  - 产物构建至 `pyprobe/web/static/` 并入库
  - CI 前端校验任务：仅 `frontend/**` 变更触发，`npm ci && npm run build && git diff --exit-code pyprobe/web/static/`，与主流水线解耦（FE3）
- [ ] 6. 批 8d — 文档收尾（2026-10）：README（extras 安装、web 子命令用法、权限段补行；命令示例须来自真实输出，§10.5-4 精神）；design.md §12.4 示例重跑核对；本条勾销

## 后续迭代（首版不含）

- 多 PID 监控（多采样器 + 前端进程切换）
- `GET /api/snapshot` 按需单次栈快照（增量路由，复用 collect_snapshot）
- 火焰图（record 聚合 + 页面内嵌渲染）
- syscall 事件流页面
- 交互控制（暂停采样 / 调频率）——需 WebSocket 替换或补充 SSE
