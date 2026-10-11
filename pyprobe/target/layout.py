"""CPython struct layouts — per-version verified offset tables (ADR A1).

``resolve_layout(version_str)`` returns an immutable :class:`Layout` value
object that callers pass explicitly — the old tree's ``offsets._active``
global singleton and its read-path side effects (implicit configure) are
gone, so one pyprobe process can observe targets running different CPython
versions at the same time.

Verified tables are keyed by CPython major.minor. For 64-bit LP64
architectures (x86-64, aarch64) the offsets are identical for the same
CPython version, so no architecture key is needed.

Unverified versions resolve to the :data:`DEFAULT_VERSION` table with
``Layout.verified == False`` — no exception (契约变更, contracts.md 批 2:
旧 configure 先填 fallback 后抛 VersionNotSupported；警告串由批次 4
session 组装，消息文本由 errors.py 契约钉住).

The dev-time ``offsets.json`` override semantics are preserved: when the
override file's ``_version`` matches the requested version's major.minor,
its content **replaces** the table wholesale (it is the complete generator
output from the requested version's real headers — merging would leak keys
whose struct field no longer exists in that version).

契约（contracts.md 批 2 表 L1–L12）。当前为骨架：表数据/DEFAULT_VERSION/
类型签名已锁定（权威数据即契约），方法返回哨兵，批次 2 实现填实。
"""

import json
import os
from dataclasses import dataclass
from types import MappingProxyType

#: Dev-time override path (module-level so tests/conftest can point it at a
#: nonexistent path — 契约 L12, §10.2-7 override 隔离).
_DEFAULT_OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), os.pardir,
                                       "offsets.json")

