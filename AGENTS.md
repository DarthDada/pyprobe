# Build
- C tool: `make -C native/c` or `scripts/build.sh`
- Python env: `uv sync`

# Run
- Sample app: `uv run python examples/fastapi_app.py` (FastAPI on :8000)
- Dump a process: `build/pyprobe <pid>`

# Layout
- `native/c/` — core C tools (pyprobe)
- `examples/` — sample Python target apps for testing
- `tests/` — tests
- `scripts/` — dev helper scripts
