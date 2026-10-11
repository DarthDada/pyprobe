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
| `docs/design.md` | 人 + AI | 架构**单一事实来源**：架构地图（§2）、分层 API、内存/偏移量/CPython 遍历、权限模型、测试架构、手动探测约束技术原因 | 操作命令、脚本列表 |
| `docs/contracts.md` | 人 + AI | 模块与 ADR 契约**单一事实来源**：ADR A1–A14（带状态）、分层依赖铁律、模块契约骨架、行为点清单与派发包模板 | 操作命令 |
| `docs/tasks/` | 人 + AI | 单任务详情（背景/步骤/验收标准）；TODO 只留索引 | 长期知识（属 design/contracts） |
| `docs/migration-2026-10.md` | 人 + AI | 迁移期台账归档（旧测试逐文件标注；ADR A14） | 现行契约（属 contracts） |
| `TODO.md` | 人 + AI | 待办索引 + 状态 + 当前目标；条目带日期、完成即清 | 任务详情（进 `docs/tasks/`）|

跨文档原则：
- **命令一律指向 `scripts/`**：任何文档（含本文件）提到构建/测试/运行命令时写脚本名，不写裸 `uv sync`/`uv build`/`pytest`，避免操作变形
- **不重复**：各文档仅引用其它文档的锚点，不复制其内容；同一事实只在一处定义
- **消灭同步而非承诺同步**（ADR A14）：同一事实只留一份，其余放指针；文档里出现「同步」即技术债。唯一例外是构建依赖的行为一致性（`pyproject.toml` `[build-system].requires`、`scripts/_common.sh` 回退、README 离线预装）——三处非文本复制、无法机械校验，维持人工同步

# Manual probing（AI 行为约束）
> 完整技术原因见 [design.md §13.6 手动探测约束](docs/design.md#136-手动探测约束)

- **必须** `subprocess.Popen` 派生子进程经 stdout 获取 PID（复用 `tests/conftest.py:target_pid`），`finally` 中 `terminate()`+`wait(5)`
- **禁止** shell `&` + `$!`/`pgrep` 取 PID（`uv run` 包装器导致 PID 错位）；**禁止**持久 shell 裸 `&` 跑长驻进程（管道继承致 shell 卡死）

# Lint / Typecheck
- Lint 走 `scripts/lint.sh`（ruff + 文档契约检查 `scripts/check_docs.sh`，ADR A14），CI 在 test 阶段前强制；禁裸 `ruff` 命令
- Typecheck 暂无（ctypes 重度使用，mypy/pyright 噪音大；经评估不采用，见 TODO 头部注记）

# TDD 工作流（强制约束）
> 契约与 ADR 记录见 [docs/contracts.md](docs/contracts.md)（ADR 见 contracts.md §2，模块契约与行为点清单见 §4–§5）。

- **循环**：契约 → 测试 → Red 验证 → 实现 → 全套件绿 + lint；严格先契约后测试、先测试后实现，禁止反向
- **Red 验证**：新测试必须先跑出失败，且失败原因须为预期断言（非语法/导入错误）；输出存档至 `docs/red/batch<N>-{red,green}.txt`（N = 批次/ADR 编号），供验收
- **同验收批**：测试与实现同批提交验收；双代理分工下子代理仅实现——禁改 tests/docs/契约/脚本，契约或测试有误时停手回报，禁止就地修测试迁就实现
- **ADR 纪律**：架构变更先成 ADR 记入 [docs/contracts.md](docs/contracts.md) 再实施，禁止绕过契约的"顺手优化"（contracts.md §10.3 复刻约束的教训）
- **测试数据纪律**：权威来源 + 出处注释、可辨识哨兵值、精确复现路径、边界与失败模式成对
- **测试注释纪律**：每条单元测试必须带简要注释（docstring 或行注释），说明测试目的或防护对象（防什么回归、锁哪条契约），以此证明该测试存在的必要性，也便于协作时理解意图；契约守护测试在注释中回链契约条目编号（如 T12、V8）。无注释的测试视同意图不明，验收打回
- 命令一律走 `scripts/run_tests.sh`（同裸命令禁令）；覆盖率棘轮只升不降（出口 ≥80）

# Architecture
- 详见 [docs/design.md](docs/design.md)
- 模块与 ADR 契约 SSOT：[docs/contracts.md](docs/contracts.md)

## 架构地图使用规则（design.md §2）
1. 会话先读地图按「入口」定位文件；**禁止未读地图先全库 grep**
2. 地图条目 → 打开文件验证一次跳转；不符 → 规则 4
3. 改结构（新增目录/改入口/跨模块边界）→ 更新对应行 + 刷新「验证于」日期；函数级改动禁止回写
4. 发现漂移顺手改一行 + 更新日期；超一行 → 开 TODO 不当场修
5. 禁止在地图写入函数签名、字段列表、调用链
