# Build
- Python env: `uv sync`
- C tool: `make -C native/c` or `scripts/build.sh`
- Offsets: `scripts/gen_offsets.sh` (生成 `pyprobe/offsets.json`)
- Wheel: `uv build --wheel` (默认 `linux_x86_64`)；两个架构用 `scripts/build_wheels.sh` (产出 `py312-none-linux_{x86_64,aarch64}.whl`)
  - 无 uv 时改用 pip: `python3 -m pip wheel . --no-deps -w dist` (默认 `linux_x86_64`)；aarch64 加 `--config-settings=--build-option=--plat-name=linux_aarch64`
  - 离线环境:先建 venv 并预装构建依赖 (`python3 -m venv venv && . venv/bin/activate && pip install setuptools wheel`)，再 `pip wheel . --no-deps -w dist --no-build-isolation`（aarch64 同样加 `--config-settings`）
    - 预装列表以 `pyproject.toml` 的 `[build-system].requires` 为准，改构建依赖时同步更新
  - `scripts/build_wheels.sh` 自动选择 uv 或 pip，并按当前 python 能否 `import setuptools` 决定是否加 `--no-build-isolation`（离线 venv 复用预装依赖，在线环境走默认隔离下载）

# Run
- Sample app: `uv run python examples/fastapi_app.py` (FastAPI on :8000)
- Python stack dump: `uv run python -m pyprobe <pid>`
- Native stack dump: `uv run python -m pyprobe <pid> --native`
- C version: `build/pyprobe <pid> [--native]`

# Test
- `uv run python -m pytest tests/`

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
  - `offsets.py` / `offsets.json` — CPython 结构体偏移量
- `native/c/` — C 实现（py_stack_dump.c, gen_offsets.c, Makefile）
- `examples/` — 示例目标进程
- `tests/` — 测试
- `scripts/` — 辅助脚本（build.sh, gen_offsets.sh）
