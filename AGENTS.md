# Build
- Python env: `uv sync`
- C tool: `make -C native/c` or `scripts/build.sh`
- Offsets: `scripts/gen_offsets.sh` (生成 `pyprobe/offsets.json`)

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
