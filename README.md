# pyprobe

CPython 进程外检查工具 — 无需 ptrace attach（Python 栈模式）即可获取运行中 Python 进程的调用栈。

提供纯 Python 实现（主交付物），另附 C 参考实现用于开发期交叉校验（不对外交付）。

> **架构与设计**：模块结构、分层 API、内存读取、偏移量、CPython 遍历、权限模型、版本支持、测试架构等详见 [docs/design.md](docs/design.md)。

## 快速开始

### 环境准备

```bash
scripts/sync.sh            # 安装/同步依赖（替代裸 uv sync）
scripts/gen_offsets.sh     # 生成 CPython 结构体偏移量（首次或更换 Python 版本时运行）
scripts/build.sh           # 编译 C 参考实现（可选；需 libdw/libelf/zlib）
```

### 构建 Wheel

Wheel 标签为 `py3-none-linux_{x86_64,aarch64}`（纯 Python，支持 Python 3.8+ 宿主，仅 Linux）。

```bash
scripts/build_wheel.sh     # 仅构建当前架构 wheel 到 dist/
scripts/build_wheels.sh    # 构建双架构（x86_64 + aarch64）wheel 到 dist/
```

**离线环境（无 uv、无网络）**

上述脚本内置 uv→pip 回退：无 `uv` 时自动改用 `pip wheel`，并在 `setuptools` 可导入时加 `--no-build-isolation` 复用已预装的构建依赖（以 `pyproject.toml` 的 `[build-system].requires` 为准）。因此离线流程只需：

```bash
python3 -m venv venv && . venv/bin/activate
pip install setuptools wheel        # 预装构建依赖（以 [build-system].requires 为准）
scripts/build_wheels.sh             # 脚本自动检测并走 pip 回退路径
```

> 改 `pyproject.toml` 的 `[build-system].requires` 时，需同步更新此处预装命令与 `scripts/_common.sh` 的回退逻辑。

### 启动目标进程

```bash
uv run python examples/fastapi_app.py   # FastAPI on :8000
pgrep -f fastapi_app                     # 获取 PID，例如 14695
```

### Python 栈转储

```bash
uv run python -m pyprobe stack -p 14695      # Python 实现
./build/pyprobe 14695                         # C 参考实现
```

输出示例：

```
Process 14695: python3 examples/fastapi_app.py
Python v3.12.13 (/home/.../python3.12)

Thread 14695
  #0 run (.../asyncio/runners.py:118)  [qualname=Runner.run]
  #1 run (.../asyncio/runners.py:195)  [qualname=run]
  #2 run (.../uvicorn/server.py:86)    [qualname=Server.run]
  #3 <module> (.../fastapi_app.py:47)  [qualname=<module>]

Thread 14701
  #0 matrix_worker (.../fastapi_app.py:22)  [qualname=matrix_worker]
  #1 run (.../threading.py:1012)            [qualname=Thread.run]
  #2 _bootstrap_inner (.../threading.py:1075) [qualname=Thread._bootstrap_inner]
```

### Native 栈转储

```bash
uv run python -m pyprobe stack -p 14695 --native   # Python 实现
./build/pyprobe 14695 --native                      # C 参考实现
```

输出示例：

```
Process 14695: python3 examples/fastapi_app.py

Thread 1 (Thread 0x0000000000000000 (LWP 14701) "python3"):
  #0  0x00007f0c55d93687 in ?? () from /usr/lib/x86_64-linux-gnu/libc.so.6
  #1  0x00007f0c55ddffba in ?? () from /usr/lib/x86_64-linux-gnu/libc.so.6
  #2  0x0000000001999945 in time_sleep () from .../python3.12
  #3  0x00000000018160dd in _PyEval_EvalFrameDefault () from .../python3.12
  ...
```

## 命令行用法

```
pyprobe stack -p <pid>            Python 调用栈转储
pyprobe stack -p <pid> --native   原生调用栈转储（gdb 风格）
```

| 参数 | 说明 |
|------|------|
| `stack` | 子命令：转储线程调用栈 |
| `-p`, `--pid <pid>` | 目标进程的 PID |
| `--native` | 转储原生（C）调用栈而非 Python 调用栈 |

## 开发脚本

所有构建/测试命令均经 `scripts/` 实现，CI 一律走 `scripts/ci.sh` 编排，避免手敲裸命令变形。公共逻辑（uv/python3 回退、`build_wheel`）抽到 `scripts/_common.sh`，各脚本 `source` 之。

| 脚本 | 用途 |
|------|------|
| `scripts/sync.sh` | 安装/同步依赖（替代裸 `uv sync`） |
| `scripts/gen_offsets.sh` | 生成 `pyprobe/offsets.json` |
| `scripts/build.sh` | C 参考实现（`build`/`clean`/`rebuild`；需 libdw/libelf/zlib） |
| `scripts/build_wheel.sh` | 单架构 native wheel（替代裸 `uv build --wheel`） |
| `scripts/build_wheels.sh` | 双架构（x86_64 + aarch64）wheel |
| `scripts/smoke.sh` | wheel 冒烟测试（构建 + 隔离 venv 安装 + import/CLI 校验） |
| `scripts/run_tests.sh` | 测试（`unit`/`integration`/全部） |
| `scripts/ci.sh` | CI 全流程编排（`sync gen-offsets test smoke`，`full` 含 `build-c`） |
| `scripts/_common.sh` | 公共逻辑（被各脚本 source，不单独执行） |

`scripts/ci.sh` 用法：

```bash
scripts/ci.sh                 # 默认流水线：sync gen-offsets test smoke
scripts/ci.sh full            # 上述 + build-c（需 libdw/libelf/zlib）
scripts/ci.sh test            # 单阶段
scripts/ci.sh sync gen-offsets   # 指定阶段，按给出顺序执行
```

## 测试

```bash
scripts/run_tests.sh              # 全部测试（unit + integration）
scripts/run_tests.sh unit         # 仅单元测试（无子进程）
scripts/run_tests.sh integration  # 仅集成测试
scripts/run_tests.sh -- -x        # -- 之后的参数透传给 pytest
```

集成测试通过 `subprocess.Popen` 派生子进程（绕过 `ptrace_scope=1`）；非 Linux 或 ptrace 权限不足时自动 skip。

## 权限要求

> 完整权限模型（ptrace_scope、dumpable、process_vm_readv vs ptrace）见 [docs/design.md §9 权限模型](docs/design.md#9-权限模型)。

- **Python 栈模式**（默认）：使用 `process_vm_readv(2)`，**不需要 ptrace attach**。`ptrace_scope=1`（默认）下仅可读取子进程；root 可读取任意进程。
- **Native 模式**（`--native`）：使用 `ptrace(2)` attach 所有线程。`dumpable=0` 的进程（如 uvicorn/FastAPI）在非 root 下不可用。

```bash
cat /proc/sys/kernel/yama/ptrace_scope   # 检查当前 ptrace_scope
sudo uv run python -m pyprobe stack -p <pid> --native   # root 可绕过所有限制
```

## 许可证

见 [LICENSE](LICENSE)。
