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

`--json` 输出机器可读结果（数据字段始终为完整路径，永不着色）：

```bash
uv run python -m pyprobe stack -p 14695 --json | python -m json.tool
```

```json
{
  "process": {
    "pid": 14695,
    "cmdline": ["python3", "examples/fastapi_app.py"],
    "exe_path": "/usr/bin/python3.12",
    "python_version": "3.12.13"
  },
  "threads": [
    {
      "native_tid": 14695,
      "thread_id": 2480501152528,
      "name": "MainThread",
      "frames": [
        {"name": "run", "filename": "/usr/lib/.../asyncio/runners.py", "line": 118}
      ],
      "idle": false
    }
  ]
}
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
pyprobe stack -p <pid> --json     机器可读 JSON 输出
pyprobe syscall -p <pid>          系统调用追踪（strace 风格）
pyprobe record -p <pid>           周期采样 → folded stacks
pyprobe top -p <pid>              实时热点视图（终端刷新）
```

| 参数 | 说明 |
|------|------|
| `stack` | 子命令：转储线程调用栈 |
| `-p`, `--pid <pid>` | 目标进程的 PID |
| `--native` | 转储原生（C）调用栈而非 Python 调用栈 |
| `--json` | 输出机器可读 JSON（完整路径、永不着色） |
| `-v`, `--verbose` | 显示完整源文件/模块路径（缺省缩短：Python 帧取末 2 级，native 模块取 basename） |
| `--color {auto,always,never}` | 彩色输出，缺省 `auto`（检测 tty） |

输出到终端时自动着色（pid 黄、函数名绿、文件名青、行号暗淡、错误红）；管道/重定向或设置 `NO_COLOR` 时自动纯文本，`--color=always` 可强制。

## 系统调用追踪

```bash
pyprobe syscall -p 14695                       # 实时流式输出（Ctrl-C 结束）
pyprobe syscall -p 14695 --max-events 30       # 收满 30 个事件后停止
pyprobe syscall -p 14695 --summary --max-events 200   # strace -c 风格汇总
pyprobe syscall -p 14695 -e trace=file         # 只看文件类系统调用
pyprobe syscall -p 14695 -e trace=read,write   # 只看指定系统调用
pyprobe syscall -p 14695 -e trace=!futex       # 排除某系统调用
```

| 参数 | 说明 |
|------|------|
| `-e, --trace <expr>` | 过滤表达式：类组名（`file`/`network`/`process`/`memory`/`signal`/`desc`）、逗号分隔的系统调用名，或 `!` 前缀排除 |
| `--max-events <n>` | 捕获 n 个事件后停止（缺省直到 Ctrl-C） |
| `--summary` | 输出 `strace -c` 风格统计表而非逐事件流 |
| `-v`, `--verbose` | 字符串参数不截断（缺省 32 字符 + `...`） |

流式输出示例：

```
14701  clock_nanosleep(1, 1, {6928, 190163573}, 0, 0x0, 0x0) = 0 <1.000455>
14701  clock_nanosleep(1, 1, {6929, 190245273}, 0, 0x0, 0x0) = 0 <1.000544>
14695  openat(AT_FDCWD, "/etc/hosts", O_RDONLY|O_CLOEXEC) = 4 <0.000021>
```

汇总示例：

```
syscall                   calls     errors       total     total/s         per-call
----------------------------------------------------------------------------------
clock_nanosleep              50          0  66.012166  66.012166      1.320243
----------------------------------------------------------------------------------
total                        50          0  66.012166  66.012166
```

- 追踪**所有线程**（含追踪期间新建线程，PTRACE_O_TRACECLONE），事件带线程 ID 前缀
- 常用 ~50 个系统调用按 strace 风格解码参数（路径字符串、`O_*`/`MAP_*`/`PROT_*` 标志、timespec、read/write 缓冲区内容），其余显示裸数值
- 基于寄存器的返回值错误自动解码为 `= -1 ENOENT (No such file or directory)` 风格
- 仅支持 x86-64（其他架构报 `UnsupportedArchitecture`）

