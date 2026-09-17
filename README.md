# pyprobe

CPython 进程外检查工具 — 无需 ptrace attach（Python 栈模式）即可获取运行中 Python 进程的调用栈。

## 功能

- **Python 栈转储**（默认）— 遍历 `_PyRuntime → interpreter → threads → frames`，输出 py-spy 风格的 Python 调用栈（函数名、文件、行号、qualname）。
- **Native 栈转储**（`--native`）— 通过 elfutils libdwfl 对所有线程进行 DWARF 回溯展开，输出 gdb `thread apply all bt` 风格的原生调用栈。

提供纯 Python 实现（主交付物），另附 C 参考实现用于开发期交叉校验（不对外交付）。

## 快速开始

### 环境准备

```bash
uv sync
scripts/gen_offsets.sh   # 生成 CPython 结构体偏移量（首次或更换 Python 版本时运行）
make -C reference/c          # 编译 C 参考实现（可选）
```

### 构建 Wheel

Wheel 标签为 `py3-none-linux_{x86_64,aarch64}`（纯 Python，支持 Python 3.8+ 宿主，仅 Linux）。

**在线环境（有 uv）**

```bash
scripts/build_wheels.sh          # 产出两个架构的 wheel 到 dist/
# 或仅构建当前架构：
uv build --wheel
```

**离线环境（无 uv，无网络）**

PEP 517 默认的构建隔离会在临时环境里下载 `setuptools`/`wheel`，离线时无法下载会失败。因此离线流程需先在虚拟环境中预装构建依赖，再用 `--no-build-isolation` 复用它们：

```bash
python3 -m venv venv && . venv/bin/activate
pip install setuptools wheel        # 预装构建依赖（以 pyproject.toml 的 [build-system].requires 为准）
pip wheel . --no-deps -w dist --no-build-isolation
# aarch64：加 --config-settings=--build-option=--plat-name=linux_aarch64
```

> 构建依赖以 `pyproject.toml` 的 `[build-system].requires` 为准；若改动该列表，需同步更新此处预装命令。

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

## 权限要求

### Python 栈模式（默认）

使用 `process_vm_readv(2)` 读取目标进程内存，**不需要 ptrace attach**。

| 条件 | 是否可用 |
|------|----------|
| root（CAP_SYS_PTRACE） | 任意进程 |
| 同用户 + `ptrace_scope=0` | 任意进程 |
| 同用户 + `ptrace_scope=1`（默认） | 仅子进程 |
| 目标进程 `dumpable=0` | 仍可读取 |

```bash
cat /proc/sys/kernel/yama/ptrace_scope   # 检查当前 ptrace_scope
```

### Native 模式（`--native`）

使用 `ptrace(2)` attach 所有线程 + libdwfl DWARF 回溯展开。

| 条件 | 是否可用 |
|------|----------|
| root（CAP_SYS_PTRACE） | 任意进程 |
| 同用户 + `ptrace_scope=0` | 任意进程 |
| 同用户 + `ptrace_scope=1`（默认） | 仅子进程 |
| 目标进程 `dumpable=0` | **不可用** |

> **注意**：uvicorn / FastAPI 运行时会将 `dumpable` 设为 0，导致 native 模式在非 root 下无法 attach。Python 栈模式不受此限制。

```bash
sudo uv run python -m pyprobe stack -p <pid> --native   # root 可绕过所有限制
```

## C 参考实现与 Python 实现对比

> C 版本为开发辅助参考实现，不对外交付；仅用于与 Python 实现交叉校验。

| 特性 | C 参考实现 | Python 实现 |
|------|--------|-------------|
| Python 栈转储 | `build/pyprobe <pid>` | `python -m pyprobe stack -p <pid>` |
| Native 栈转储 | `build/pyprobe <pid> --native` | `python -m pyprobe stack -p <pid> --native` |
| 运行时依赖 | libdw.so、libelf.so、libz | libdw.so.1（仅 native 模式） |
| 编译需求 | 需要 C 编译器 + CPython 头文件 | 仅生成偏移量时需要 |
| 外部 Python 包 | 无 | 无（全部使用标准库 ctypes/struct） |
| 架构支持 | x86-64、aarch64 | x86-64（aarch64 需重新生成偏移量） |
| CPython 版本 | 编译时绑定 | 运行时按版本自动选择偏移量 |

## 支持的 CPython 版本

| CPython 版本 | 架构 | 状态 |
|-------------|------|------|
| 3.12.x | x86-64 | 已验证 |
| 3.12.x | aarch64 | 未验证（偏移量理论上与 x86-64 相同，待实际验证） |
| 3.11.x | x86-64, aarch64 | 未验证（回退 3.12 偏移量，输出可能不正确） |
| 3.13.x | x86-64, aarch64 | 未验证（回退 3.12 偏移量，输出可能不正确） |

> 对未验证版本，pyprobe 会在 stderr 输出告警并使用 3.12 偏移量作为默认回退。
> 可通过 `scripts/gen_offsets.sh` 为目标 CPython 生成偏移量，验证后编入 `pyprobe/offsets.py`。

## 限制

- **CPython 版本**：已验证 3.12.x (x86-64)；其他版本/架构回退 3.12 偏移量并告警
- 仅支持 **Linux**（依赖 `/proc`、`process_vm_readv`、`ptrace`）
- 已验证 **x86-64**；aarch64 偏移量理论上相同（均为 64 位 LP64）但未实际验证
- Native 模式在非 root 下受 `ptrace_scope` 和 `dumpable` 限制

## 许可证

见 [LICENSE](LICENSE)。
