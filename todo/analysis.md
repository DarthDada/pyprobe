# pyprobe 易集成性分析

> 目标：实现一个易集成的 Python 进程监控工具，支持三种集成方式：
> 1. whl 包安装
> 2. 源码集成 + 命令行工具
> 3. 源码集成 + 接口

---

## 模式一：whl 包安装

### 1. `offsets.json` 未被打入 wheel（已修复）

`pyproject.toml` 已声明 `[tool.setuptools.package-data]` 将 `offsets.json` 打入 wheel。同时 `offsets.py` 重构为多版本验证表（`_VERIFIED_OFFSETS`），已验证偏移量直接编入 Python 代码——即使 wheel 中无 `offsets.json`，也能正常工作。`offsets.json` 仅作开发期覆盖。

### 2. wheel 标签 `py312` 不当限制了宿主 Python（已修复）

`setup.cfg` 已改为 `python_tag = py3`，`pyproject.toml` 已改为 `requires-python = ">=3.8"`。pyprobe 为纯 Python，宿主版本不受限。

### 3. C 实现未纳入 wheel 分发（非问题 / by design）

C 代码为开发辅助参考实现，不对外交付，不打包进 wheel 是设计预期。此前 README 将 C 版本呈现为与 Python 对等的交付物，造成认知错位——该问题已在 README 调整中修正（C 降格为"参考实现"）。

### 4. 无运行时 CPython 版本校验（已修复）

`stack_dump.py` 在读取目标 `Py_Version` 后调用 `offsets.configure(version_str)`，按目标版本选择已验证偏移量表。未验证版本会输出 stderr 告警并回退默认偏移量。

---

## 模式二：源码集成 + 命令行工具

### 5. `libdw.so.1` 在导入时强制加载，破坏非 native 模式（已修复）

`native_dump.py` 原在模块顶层执行 `ctypes.CDLL("libdw.so.1")`，导致导入即加载。现已改为惰性加载：`libdw`/`libc` 初始化为 `None`，CDLL 加载与原型设置移入 `_init_libs()`，仅在 `dump_native()` 首次调用时触发。Python 栈模式不再依赖 libdw。

### 6. CLI 无 argparse，脆弱且不可扩展

`cli.py:14-23` 手动解析 `sys.argv`：
- 无 `--help` / `-h`
- 无 `--version`
- PID 非数字时直接 `ValueError` 崩溃，无友好提示
- 无 `--output`/`--format json` 等选项
- 未来加任何参数都要改这个 if-else 链

### 7. `gen_offsets.sh` 依赖 C 编译器 + CPython 开发头文件

这是源码集成的必要步骤，但生产/最小化环境常无 `cc` 或 `python3.12-dev`。无 fallback 机制（如预置多版本偏移量表或运行时自动探测）。

---

## 模式三：源码集成 + 接口

### 8. 无公共 API，函数直接 print 而非返回数据（致命）（已修复）

`__init__.py` 仅有 docstring，无任何 `__all__` 或导出。核心函数设计为 CLI 入口而非库函数：

```python
# stack_dump.py
def dump_python(pid):
    ...
    print(f"Process {pid}: {cmdline}")  # 直接打印
    ...
    return 0  # 返回退出码，不返回数据
```

调用方无法获取结构化数据（线程列表 + 每线程的帧列表）。要程序化使用只能 hack `redirect_stdout`，完全不可用作库 API。

**应有分层设计**：`collect(pid) -> [ThreadInfo]`（返回数据）与 `format(threads) -> str`（格式化输出）分离。

**已修复**：采用三层分离设计：
- `collect_python(pid) -> (ProcessInfo, list[ThreadInfo])` — 纯数据采集，返回结构化 dataclass，抛异常
- `format_process(proc_info, threads) -> str` — 格式化为 CLI 风格字符串
- `dump_python(pid) -> int` — 薄 CLI 封装（collect + format + print + 退出码）

native_dump 同步分离为 `collect_native` / `format_native` / `dump_native`。新增 `types.py`（`FrameInfo`/`ThreadInfo`/`ProcessInfo`/`NativeFrame`/`NativeThreadInfo` dataclass）与 `errors.py`（异常层级）。`__init__.py` 导出 `__all__` 公共 API。

### 9. 无异常类型，错误通过 print + return 1 处理（已修复）

```python
print("[!] cannot find _PyRuntime symbol")
return 1
```

库调用方无法区分"进程不存在""权限不足""版本不匹配""符号未找到"等不同错误。无自定义异常类（如 `ProcessNotFound`、`PermissionDenied`、`OffsetMismatch`），无 `logging` 使用。

**已修复**：新增 `errors.py` 异常层级（`PyProbeError` 基类 + `ProcessNotFound`/`PermissionDenied`/`SymbolNotFound`/`NoInterpreterState`/`NoThreadState`/`VersionNotSupported`/`AttachFailed`）。`collect_python`/`collect_native` 抛出相应异常而非 print + return 1；`dump_*` CLI 封装捕获异常并转换为 stderr 提示 + 退出码。