_VERIFIED_OFFSETS = {
    # Generated per-version on x86-64 by tools/gen_offsets.c via
    # scripts/gen_offsets.sh (LP64: x86-64 and aarch64 share these values).
    # Keys whose struct field does not exist in a version are omitted there;
    # consumers branch with Layout.get_or(). See the header of gen_offsets.c
    # for the full per-version compatibility notes.
    "3.11": {
        "pointer_size": 8,
        "int_size": 4,
        "RuntimeState.interpreters": 32,
        "pyinterpreters.main": 16,
        "pyinterpreters.head": 8,
        "InterpreterState.threads": 8,
        "pythreads.head": 8,
        "InterpreterState.sysdict": 904,
        "ThreadState.next": 8,
        "ThreadState.cframe": 56,
        "ThreadState.thread_id": 152,
        "ThreadState.native_thread_id": 160,
        "CFrame.current_frame": 8,
        "InterpreterFrame.f_code": 32,
        "InterpreterFrame.previous": 48,
        "InterpreterFrame.prev_instr": 56,
        "CodeObject.co_firstlineno": 72,
        "CodeObject.co_qualname": 128,
        "CodeObject.co_filename": 112,
        "CodeObject.co_name": 120,
        "CodeObject.co_linetable": 136,
        "CodeObject.co_code_adaptive": 184,
        "LongObject.ob_size": 16,
        "LongObject.ob_digit": 24,
        "digit_size": 4,
        "PyObject_size": 16,
        "Object.ob_type": 8,
        "TypeObject.tp_flags": 168,
        "TypeObject.tp_dictoffset": 288,
        "Py_TPFLAGS_MANAGED_DICT": 16,
        "PyObject.pre_values": -32,
        "HeapTypeObject.ht_cached_keys": 872,
        "DictObject.ma_keys": 32,
        "DictObject.ma_values": 40,
        "dictkeysobject_size": 32,
        "dictkeysobject.dk_log2_index_bytes": 9,
        "dictkeysobject.dk_kind": 10,
        "dictkeysobject.dk_nentries": 24,
        "dictvalues_header": 0,
        "PyDictKeyEntry_size": 24,
        "PyDictUnicodeEntry_size": 16,
        "BytesObject.ob_sval": 32,
        "VarObject.ob_size": 16,
        "PyASCIIObject_size": 48,
        "PyCompactUnicodeObject_size": 72,
        "PyUnicodeObject.data_any": 72,
        "_Py_CODEUNIT_size": 2,
    },
    "3.12": {
        "pointer_size": 8,
        "int_size": 4,
        "RuntimeState.interpreters": 32,
        "pyinterpreters.main": 16,
        "pyinterpreters.head": 8,
        "InterpreterState.threads": 64,
        "pythreads.head": 8,
        "InterpreterState.sysdict": 352,
        "InterpreterState.imports": 944,
        "_import_state.modules": 0,
        "InterpreterState.interpreter_trampoline": 381736,
        "ThreadState.next": 8,
        "ThreadState.cframe": 56,
        "ThreadState.thread_id": 136,
        "ThreadState.native_thread_id": 144,
        "CFrame.current_frame": 0,
        "InterpreterFrame.f_code": 0,
        "InterpreterFrame.previous": 8,
        "InterpreterFrame.prev_instr": 56,
        "CodeObject.co_firstlineno": 68,
        "CodeObject.co_qualname": 128,
        "CodeObject.co_filename": 112,
        "CodeObject.co_name": 120,
        "CodeObject.co_linetable": 136,
        "CodeObject.co_code_adaptive": 192,
        "LongObject.long_value.lv_tag": 16,
        "LongObject.long_value.ob_digit": 24,
        "digit_size": 4,
        "PyObject_size": 16,
        "Object.ob_type": 8,
        "TypeObject.tp_flags": 168,
        "TypeObject.tp_dictoffset": 288,
        "Py_TPFLAGS_MANAGED_DICT": 16,
        "HeapTypeObject.ht_cached_keys": 880,
        "DictObject.ma_keys": 32,
        "DictObject.ma_values": 40,
        "dictkeysobject_size": 32,
        "dictkeysobject.dk_log2_index_bytes": 9,
        "dictkeysobject.dk_kind": 10,
        "dictkeysobject.dk_nentries": 24,
        "dictvalues_header": 0,
        "PyDictKeyEntry_size": 24,
        "PyDictUnicodeEntry_size": 16,
        "BytesObject.ob_sval": 32,
        "VarObject.ob_size": 16,
        "PyASCIIObject_size": 40,
        "PyCompactUnicodeObject_size": 56,
        "PyUnicodeObject.data_any": 56,
        "_Py_CODEUNIT_size": 2,
    },
    "3.13": {
        "pointer_size": 8,
        "int_size": 4,
        "RuntimeState.interpreters": 624,
        "pyinterpreters.main": 16,
        "pyinterpreters.head": 8,
        "InterpreterState.threads": 7336,
        "pythreads.head": 8,
        "InterpreterState.sysdict": 7640,
        "ThreadState.next": 8,
        "ThreadState.current_frame": 72,
        "ThreadState.thread_id": 152,
        "ThreadState.native_thread_id": 160,
        "InterpreterFrame.f_code": 0,
        "InterpreterFrame.previous": 8,
        "InterpreterFrame.prev_instr": 56,
        "CodeObject.co_firstlineno": 68,
        "CodeObject.co_qualname": 128,
        "CodeObject.co_filename": 112,
        "CodeObject.co_name": 120,
        "CodeObject.co_linetable": 136,
        "CodeObject.co_code_adaptive": 200,
        "LongObject.long_value.lv_tag": 16,
        "LongObject.long_value.ob_digit": 24,
        "digit_size": 4,
        "PyObject_size": 16,
        "Object.ob_type": 8,
        "TypeObject.tp_flags": 168,
        "TypeObject.tp_dictoffset": 288,
        "Py_TPFLAGS_MANAGED_DICT": 16,
        "HeapTypeObject.ht_cached_keys": 880,
        "DictObject.ma_keys": 32,
        "DictObject.ma_values": 40,
        "dictkeysobject_size": 32,
        "dictkeysobject.dk_log2_index_bytes": 9,
        "dictkeysobject.dk_kind": 10,
        "dictkeysobject.dk_nentries": 24,
        "dictvalues_header": 8,
        "PyDictKeyEntry_size": 24,
        "PyDictUnicodeEntry_size": 16,
        "BytesObject.ob_sval": 32,
        "VarObject.ob_size": 16,
        "PyASCIIObject_size": 40,
        "PyCompactUnicodeObject_size": 56,
        "PyUnicodeObject.data_any": 56,
        "_Py_CODEUNIT_size": 2,
    },
    "3.14": {
        "pointer_size": 8,
        "int_size": 4,
        "RuntimeState.interpreters": 800,
        "pyinterpreters.main": 16,
        "pyinterpreters.head": 8,
        "InterpreterState.threads": 7336,
        "pythreads.head": 8,
        "InterpreterState.sysdict": 7648,
        "ThreadState.next": 8,
        "ThreadState.current_frame": 72,
        "ThreadState.thread_id": 152,
        "ThreadState.native_thread_id": 160,
        "InterpreterFrame.f_code": 0,
        "InterpreterFrame.previous": 8,
        "InterpreterFrame.prev_instr": 56,
        "CodeObject.co_firstlineno": 68,
        "CodeObject.co_qualname": 128,
        "CodeObject.co_filename": 112,
        "CodeObject.co_name": 120,
        "CodeObject.co_linetable": 136,
        "CodeObject.co_code_adaptive": 208,
        "LongObject.long_value.lv_tag": 16,
        "LongObject.long_value.ob_digit": 24,
        "digit_size": 4,
        "PyObject_size": 16,
        "Object.ob_type": 8,
        "TypeObject.tp_flags": 168,
        "TypeObject.tp_dictoffset": 288,
        "Py_TPFLAGS_MANAGED_DICT": 16,
        "HeapTypeObject.ht_cached_keys": 880,
        "DictObject.ma_keys": 32,
        "DictObject.ma_values": 40,
        "dictkeysobject_size": 32,
        "dictkeysobject.dk_log2_index_bytes": 9,
        "dictkeysobject.dk_kind": 10,
        "dictkeysobject.dk_nentries": 24,
        "dictvalues_header": 8,
        "PyDictKeyEntry_size": 24,
        "PyDictUnicodeEntry_size": 16,
        "BytesObject.ob_sval": 32,
        "VarObject.ob_size": 16,
        "PyASCIIObject_size": 40,
        "PyCompactUnicodeObject_size": 56,
        "PyUnicodeObject.data_any": 56,
        "_Py_CODEUNIT_size": 2,
    },
}

