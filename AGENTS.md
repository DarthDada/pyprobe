# Build / Test / Run
- 所有关键命令均经 `scripts/` 实现，详见 README「开发脚本」；CI 一律走 `scripts/ci.sh`，勿手敲裸命令（`uv sync`/`uv build`/`pytest` 等均已有对应脚本）
- 改构建依赖时同步 `pyproject.toml` 的 `[build-system].requires` 与 `scripts/_common.sh` 回退逻辑

# Docs 写作约束

文档分工单一事实来源，避免散弹式修改与内容重复：

| 文档 | 受众 | 写什么 | 不写什么 |
|------|------|--------|----------|
| `README.md` | 人 + AI | 用户/贡献者都需的：快速开始、构建/测试/运行命令、开发脚本表、CLI 用法、权限要求 | AI 行为约束、架构内部 |
| `AGENTS.md`（本文件） | 仅 AI | AI 专属行为约束、跨文档指针 | 人类向的操作说明、架构内容 |
| `docs/design.md` | 人 + AI | 架构**单一事实来源**：模块结构、分层 API、内存/偏移量/CPython 遍历、权限模型、测试架构、手动探测约束技术原因 | 操作命令、脚本列表 |
| `TODO.md` | 人 + AI | 待办事项 | — |

跨文档原则：
- **命令一律指向 `scripts/`**：任何文档（含本文件）提到构建/测试/运行命令时写脚本名，不写裸 `uv sync`/`uv build`/`pytest`，避免操作变形
- **不重复**：各文档仅引用其它文档的锚点，不复制其内容；同一事实只在一处定义
- **改一处同步相关处**：如改构建依赖需同步 `pyproject.toml`、`scripts/_common.sh` 回退、README 离线流程说明

# Manual probing（AI 行为约束）
> 完整技术原因见 [design.md §13.3 手动探测约束](docs/design.md#133-手动探测约束)

- **必须** `subprocess.Popen` 派生子进程经 stdout 获取 PID（复用 `tests/conftest.py:target_pid`），`finally` 中 `terminate()`+`wait(5)`
- **禁止** shell `&` + `$!`/`pgrep` 取 PID（`uv run` 包装器导致 PID 错位）；**禁止**持久 shell 裸 `&` 跑长驻进程（管道继承致 shell 卡死）

# Lint / Typecheck
- 无

# Architecture
- 详见 [docs/design.md](docs/design.md)
