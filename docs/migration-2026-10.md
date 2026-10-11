# 迁移期台账归档（第一性原理重构，2026-10）

> 本文件归档 [contracts.md](contracts.md) 第一性原理重构（`next/` 原子替换，2026-10）的
> **过程性台账**：批 1–6 的「旧测试逐文件标注」（对应 contracts.md §5.2 机制，替换前 100%
> 过一遍）。行为点表的「旧测试出处」列（引用已删除的旧树测试/行号）同时移除——完整映射可经
> git 历史恢复（本文件迁移前的 contracts.md 版本）；行为点编号（T12、WM1…）保留在
> contracts.md（测试注释回链不失效）。
>
> 归档原因见 [ADR A14](contracts.md#a14-文档契约机械化文档规则-lint-化)：完工的迁移台账
> 继续占据活契约篇幅即腐化源头；「完成」的定义包含清理尸体。

#### 批 1 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_memory.py | 已覆盖（拆为 test_mem.py + test_views.py） | — |
| tests/test_errors.py | 已覆盖（test_errors.py，消息 pin 加强） | — |
| tests/test_syscall_tracer.py | 引擎部分已覆盖（test_ptrace.py）；entry/exit 配对、过滤、渲染属批次 5 | 配对/渲染非引擎职责，A2 分层后归 observe/syscalls.py |
| tests/test_types.py | 数据部分已覆盖（test_dto.py）；format() 渲染属批次 6 | format() 被 A5 废除，渲染迁 present/text.py |
| tests/test_procmeta.py | 已覆盖（test_procfs.py）；decode_py_version 属批次 2（target/identity） | 版本解码是 CPython 布局知识，非内核接口 |


#### 批 2 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_offsets.py | 已覆盖（test_layout.py）；`configure`/`get`/`get_or` 模块函数与 `_active` 复位测试不移植 | 全局单例即 A1 要消灭的对象；L3 语义改写（不抛异常）已入契约 |
| tests/test_elf.py | 已覆盖（test_symbols.py，另增合成 ELF 单测——旧树只有真实二进制 spec 测试，解析逻辑无单元级守护） | — |
| tests/test_procmeta.py | decode_py_version 已覆盖（test_identity.py I1/I2）；read_cmdline 批次 1 已覆盖（test_procfs.py P1） | — |


#### 批 3 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_pyobject.py | 已覆盖（test_objects.py；O6 死检查删除入契约） | — |
| tests/test_dict_iter.py | 已覆盖（test_dicts.py，构造器改 Layout 驱动） | — |
| tests/test_linetable.py | 已覆盖（test_code.py；另增 C7 spec-oracle——旧树只有手工构造 linetable，无 CPython 地面真值对照） | — |
| tests/test_stack_dump.py | 帧/线程链部分已覆盖（test_frames/test_runtime）；idle 启发式、collect_thread、format/CLI 部分属批次 4/6 | 分层后职责在 observe/present |
| tests/test_thread_names.py | （不存在——旧树 names 仅集成测试覆盖） | 批次 3 新增单元级覆盖（test_names.py），集成验证批次 4 端到端 |


#### 批 4 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_stack_dump.py | idle 启发式 + collect_thread 已覆盖（test_snapshot）；TestFormatProcess/TestDumpPythonCli 属批次 6 | format/CLI 在 present/cli |
| tests/test_sampler.py | 已覆盖（TestInit 决议部分 → test_session；TestSample → test_sampling）；test_offsets_not_reconfigured_in_sample 不移植 | 全局 configure 已被 A1 消灭，该测试守护的对象不存在了 |
| tests/test_record.py | FoldKey/CollectProfile 已覆盖（test_profile）；FormatFolded/DumpRecord 属批次 6 | 同上 |
| tests/test_top.py | TestUpdate 已覆盖（test_topstats，A7 修复语义）；TestRender/TestDumpTop 属批次 6 | 同上 |
| tests/test_integration.py | TestCollectPython 已覆盖（test_session_snapshot 集成）；format/CLI/native/syscall 段属批次 5/6 | 分层后职责在 observe/present |


#### 批 5 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_syscall_tracer.py | 消费侧已覆盖（test_syscalls SY1–SY7；引擎侧批次 1 已覆盖）；`_handle_exec` 绕过过滤器场景以 A7 修复语义覆盖（SY5） | 修复语义取代旧错误行为，不复制旧断言 |
| tests/test_syscall_render.py | 已覆盖（SY8–SY10；format_summary/JSON 段属批次 6）；`_fill_out_args` ret<0 分支与 ret==0 读 32 字节两断言不移植 | A7 修复语义（死代码删除/空串渲染）取代 |
| tests/test_syscall_table.py | 已覆盖（SY11 spec-oracle 取代人工抽样断言——头文件即权威，旧表内断言是对抄本的抄本） | spec-oracle 上位替代 |
| tests/test_stack_dump.py::TestDumpNativeCli | CLI 段属批次 6；collect 侧已覆盖（NA 系） | — |


#### 批 6 旧测试标注（§5.2 实例）

| 旧测试文件 | 覆盖判定 | 放弃理由 |
|------------|----------|----------|
| tests/test_types.py | format 部分已覆盖（test_text TX1/2/3/5/6）；dataclass 数据部分批次 1 已覆盖（test_dto） | A5：format() 自 dto 剥离 |
| tests/test_colors.py | 已覆盖（test_color CL1–CL3） | — |
| tests/test_stack_dump.py::TestFormatProcess | 已覆盖（TX4） | — |
| tests/test_record.py::TestFormatFolded | 已覆盖（TX8） | — |
| tests/test_top.py::TestRender/TestDumpTop | 已覆盖（TX9/DP5，含 A7 P1-8 修复守护） | — |
| tests/test_syscall_render.py::TestFormatSummary | 已覆盖（TX7） | — |
| tests/test_json_output.py | 已覆盖（test_jsonout JS1/JS2） | — |
| tests/test_cli.py | 已覆盖（test_cli CLI1–CLI3 复刻） | — |
| tests/test_api_exports.py | 已覆盖（test_api_exports API1/2，名单 pin 强化） | — |