#: Fallback version used when the target CPython version is not in
#: ``_VERIFIED_OFFSETS`` (复刻约束, 契约 L7).
DEFAULT_VERSION = "3.12"


@dataclass(frozen=True)
class Layout:
    """Immutable per-version struct-offset table (契约 L1/L3/L10/L11).

    ``version`` is the requested version string (preserved for diagnostics),
    ``key`` the major.minor table key actually used, ``verified`` False when
    the table is the DEFAULT_VERSION fallback for an unverified version.
    """

    version: str
    key: str
    verified: bool
    table: MappingProxyType

    def get(self, name: str) -> int:
        """Offset ``name``; KeyError when absent in this version (契约 L5)."""
        return self.table[name]

    def get_or(self, name: str, default=None):
        """Like get(), but ``default`` for keys absent in this version (L5)."""
        return self.table.get(name, default)


def _version_key(version_str: str) -> str:
    """Extract the ``major.minor`` table key from a version string (L2).

    Inputs without a minor component are returned unchanged
    (``"3"`` → ``"3"``, ``""`` → ``""``).
    """
    parts = version_str.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return version_str


def resolve_layout(version_str: str, *, overrides_path: str | None = None) -> Layout:
    """Resolve the layout for a target's CPython version string (L1–L4).

    ``overrides_path=None`` uses the module default (dev-time offsets.json
    next to the package); pass an explicit path in tests.
    """
    req_key = _version_key(version_str)
    table = _VERIFIED_OFFSETS.get(req_key)
    verified = table is not None
    if table is None:
        # L3 (A1 契约变更): 未验证版本回退 DEFAULT_VERSION 表，不抛异常——
        # verified=False 标记，警告串由批次 4 session 组装。
        table = _VERIFIED_OFFSETS[DEFAULT_VERSION]

    # L2/L3: key 恒为 backing 表的版本——verified 取其键，未验证/不可提取
    # 输入（"3"、""）取 DEFAULT_VERSION；override 命中则改写为 override 版本。
    key = req_key if verified else DEFAULT_VERSION

    # L4: dev override 整表替换（不合并——合并会把已删除字段的键渗进会话）。
    # 对 verified 与 fallback 基座同一规则；_version 与请求的 major.minor
    # 匹配才生效，文件不存在则跳过（L12: 默认路径可被指向不存在文件）。
    path = _DEFAULT_OVERRIDES_PATH if overrides_path is None else overrides_path
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        if data.pop("_version", None) == req_key:
            table = data
            key = req_key  # 命中即 override 的 _version（表的真正来源）

    # L10/L11: 拷贝后包 MappingProxy——表不可变，多 Layout 并存互不干扰。
    return Layout(version=version_str, key=key, verified=verified,
                  table=MappingProxyType(dict(table)))


def supported_versions() -> list[str]:
    """Sorted list of verified CPython major.minor keys (契约 L6)."""
    return sorted(_VERIFIED_OFFSETS)
