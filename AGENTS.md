# Build
- Python env: `uv sync`
- C reference impl: `make -C reference/c` or `scripts/build.sh`
- Offsets: `scripts/gen_offsets.sh` (生成 `pyprobe/offsets.json`)
- Wheel: `uv build --wheel` (默认 `linux_x86_64`)；两个架构用 `scripts/build_wheels.sh` (产出 `py3-none-linux_{x86_64,aarch64}.whl`)
  - 无 uv 时改用 pip: `python3 -m pip wheel . --no-deps -w dist` (默认 `linux_x86_64`)；aarch64 加 `--config-settings=--build-option=--plat-name=linux_aarch64`
  - 离线环境:先建 venv 并预装构建依赖 (`python3 -m venv venv && . venv/bin/activate && pip install setuptools wheel`)，再 `pip wheel . --no-deps -w dist --no-build-isolation`（aarch64 同样加 `--config-settings`）
    - 预装列表以 `pyproject.toml` 的 `[build-system].requires` 为准，改构建依赖时同步更新
  - `scripts/build_wheels.sh` 自动选择 uv 或 pip，并按当前 python 能否 `import setuptools` 决定是否加 `--no-build-isolation`（离线 venv 复用预装依赖，在线环境走默认隔离下载）

# Run
- Sample app: `uv run python examples/fastapi_app.py` (FastAPI on :8000)
- Python stack dump: `uv run python -m pyprobe stack -p <pid>`
- Native stack dump: `uv run python -m pyprobe stack -p <pid> --native`
- C version (reference impl): `build/pyprobe <pid> [--native]`

## Manual probing — target process setup
- 手动端到端验证 `pyprobe stack` 时，必须用 `subprocess.Popen` 派生子进程并通过其 stdout 获取真实 PID（复用 `tests/conftest.py:target_pid` 的模式）；`try/finally` 中 `terminate()` + `wait(timeout=5)` 确保子进程被回收
- **禁止** 用 shell `&` 后台启动 + `$!`/`pgrep` 获取 PID：`uv run` 包装器会导致 `$!` 指向 `uv`/`bash` 而非 Python 解释器；`pgrep -f <pattern>` 会匹配到 `/bin/bash -c ...` 命令行。对非 Python 进程探测 `_PyRuntime` 必然失败
- **禁止** 在持久 shell 里裸用 `&` 跑长驻进程：后台子进程继承管道，不退出时管道不关闭，会导致整个 shell 会话卡死（连后续 `pkill` 都无法返回）
- `process_vm_readv` 要求目标进程是当前进程的后代（`ptrace_scope=1` 默认下）；`Popen` 子进程天然满足此条件，shell `&` 后台进程则不一定

# Test
- All tests: `uv run python -m pytest tests/` 或 `scripts/run_tests.sh`
- Unit only (无子进程依赖): `scripts/run_tests.sh unit` 或 `pytest -m "not integration"`
- Integration only (spawn 子进程 + process_vm_readv): `scripts/run_tests.sh integration`
- 集成测试通过 `target_pid` session fixture 派生子进程作为探测目标（绕过 ptrace_scope=1 限制）；非 Linux 自动 skip
- Native dump 集成测试在 ptrace 权限不足时自动 skip

# Lint / Typecheck
- 无配置（暂无 linter / typechecker）

# Layout
- `pyprobe/` — 纯 Python 实现（入口 `pyprobe.cli:main`）
  - `memory.py` — process_vm_readv (ctypes)
  - `elf.py` — ELF64 符号查找 (struct, 零依赖)
  - `pyobject.py` — PyLong / PyUnicode / PyBytes 远程读取
  - `dict_iter.py` — CPython 3.12 Dict 迭代器
  - `linetable.py` — PEP 626 行号表解析
  - `thread_names.py` — threading._active 线程名查找
  - `stack_dump.py` — Python 栈转储主逻辑
  - `native_dump.py` — Native 栈转储 (ctypes + libdw.so)
  - `offsets.py` / `offsets.json` — CPython 结构体偏移量（多版本验证表，`offsets.py` 为单一数据源，`offsets.json` 为开发期覆盖）
- `tools/` — 构建期工具（gen_offsets.c，生成 pyprobe/offsets.json）
- `reference/c/` — C 参考实现（py_stack_dump.c, Makefile；非交付，仅供交叉校验）
- `examples/` — 示例目标进程
- `tests/` — 测试
  - `conftest.py` — `target_pid` session fixture（派生子进程作为探测目标）
  - `helpers.py` — `FakeReader`（内存字典模拟 `RemoteReader`）+ CPython 对象内存构造器
  - `targets/target_app.py` — 集成测试目标进程（主线程 + bg-worker 线程）
  - `test_*.py` — 单元测试（offsets/linetable/dict_iter/elf/pyobject/memory/stack_dump/types/errors）
  - `test_integration.py` — 集成测试（`@pytest.mark.integration`，端到端验证 `collect_python`/`format_process`/`dump_python`/`collect_native`）
- `scripts/` — 辅助脚本（build.sh, build_wheels.sh, gen_offsets.sh, run_tests.sh）
