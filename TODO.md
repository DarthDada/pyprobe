# TODO

## 采用 TDD 开发前需补齐的基础设施

> 现状：214 个测试 + FakeReader/对象构造器 + target_pid fixture 已具备；以下为按红→绿→重构循环衡量的缺口。

### P0 — 支撑红绿循环本身

- [ ] 引入 `pytest-cov`：加入 dev 依赖组；`scripts/run_tests.sh` 支持 `--cov` 透传或新增 coverage 阶段；设定 `--cov-fail-under` 基线防覆盖回退
- [ ] watch 模式：`scripts/run_tests.sh watch` 子命令，复用 venv 中已有的 `watchfiles`，实现保存即重跑（秒级反馈）
- [ ] pytest 严格化：`pyproject.toml` 增加 `addopts = ["-ra", "--strict-markers", "--strict-config"]` 与 `filterwarnings = ["error"]`；同步 `scripts/run_tests.sh`

### P1 — 流程纪律强制（TDD 是流程约束，靠自觉必退化）

- [ ] 远程 CI：新增 `.github/workflows/ci.yml`，仅调用 `scripts/ci.sh`（与本地一致），强制"提交必须全绿"
- [ ] git hooks：pre-commit/pre-push 跑单元测试（无裸命令，走 `scripts/run_tests.sh unit`）
- [ ] AGENTS.md 增补 TDD 工作流约束条目（先写失败测试、red 阶段验证、测试与实现同提交）
- [ ] design.md §13 增补测试架构对应变更（覆盖率目标、watch 模式、CI 阶段）

### P2 — "绿"的质量与重构安全网

- [ ] golden file 测试：`format_process` / CLI 输出格式快照基线，保障格式化重构安全
- [ ] 变异测试（mutmut）：解析二进制内存布局的代码测试"看似覆盖但抓不住错位偏移"风险高，用变异测试验证测试有效性
- [ ] 引入 lint/typecheck（当前 AGENTS.md 明确"无"）：重构安全网 = 测试 + 静态检查，二者缺一
- [ ] 属性测试（hypothesis）：`linetable` / `dict_iter` / `pyobject` 解析器代码的典型受益者
- [ ] 多版本偏移量 fixture 化：为 3.11/3.13 支持做 TDD 式开发（先写目标版本偏移量的失败测试）

## CPython 版本支持

- [ ] 实测 x86-64 下 CPython 3.11/3.13：用对应版本解释器跑 `scripts/gen_offsets.sh` 生成偏移量，子进程端到端验证后编入 `_VERIFIED_OFFSETS`，同步更新 design.md §10 版本支持表（当前均回退 3.12 偏移量并告警）
