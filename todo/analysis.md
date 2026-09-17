# pyprobe 易集成性分析

> 目标：实现一个易集成的 Python 进程监控工具，支持三种集成方式：
> 1. whl 包安装
> 2. 源码集成 + 命令行工具
> 3. 源码集成 + 接口

---

## 模式一：whl 包安装

### 1. `offsets.json` 未被打入 wheel（致命）

wheel 内容实际只有 `.py` 文件：

```
pyprobe/__init__.py
pyprobe/__main__.py
pyprobe/cli.py
...（仅 .py 文件，无 offsets.json）
```

`pyproject.toml` 没有声明 `[tool.setuptools.package-data]`，`offsets.json` 被排除在 wheel 之外。运行时 `offsets.py:reload()` 找不到 json 文件，回退到 `_OFFSETS` 硬编码字典——这些值是**构建机器上**的 CPython 3.12 偏移量。whl 用户无法重新生成偏移量（没有源码树、没有 `gen_offsets.sh`、没有 C 编译器），如果目标进程的 CPython 构建不同，输出会是静默垃圾。

### 2. wheel 标签 `py312` 不当限制了宿主 Python

`setup.cfg` 指定 `python_tag = py312`，`pyproject.toml` 声明 `requires-python = ">=3.12"`。但 pyprobe 本身是纯 Python，**可以在任何 Python 版本下运行**去探测一个 3.12 目标进程。`py312` 标签会让 pip 拒绝在 3.11 或 3.13 宿主上安装，而 `>=3.12` 又允许 3.13 安装 sdist——两者矛盾。纯 Python 包应使用 `py3` 标签。

### 3. C 实现未纳入 wheel 分发（非问题 / by design）

C 代码为开发辅助参考实现，不对外交付，不打包进 wheel 是设计预期。此前 README 将 C 版本呈现为与 Python 对等的交付物，造成认知错位——该问题已在 README 调整中修正（C 降格为"参考实现"）。

### 4. 无运行时 CPython 版本校验

`stack_dump.py:148-150` 读取了目标的 `Py_Version` 并打印，但**从不检查**它是否与偏移量匹配。对 3.11 或 3.13 目标进程会静默使用 3.12 偏移量，产生垃圾输出而非报错。

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

### 8. 无公共 API，函数直接 print 而非返回数据（致命）

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

### 9. 无异常类型，错误通过 print + return 1 处理

```python
print("[!] cannot find _PyRuntime symbol")
return 1
```

库调用方无法区分"进程不存在""权限不足""版本不匹配""符号未找到"等不同错误。无自定义异常类（如 `ProcessNotFound`、`PermissionDenied`、`OffsetMismatch`），无 `logging` 使用。

### 10. 模块级副作用阻碍库导入

`import pyprobe` 或任何子模块都会触发：
- `memory.py:18` → `ctypes.CDLL("libc.so.6")`（可接受，Linux 必有）
- `native_dump.py:8` → `ctypes.CDLL("libdw.so.1")`（不应在导入时）
- `offsets.py:66` → `reload()` 读文件

库应惰性加载——native 相关的 CDLL 应延迟到 `dump_native()` 调用时。

---

## 跨模式通用问题

### 11. 零测试覆盖

`tests/` 目录仅有 `.gitkeep`。对一个直接读取远程进程内存的工具，无测试是严重可靠性风险。`offsets.py` 的偏移量正确性、`linetable.py` 的 PEP 626 解析、`dict_iter.py` 的多 kind 迭代、`elf.py` 的符号查找——都应有单元测试。

### 12. `offsets.py` 双重数据源，易混淆

`offsets.py` 有硬编码 `_OFFSETS` 字典（56 个键值），然后 `reload()` 用 `offsets.json` 覆盖。源码集成时两者都存在，json 优先；whl 安装时只有硬编码。修改 json 不会更新硬编码值，反之亦然。无法通过环境变量或 API 指定自定义偏移量文件。

### 13. 仅支持 CPython 3.12 + x86-64

3.12 已非最新稳定版。许多用户在 3.11 或 3.13+。工具无版本扩展机制——添加一个版本需要手写新的偏移量表和可能的布局适配逻辑。aarch64 标注"需重新生成偏移量并验证"但无实际支持或 CI。

### 14. 无 CI/CD

无 GitHub Actions 或其他 CI 配置。无自动化构建、测试、多架构 wheel 产出。对分发工具而言无质量门禁。

---

## 优先级建议

| 优先级 | 问题 | 影响 |
|--------|------|------|
| P0 | #1 offsets.json 未入 wheel | whl 用户可能得到垃圾输出 |
| ~~P0~~ | ~~#5 libdw 导入时加载~~ | 已修复（惰性加载） |
| P0 | #8 无结构化 API | 模式三（接口集成）根本无法实现 |
| P1 | #4 无版本校验 | 静默垃圾输出，用户无感知 |
| P1 | #2 py312 标签 | 不当限制宿主 Python 版本 |
| P1 | #10 模块级副作用 | 库导入不安全 |
| P1 | #9 无异常类型 | 库调用方无法处理错误 |
| P2 | #6 无 argparse | CLI 不可扩展、无帮助 |
| P2 | #11 测试缺失 | 可靠性无保障 |
| ~~P2~~ | ~~#3 C 实现未打包~~ | 非问题（by design，C 为参考实现不交付） |
| P3 | #12 双数据源 | 维护混淆 |
| P3 | #13 单版本支持 | 适用范围窄 |
| P3 | #14 无 CI | 无质量门禁 |
