# Build / Test / Run
- 所有关键命令均经 `scripts/` 实现，详见 README「开发脚本」；CI 一律走 `scripts/ci.sh`，勿手敲裸命令（`uv sync`/`uv build`/`pytest`/`ruff` 等均已有对应脚本）
- 改构建依赖时同步 `pyproject.toml` 的 `[build-system].requires` 与 `scripts/_common.sh` 回退逻辑

# 开发环境初始化（AI 行为约束）
> 命令用途与离线/pip 回退细节见 README「快速开始」「开发脚本」，此处只写 agent 检查逻辑，不重复命令说明。

会话开始或环境状态未知时，按序检查并初始化：
1. **Python ≥ 3.11**：以 `pyproject.toml` `requires-python` 为准；不满足时报告并停止，勿自行改版本要求
2. **`scripts/sync.sh`**：依赖同步；缺 `uv` 时脚本自动 pip 回退，无需预装 uv，勿绕过脚本手装
3. **`scripts/gen_offsets.sh`**：`pyprobe/offsets.json` 已入库且带 `_version` 字段——本地 Python 的 `major.minor` 与其不符、文件缺失或损坏时重新生成（需 `cc` + Python 头文件）
4. **C 参考实现**（`scripts/build.sh`，需 libdw/libelf/zlib）：仅交叉校验/`ci.sh full` 用，非必需；缺系统库时跳过并说明，勿阻塞主流程

初始化后跑 `scripts/run_tests.sh unit` 验证环境可用，再进入正题。环境缺件（Python 过旧、缺编译器/头文件）只报告差距与修复方向，不擅自改 `pyproject.toml` 或绕过 `scripts/`。

# Docs 写作约束

文档分工单一事实来源，避免散弹式修改与内容重复：

| 文档 | 受众 | 写什么 | 不写什么 |
|------|------|--------|----------|
| `README.md` | 人 + AI | 用户/贡献者都需的：快速开始、构建/测试/运行命令、开发脚本表、CLI 用法、权限要求 | AI 行为约束、架构内部 |
| `AGENTS.md`（本文件） | 仅 AI | AI 专属行为约束、跨文档指针 | 人类向的操作说明、架构内容 |
| `docs/design.md` | 人 + AI | 架构**单一事实来源**：模块结构、分层 API、内存/偏移量/CPython 遍历、权限模型、测试架构、手动探测约束技术原因 | 操作命令、脚本列表 |
| `next/docs/contracts.md` | 人 + AI | next/ 重建契约**单一事实来源**（§10 期间）：ADR、分层依赖铁律、模块契约骨架、行为点清单与派发包模板 | 操作命令、旧树架构 |
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
- Lint 走 `scripts/lint.sh`（ruff，配置在 `pyproject.toml` `[tool.ruff]`），CI 在 test 阶段前强制；禁裸 `ruff` 命令
- Typecheck 暂无（ctypes 重度使用，mypy/pyright 噪音大；TODO §7.7 待评估）

# TDD 工作流（§10 重构期强制约束）
> 契约与 ADR 全文见 [next/docs/contracts.md](next/docs/contracts.md)；批次计划见 TODO §10.4。

- **循环**：契约 → 测试 → Red 验证 → 派发包 → 实现 → 全套件绿 + lint；严格先契约后测试、先测试后实现，禁止反向
- **Red 验证**：新测试必须先跑出失败，且失败原因须为预期断言（非语法/导入错误），输出存档供验收
- **同验收批**：测试与实现同批提交验收；双代理分工下子代理仅实现——禁改 tests/docs/契约/脚本，契约或测试有误时停手回报，禁止就地修测试迁就实现
- **冻结铁律**：旧树 `pyprobe/`、`tests/` 不接受新功能；重构期新代码只进 `next/`，旧树紧急修复由主代理以契约更新镜像进 next/
- **测试数据纪律**：权威来源 + 出处注释、可辨识哨兵值、精确复现路径、边界与失败模式成对
- next 树命令一律走 `scripts/next.sh`（同裸命令禁令）；覆盖率棘轮只升不降（§10.5-3 出口 ≥80）

# Architecture
- 详见 [docs/design.md](docs/design.md)
- next/ 重构架构契约 SSOT：[next/docs/contracts.md](next/docs/contracts.md)（§10 重构期）