## 采样记录（record）

```bash
pyprobe record -p 14695                       # 50Hz 采样，Ctrl-C 结束
pyprobe record -p 14695 -d 10                 # 采样 10 秒后停止
pyprobe record -p 14695 -r 100 -d 5           # 100Hz 采样 5 秒
pyprobe record -p 14695 -d 10 -o folded.txt   # 写入文件（缺省 stdout）
```

| 参数 | 说明 |
|------|------|
| `-r`, `--rate <hz>` | 采样频率（缺省 50，范围 (0, 1000]） |
| `-d`, `--duration <sec>` | 采样时长（缺省直到 Ctrl-C） |
| `-o`, `--output <file>` | folded 输出文件（缺省 stdout） |
| `--color {auto,always,never}` | stderr 进度着色（stdout 始终纯 folded） |

输出为 [folded stacks](https://www.brendangregg.com/flamegraphs.html) 格式（flamegraph.pl / inferno-flamegraph 输入），每行 `线程;帧;帧;... 样本数`，帧序从根到叶，线程名做首帧前缀：

```
"matrix-worker";matrix_worker 87
MainThread;run;main;loop 42
```

- 采样使用 `process_vm_readv(2)`，**不需要 ptrace attach**；空闲线程（内核态睡眠）自动排除
- stdout 纯 folded 输出（进度/摘要走 stderr），可直接管道给火焰图工具：

```bash
pyprobe record -p 14695 -d 10 | inferno-flamegraph > profile.svg
```

- Ctrl-C 或目标进程退出时已采集的样本不丢失（部分数据照常输出）

## 实时热点（top）

```bash
pyprobe top -p 14695                # 50Hz 采样，每秒刷新
pyprobe top -p 14695 -r 20 -i 2    # 20Hz 采样，每 2 秒刷新
```

| 参数 | 说明 |
|------|------|
| `-r`, `--rate <hz>` | 采样频率（缺省 50，范围 (0, 1000]） |
| `-i`, `--interval <sec>` | 终端刷新间隔（缺省 1.0，范围 (0, 60]） |
| `--color {auto,always,never}` | 彩色输出，缺省 `auto`（检测 tty） |

持续采样累积统计并整屏刷新，展示各线程当前帧与热点函数排行（`OWN%` 为函数自身消耗——位于栈顶的采样占比；`TOTAL%` 为含被调用开销——出现在栈内任意位置的采样占比）：

```
Process 14695: python3 examples/fastapi_app.py
Python v3.12.13 (/usr/bin/python3.12)
Elapsed 12.3s | 615 samples (idle 2)

Active threads
  TID     OWN%  CURRENT
  14701   52.3%  matrix_worker (fastapi_app.py:22)
  14695    0.8%  run (asyncio/runners.py:118)

Top functions
    OWN%   TOTAL%     TIME  FUNCTION
    52.3%    61.5%     6.4s  matrix_worker
     0.8%     1.2%     0.1s  run
```

- 需要终端（stdout 非 tty 报错退出码 2，非交互场景请用 `record`）
- Ctrl-C 干净退出（恢复光标），目标进程退出自动结束

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
- **Syscall 追踪**（`syscall` 子命令）：使用 `ptrace(2)`（PTRACE_SEIZE + PTRACE_SYSCALL），权限要求同 native 模式。仅 x86-64；结束时自动 detach，不会挂死目标进程。
- **采样**（`record` / `top` 子命令）：与 Python 栈模式相同——`process_vm_readv(2)`，**不需要 ptrace attach**，不受 `dumpable=0` 限制。

```bash
cat /proc/sys/kernel/yama/ptrace_scope   # 检查当前 ptrace_scope
sudo uv run python -m pyprobe stack -p <pid> --native   # root 可绕过所有限制
```

## 许可证

见 [LICENSE](LICENSE)。
