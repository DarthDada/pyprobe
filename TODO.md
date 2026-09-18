# TODO

## 目录

1. [采用 TDD 开发前需补齐的基础设施](#1-采用-tdd-开发前需补齐的基础设施)
2. [调用栈输出路径缩短](#2-调用栈输出路径缩短)
3. [native 栈输出对齐 gdb](#3-native-栈输出对齐-gdb)

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
- [ ] 12. 多版本偏移量 fixture 化：为 3.11/3.13 支持做 TDD 式开发（先写目标版本偏移量的失败测试）

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

- [ ] 1. Python 模式：`FrameInfo.format()` 显示层将路径缩短为**末 2 级**（`os.sep` 分割取 `[-2:]`，不足 2 级原样返回），对齐 py-spy 惯例。dataclass 字段 `FrameInfo.filename` 保持完整路径不变（`collect_*` 层契约不变）
- [ ] 2. Native 模式：`NativeThreadInfo.format()` 显示层将 module 缩短为 **basename**（`os.path.basename`），对齐 `perf report` / `addr2line` 惯例（`.so` basename 天然唯一）。`NativeFrame.module` 数据字段保持完整路径不变
- [ ] 3. 共用 `_shorten_path(path, depth=2)` 辅助函数（`types.py` 私有），Python 模式传 `depth=2`，Native 模式传 `depth=1`
- [ ] 4. 同步更新 `tests/test_types.py`：`/lib/libc.so`（Python 模式末 2 级仍为 `lib/libc.so`；Native 模式 basename 为 `libc.so`）等断言
- [ ] 5. 同步更新 `docs/design.md` §3.3 颜色表注或 §3.1 数据类型说明，记录 format 层路径缩短策略

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
