# TODO

## 目录

1. [采用 TDD 开发前需补齐的基础设施](#1-采用-tdd-开发前需补齐的基础设施)
2. [调用栈输出路径缩短](#2-调用栈输出路径缩短)
3. [native 栈输出对齐 gdb](#3-native-栈输出对齐-gdb)
4. [strace 子命令：系统调用追踪](#4-strace-子命令系统调用追踪)

## 1. 采用 TDD 开发前需补齐的基础设施

> 现状：214 个测试 + FakeReader/对象构造器 + target_pid fixture 已具备；以下为按红→绿→重构循环衡量的缺口。

### P0 — 支撑红绿循环本身

- [ ] 1. 引入 `pytest-cov`：加入 dev 依赖组；`scripts/run_tests.sh` 支持 `--cov` 透传或新增 coverage 阶段；设定 `--cov-fail-under` 基线防覆盖回退
- [ ] 2. watch 模式：`scripts/run_tests.sh watch` 子命令，复用 venv 中已有的 `watchfiles`，实现保存即重跑（秒级反馈）
- [ ] 3. pytest 严格化：`pyproject.toml` 增加 `addopts = ["-ra", "--strict-markers", "--strict-config"]` 与 `filterwarnings = ["error"]`；同步 `scripts/run_tests.sh`

### P1 — 流程纪律强制（TDD 是流程约束，靠自觉必退化）

- [ ] 4. 远程 CI：新增 `.github/workflows/ci.yml`，仅调用 `scripts/ci.sh`（与本地一致），强制"提交必须全绿"
- [ ] 5. git hooks：pre-commit/pre-push 跑单元测试（无裸命令，走 `scripts/run_tests.sh unit`）
- [ ] 6. AGENTS.md 增补 TDD 工作流约束条目（先写失败测试、red 阶段验证、测试与实现同提交）
- [ ] 7. design.md §13 增补测试架构对应变更（覆盖率目标、watch 模式、CI 阶段）

### P2 — "绿"的质量与重构安全网

- [ ] 8. golden file 测试：`format_process` / CLI 输出格式快照基线，保障格式化重构安全
- [ ] 9. 变异测试（mutmut）：解析二进制内存布局的代码测试"看似覆盖但抓不住错位偏移"风险高，用变异测试验证测试有效性
- [ ] 10. 引入 lint/typecheck（当前 AGENTS.md 明确"无"）：重构安全网 = 测试 + 静态检查，二者缺一
- [ ] 11. 属性测试（hypothesis）：`linetable` / `dict_iter` / `pyobject` 解析器代码的典型受益者
- [ ] 12. 多版本偏移量测试 fixture 化：`TestOffsetsTable`（tests/test_offsets.py）仍硬编码 3.12；参数化覆盖 `_VERIFIED_OFFSETS` 全部版本（key fixture 分共享 key + 版本特有 key，如 3.11 `PyObject.pre_values`、3.13 `ThreadState.current_frame`），使未来新版本支持可 TDD 式开发（先写目标版本偏移量的失败测试，再编入表）

## 2. 调用栈输出路径缩短

> 背景：`pyprobe stack` 输出中每个帧后的源文件路径过长且信息熵低，影响分析效率（2026-09 实测）。问题根因：format 层直接输出 `FrameInfo.filename` / `NativeFrame.module` 的完整路径，未做缩短。

### 问题分析

**Python 模式**（`types.py:25` `FrameInfo.format`）

实测 FastAPI 目标进程，路径可达 90+ 字符，有效信息仅末 2 级：

```
#0 run (/home/admin/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/lib/python3.12/asyncio/runners.py:118)
#2 run (/home/admin/projects/pyprobe/.venv/lib/python3.12/site-packages/uvicorn/server.py:86)
```

**Native 模式**（`types.py:107` `NativeThreadInfo.format`）

单线程内同一路径重复 N 次（实测 17 帧中 13 帧重复 python3.12 路径），噪声更严重：

```
#3  0x...1999945 in time_sleep () from /home/admin/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/bin/python3.12
#4  0x...18056e8 in cfunction_vectorcall_O.llvm... () from /home/admin/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/bin/python3.12
...（重复 11 次）
```

### 修改建议

- [x] 1. Python 模式：`FrameInfo.format()` 显示层将路径缩短为**末 2 级**（`os.sep` 分割取 `[-2:]`，不足 2 级原样返回），对齐 py-spy 惯例。dataclass 字段 `FrameInfo.filename` 保持完整路径不变（`collect_*` 层契约不变）
- [x] 2. Native 模式：`NativeThreadInfo.format()` 显示层将 module 缩短为 **basename**（`os.path.basename`），对齐 `perf report` / `addr2line` 惯例（`.so` basename 天然唯一）。`NativeFrame.module` 数据字段保持完整路径不变
- [x] 3. 共用 `_shorten_path(path, depth=2)` 辅助函数（`types.py` 私有），Python 模式传 `depth=2`，Native 模式传 `depth=1`
- [x] 4. 同步更新 `tests/test_types.py`：`/lib/libc.so`（Python 模式末 2 级仍为 `lib/libc.so`；Native 模式 basename 为 `libc.so`）等断言
- [x] 5. 同步更新 `docs/design.md` §3.3 颜色表注或 §3.1 数据类型说明，记录 format 层路径缩短策略

> 实现调整（相对原建议）：新增 CLI `-v/--verbose` 开关控制详细程度——缺省输出缩短路径，`-v` 显示完整路径。`verbose` 参数经 CLI → `dump_*` → `format_*` → dataclass `format()` 三层透传（与 `--color` 同模式）。Native 模式经 `os.path.basename` 实现而非 `_shorten_path(depth=1)`，语义等价。

### 影响面（无回归）

- 空闲检测 `_is_thread_idle_by_frames` 用 `filename.endswith()` 操作原始字段，不经 format → 不受影响
- 集成测试 `test_frames_reference_target_app` 检查 `f.filename`（原始字段）→ 不受影响
- 单元测试用裸文件名（`bar.py`、`x.py`）≤2 级 → 输出不变
- `collect_*` / `format_*` 分层契约不变：缩短仅在 format 显示层

## 3. native 栈输出对齐 gdb

> 背景：`pyprobe stack --native` 与 gdb `thread apply all bt` 对比（2026-09 实测），已修复 `GElf_Word` 32 位截断致全 `??`（1fbb785）与线程降序两问题；以下为剩余差异的落地计划，全部基于现有技术栈（ctypes + libdw/libdwfl，无新依赖）。

### P0 — 生产环境可加载（前置条件）

- [ ] 1. libdw 加载名回退：`ctypes.CDLL` 依次尝试 `libdw.so.1` → `libdw.so`（或 `ctypes.util.find_library("dw")`），兼容 ldconfig 只注册其一的环境
- [ ] 2. 符号探测降级：加载后检查 `dwarf_getscopes` / `dwfl_module_getsrc` / `dwarf_cfi_addrframe` 等是否存在（最低需 elfutils 0.158，CFI 0.142+），缺失则标记对应特性不可用，保底符号名+回溯
- [ ] 3. 回溯失败标注对齐 gdb：`unwind_failed` 判定修正（native_dump.py:213 当前把 pc=0 单帧当成功，如 io_uring `iou-sqp-*` 线程应提示 `Backtrace stopped`）

### P1 — 展示层对齐（低成本高收益）

- [ ] 4. LLVM 后缀截断：`re.sub(r'\.llvm\.\d+$', '', name)` 对齐 gdb 短符号名（`run_mod.llvm`）
- [ ] 5. file:line：`dwfl_module_getsrc` + `dwarf_linesrc`/`dwarf_lineno`，输出 `at src/unix/linux.c:1432`（仅带 DWARF 的模块生效，如 uvloop；python 主程序无 `.debug_*` 与 gdb 持平）
- [ ] 6. 线程头格式：补 pthread 描述符地址（`Thread 1 (Thread 0x7f... (LWP 7101) "python3")`）——需评估获取方式（ptrace 读 pthread 结构 vs 保持现状仅 LWP）

### P2 — 完整 DWARF 解析（高成本）

- [ ] 7. 内联帧展开：`dwarf_getscopes` 作用域链上每个 `DW_TAG_inlined_subroutine` DIE 展开为逻辑帧，`DW_AT_abstract_origin` 回溯取函数名，`DW_AT_call_file`/`call_line` 取调用点
- [ ] 8. 参数名：subprogram DIE 遍历 `DW_TAG_formal_parameter` 取 `DW_AT_name`
- [ ] 9. 参数值（最难，可只做子集）：`dwarf_cfi_addrframe` + `dwarf_frame_register` 求 CFI，`dwarf_getlocation` 位置表达式解释器（`DW_OP_fbreg`/`DW_OP_regN`/`DW_OP_addr` 等），远程读内存复用 `RemoteReader`；`@entry` 依赖 `DW_AT_call_site`/GNU 扩展，视成本取舍

## 4. strace 子命令：系统调用追踪

> 背景：新增 `pyprobe strace -p <pid>` 子命令，实时监控目标进程**所有线程**的系统调用（类似 strace），含统计汇总模式 `--summary`（strace -c 等价）。已确认范围：全线程 + TRACECLONE 跟随新线程；strace 风格参数解码（常用 ~50 syscall，其余裸数字）；仅 x86-64。
>
> 技术方案：纯 Python + ctypes 调 `libc.ptrace`（零第三方依赖）。核心 **PTRACE_SEIZE + PTRACE_SYSCALL**（SEIZE 而非 ATTACH 的原因：提前结束时 tracee 处于运行态，ATTACH 无法 DETACH 会把目标进程挂死；SEIZE 可 INTERRUPT 后 DETACH，且 options 随 SEIZE 传入、新线程自动继承）。完整设计见会话计划（PTRACE 状态机、坑位清单）。

- [ ] 1. 新增 `pyprobe/syscall_table.py`：x86-64 syscall 号→名表（~362 条，从 `/usr/include/x86_64-linux-gnu/asm/unistd_64.h` 一次性提取编入）+ `DECODE` 参数类别元数据（~50 常用 syscall：path/buf_in/buf_out/open_flags/timespec 等）+ `TRACE_GROUPS`（file/network/process 类组）+ O_*/MAP_*/PROT_* flags 常量表；表抽查单测（read=0/write=1/openat=257）
- [ ] 2. `pyprobe/types.py` 新增 `SyscallEvent` dataclass（tid/name/nr/args/rendered/ret/error/elapsed + `format()`，strace 风格输出 + 项目颜色语义）；`pyprobe/errors.py` 新增 `UnsupportedArchitecture`；format/异常单测
- [ ] 3. `pyprobe/strace.py` 纯函数部分：字符串转义（strace 风格 `\NNN` 八进制）、截断（非 verbose 32 字符）、`_read_cstr`/`_read_timespec`/`_decode_args`/`_fill_out_args`（FakeReader 注入单测）、`TraceFilter`（`-e trace=file|network|read,write` 解析）、`format_summary` + `SyscallStat`（strace -c 风格表格）
- [ ] 4. `pyprobe/strace.py` ptrace 引擎：`_UserRegs`（x86-64 user_regs_struct）、attach（SEIZE + re-scan + INTERRUPT）/detach（幂等，INTERRUPT + WNOHANG 收 stop + DETACH）、events() 主循环状态机（`waitpid(-1, __WALL)` 分派：syscall stop 相位跟踪、EVENT_CLONE 新线程激活、EVENT_EXEC 补事件+相位重置、group-stop 吞掉、信号转发）、entry 暂存 6 参数 + buf_out exit 读、elapsed 时间戳、`Stracer`/`collect_strace`/`dump_strace`（含 summary 路径，KeyboardInterrupt 优雅 detach）；monkeypatch stub 单测
- [ ] 5. CLI 接入：`cli.py` 加 strace 子命令（`-e trace=`/`--max-events`/`--summary`/`--color`/`-v`）；`__init__.py` 导出新 API；`test_cli.py` 加 stub 分发测试
- [ ] 6. 集成测试 + 端到端：`test_integration.py` 加 `TestStrace`（collect 断言 clock_nanosleep/多 tid/elapsed；dump 输出断言；summary 表头断言；AttachFailed → skip；trace 后目标进程仍存活）；`scripts/run_tests.sh integration`；手动端到端（Popen 派生 target_app 取 PID，验证 `strace --max-events 30` 与 `--summary --max-events 200`）
- [ ] 7. 文档：README（CLI 用法、输出/汇总示例、权限说明）；`docs/design.md` §2/§3/§9/§13 更新 + 末尾新增 §14（锚点不重编号）
