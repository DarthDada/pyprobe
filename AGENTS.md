# Build
- `uv sync` · `scripts/gen_offsets.sh` · `make -C reference/c`（C 参考实现，可选）
- Wheel: `uv build --wheel`；双架构 `scripts/build_wheels.sh`
  - 无 uv / 离线:详见 `scripts/build_wheels.sh`（自动选 uv 或 pip + `--no-build-isolation`）
  - 改构建依赖时同步 `pyproject.toml` 的 `[build-system].requires`

# Run
- `uv run python examples/fastapi_app.py` (FastAPI :8000)
- `uv run python -m pyprobe stack -p <pid> [--native]`
- C 参考实现: `build/pyprobe <pid> [--native]`

## Manual probing
> 约束详见 [design.md §9 权限模型](docs/design.md#9-权限模型) 与 [§13.3 手动探测约束](docs/design.md#133-手动探测约束)

- **必须** `subprocess.Popen` 派生子进程经 stdout 获取 PID（复用 `tests/conftest.py:target_pid`），`finally` 中 `terminate()`+`wait(5)`
- **禁止** shell `&` + `$!`/`pgrep` 取 PID（`uv run` 包装器导致 PID 错位）；**禁止**持久 shell 裸 `&` 跑长驻进程（管道继承致 shell 卡死）

# Test
- `uv run python -m pytest tests/` 或 `scripts/run_tests.sh [unit|integration]`
- 集成测试 spawn 子进程（绕过 ptrace_scope=1）；非 Linux / ptrace 不足时自动 skip

# Lint / Typecheck
- 无

# Architecture
- 详见 [docs/design.md](docs/design.md)