### 10. 模块级副作用阻碍库导入（部分修复）

`import pyprobe` 或任何子模块会触发：
- `memory.py:18` → `ctypes.CDLL("libc.so.6")`（可接受，Linux 必有）
- ~~`native_dump.py:8` → `ctypes.CDLL("libdw.so.1")`~~ → 已修复（#5 惰性加载）
- ~~`offsets.py:66` → `reload()` 读文件~~ → 已修复（重构后无模块级 I/O，`configure()` 按需调用）

native_dump 和 offsets 的模块级副作用已消除。

---

## 跨模式通用问题

### 11. 零测试覆盖（已修复）

`tests/` 目录仅有 `.gitkeep`。对一个直接读取远程进程内存的工具，无测试是严重可靠性风险。`offsets.py` 的偏移量正确性、`linetable.py` 的 PEP 626 解析、`dict_iter.py` 的多 kind 迭代、`elf.py` 的符号查找——都应有单元测试。

**已修复**：新增 9 个测试文件共 181 个单元测试，覆盖全部核心模块：
- `test_offsets.py` — 版本键解析、configure/get/fallback、全键校验、KeyError
- `test_types.py` — FrameInfo/ThreadInfo/ProcessInfo/NativeThreadInfo 格式化
- `test_errors.py` — 异常层级、各异常类属性
- `test_linetable.py` — PEP 626 行号表解析（code=10/11/12/13/14/15、越界、空表、不可读）
- `test_pyobject.py` — PyLong（0/1/2 位/过大）、PyBytes（正常/空/过大/负）、PyUnicode（ASCII/UTF-16/非紧凑/null）
- `test_dict_iter.py` — combined/unicode/split/managed 字典迭代、空洞跳过、空表、失败路径
- `test_memory.py` — 页缓存（单页/跨页/部分页/旁路/LRU 淘汰）、read_ptr/u32/u64/int
- `test_elf.py` — decode_py_version、read_cmdline、find_symbol/read_const（真实 CPython ELF）
- `test_stack_dump.py` — collect_frames（链遍历/trampoline 跳过/上限）、_is_thread_idle（全部启发式）、collect_thread、format_process、collect_python 错误路径

通过 `FakeReader`（内存字典模拟 RemoteReader）和 CPython 对象内存构造器实现零进程依赖测试；`FakeRemoteReader` 子类化 RemoteReader 测试真实页缓存逻辑。

### 12. `offsets.py` 双重数据源，易混淆（已修复）

`offsets.py` 重构为 `_VERIFIED_OFFSETS` 多版本字典作为单一数据源。`offsets.json` 降级为开发期覆盖（`configure()` 内加载），不再与硬编码值并列。添加新版本只需在 `_VERIFIED_OFFSETS` 中新增条目。

### 13. 仅支持 CPython 3.12 + x86-64（部分修复）

`offsets.py` 已支持多版本扩展机制（`_VERIFIED_OFFSETS` 字典 + `configure()`）。当前已验证 3.12 x86-64。aarch64 偏移量理论上相同（64 位 LP64）但未实际验证。添加新版本/架构只需运行 `gen_offsets.sh` 生成偏移量，验证后编入 `_VERIFIED_OFFSETS`。未验证版本会告警并回退。

### 14. 无 CI/CD

无 GitHub Actions 或其他 CI 配置。无自动化构建、测试、多架构 wheel 产出。对分发工具而言无质量门禁。

---

## 优先级建议

| 优先级 | 问题 | 影响 |
|--------|------|------|
| ~~P0~~ | ~~#1 offsets.json 未入 wheel~~ | 已修复（package-data + 验证表入代码） |
| ~~P0~~ | ~~#5 libdw 导入时加载~~ | 已修复（惰性加载） |
| ~~P0~~ | ~~#8 无结构化 API~~ | 已修复（collect/format/dump 三层分离 + types.py + __all__ 导出） |
| ~~P1~~ | ~~#4 无版本校验~~ | 已修复（configure() 告警+回退） |
| ~~P1~~ | ~~#2 py312 标签~~ | 已修复（py3 + >=3.8） |
| ~~P1~~ | ~~#10 模块级副作用~~ | 部分修复（offsets/native_dump 已惰性化，libc 保留） |
| ~~P1~~ | ~~#9 无异常类型~~ | 已修复（errors.py 异常层级 + collect_* 抛异常） |
| ~~P2~~ | ~~#6 无 argparse~~ | 已修复（argparse + --help/--version） |
| ~~P2~~ | ~~#11 测试缺失~~ | 已修复（9 文件 181 测试，FakeReader 零进程依赖） |
| ~~P2~~ | ~~#3 C 实现未打包~~ | 非问题（by design，C 为参考实现不交付） |
| ~~P3~~ | ~~#12 双数据源~~ | 已修复（单一数据源 _VERIFIED_OFFSETS） |
| ~~P3~~ | ~~#13 单版本支持~~ | 部分修复（多版本机制就绪，待验证更多版本） |
| P3 | #14 无 CI | 无质量门禁 |
