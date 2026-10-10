"""Contract tests for target/layout.py — Layout value object (L1–L12, A1).

The key-set oracle (_SHARED_KEYS / _VERSION_EXTRA_KEYS) is intentionally
independent of ``_VERIFIED_OFFSETS`` so table regressions are caught instead
of tautologically confirmed (old test_offsets.py pattern, retained per
测试数据纪律 — the oracle is the authority, the table is the suspect).
"""

import json
from pathlib import Path

import pytest

import pyprobe.target.layout as layout_mod
from pyprobe.target.layout import (
    _VERIFIED_OFFSETS,
    DEFAULT_VERSION,
    resolve_layout,
    supported_versions,
)

NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"


class TestVersionKey:
    """L2: major.minor 提取的各种输入形态（override 匹配与表查找的地基，
    错位即整表错配）。提取规则直接钉私有函数（旧 test_offsets.py 同法）。"""

    def test_full_version(self):
        assert layout_mod._version_key("3.12.13") == "3.12"

    def test_major_minor(self):
        assert layout_mod._version_key("3.11") == "3.11"

    def test_major_only(self):
        assert layout_mod._version_key("3") == "3"

    def test_empty(self):
        assert layout_mod._version_key("") == ""


class TestLayoutKeySemantics:
    """L2/L3：Layout.key 恒为 backing 表的版本——verified 版本取其键，
    未验证/不可提取输入取 DEFAULT_VERSION，override 命中取 override 版本。"""

    def test_verified_key(self):
        lay = resolve_layout("3.12.13", overrides_path=NO_OVERRIDE)
        assert lay.key == "3.12"

    def test_unextractable_input_uses_default_key(self):
        """L3 边界：无 minor 段的输入（"3"、""）同样回退——key 记录实际
        使用的表（DEFAULT_VERSION），而非原样保留垃圾输入误导诊断。"""
        for bad in ("3", ""):
            lay = resolve_layout(bad, overrides_path=NO_OVERRIDE)
            assert lay.key == DEFAULT_VERSION
            assert lay.verified is False


class TestResolve:
    def test_verified_full_version(self):
        """L1: 全版本串解析到对应表且 verified=True。"""
        lay = resolve_layout("3.12.13", overrides_path=NO_OVERRIDE)
        assert lay.verified is True
        assert lay.version == "3.12.13"
        assert lay.get("pointer_size") == 8

    def test_unverified_falls_back_without_raising(self):
        """L3 (A1 契约变更): 未验证版本 → DEFAULT_VERSION 表 + verified=False，
        不抛异常（旧 configure 先填后抛；警告组装责任在批次 4 session）。"""
        lay = resolve_layout("9.9.1", overrides_path=NO_OVERRIDE)
        assert lay.verified is False
        assert lay.version == "9.9.1"  # requested 版本保留供诊断
        assert lay.key == DEFAULT_VERSION
        assert lay.get("pointer_size") == 8

    def test_default_version(self):
        """L7: 回退基座版本 pin（复刻约束）。"""
        assert DEFAULT_VERSION == "3.12"

    def test_supported_versions(self):
        """L6: 支持列表升序且覆盖 3.11–3.14（覆盖面契约）。"""
        versions = supported_versions()
        assert versions == sorted(versions)
        assert {"3.11", "3.12", "3.13", "3.14"} <= set(versions)

    def test_multiple_layouts_coexist(self):
        """L11 (A1 核心收益): 两个不同版本的 Layout 并存互不干扰——旧全局
        单例下不可能的形态，防实现私藏共享可变状态。"""
        a = resolve_layout("3.12", overrides_path=NO_OVERRIDE)
        b = resolve_layout("3.13", overrides_path=NO_OVERRIDE)
        assert a.get("InterpreterState.sysdict") == 352
        assert b.get("InterpreterState.sysdict") == 7640
        # 再次取 a 的键不被 b 的解析污染
        assert a.get("InterpreterState.sysdict") == 352


class TestGetSemantics:
    def test_get_missing_key_raises_keyerror(self):
        """L5: 缺键 → KeyError（版本间字段缺失必须显式暴露，防静默拿错值）。"""
        lay = resolve_layout("3.13", overrides_path=NO_OVERRIDE)
        with pytest.raises(KeyError):
            lay.get("InterpreterState.imports")  # 3.13 无此字段

    def test_get_or_returns_default(self):
        """L5: get_or 缺键回 default——版本分支的合法探针。"""
        lay = resolve_layout("3.13", overrides_path=NO_OVERRIDE)
        assert lay.get_or("InterpreterState.imports") is None
        assert lay.get_or("pointer_size") == 8

    def test_table_is_immutable(self):
        """L10: 表不可变（旧 test_active_is_copy 的防污染语义由结构保证，
        而非靠"记得拷贝"的纪律）。"""
        lay = resolve_layout("3.12", overrides_path=NO_OVERRIDE)
        with pytest.raises(TypeError):
            lay.table["pointer_size"] = 999
        assert lay.get("pointer_size") == 8


class TestDevOverride:
    """L4: dev override 语义（整表替换，不合并——合并会把已删除字段的键
    渗进新版本会话，旧 offsets.py docstring 的教训）。"""

    def _write(self, tmp_path, payload):
        path = tmp_path / "offsets.json"
        path.write_text(json.dumps(payload))
        return str(path)

    def test_matching_override_replaces_fallback_entirely(self, tmp_path):
        path = self._write(tmp_path, {
            "_version": "9.9", "pointer_size": 8, "custom.key": 42})
        lay = resolve_layout("9.9.1", overrides_path=path)
        assert set(lay.table) == {"pointer_size", "custom.key"}
        assert lay.get("custom.key") == 42
        assert lay.get_or("InterpreterState.imports") is None
        # L2/L3：override 命中时 key 记 override 版本（表的真正来源）
        assert lay.key == "9.9"

    def test_non_matching_override_ignored(self, tmp_path):
        path = self._write(tmp_path, {"_version": "3.12", "pointer_size": 99})
        lay = resolve_layout("9.9.1", overrides_path=path)
        assert lay.get("pointer_size") == 8  # fallback 表，非 override 的 99

    def test_override_replaces_verified_table_too(self, tmp_path):
        path = self._write(tmp_path, {
            "_version": "3.13", "pointer_size": 8, "custom.key": 7})
        lay = resolve_layout("3.13.5", overrides_path=path)
        assert set(lay.table) == {"pointer_size", "custom.key"}


class TestDefaultOverridePath:
    def test_conftest_isolates_default_path(self):
        """L12 (§10.2-7): conftest 已把默认 override 路径指到不存在文件——
        套件验证的是内置表而非 tracked offsets.json；此测试钉住该机制本身。"""
        assert not Path(layout_mod._DEFAULT_OVERRIDES_PATH).exists()

    def test_default_path_used_when_no_param(self):
        """L4/L12 配套：不传 overrides_path 时走模块默认（此处即隔离后的
        不存在路径）→ 内置表原样生效。"""
        lay = resolve_layout("3.12.13")
        assert lay.get("pointer_size") == 8
        assert set(lay.table) == set(_VERIFIED_OFFSETS["3.12"])


#: Keys present in every supported version's table (independent oracle).
_SHARED_KEYS = frozenset({
    "pointer_size", "int_size",
    "RuntimeState.interpreters", "pyinterpreters.main", "pyinterpreters.head",
    "InterpreterState.threads", "pythreads.head", "InterpreterState.sysdict",
    "ThreadState.next", "ThreadState.thread_id", "ThreadState.native_thread_id",
    "InterpreterFrame.f_code", "InterpreterFrame.previous",
    "InterpreterFrame.prev_instr",
    "CodeObject.co_firstlineno", "CodeObject.co_qualname",
    "CodeObject.co_filename", "CodeObject.co_name",
    "CodeObject.co_linetable", "CodeObject.co_code_adaptive",
    "digit_size", "PyObject_size", "Object.ob_type",
    "TypeObject.tp_flags", "TypeObject.tp_dictoffset",
    "Py_TPFLAGS_MANAGED_DICT",
    "HeapTypeObject.ht_cached_keys",
    "DictObject.ma_keys", "DictObject.ma_values",
    "dictkeysobject_size", "dictkeysobject.dk_log2_index_bytes",
    "dictkeysobject.dk_kind", "dictkeysobject.dk_nentries",
    "dictvalues_header",
    "PyDictKeyEntry_size", "PyDictUnicodeEntry_size",
    "BytesObject.ob_sval", "VarObject.ob_size",
    "PyASCIIObject_size", "PyCompactUnicodeObject_size",
    "PyUnicodeObject.data_any", "_Py_CODEUNIT_size",
})

#: Keys that exist only in specific CPython versions (struct layout
#: divergence). Adding a new CPython version is TDD-style: write the
#: expected extras here first (tests go red), then fill in
#: ``_VERIFIED_OFFSETS`` (tests go green). Versions with no extra keys must
#: still be listed with an empty set.
_VERSION_EXTRA_KEYS = {
    "3.11": frozenset({
        "ThreadState.cframe", "CFrame.current_frame",
        "LongObject.ob_size", "LongObject.ob_digit",
        "PyObject.pre_values",
    }),
    "3.12": frozenset({
        "ThreadState.cframe", "CFrame.current_frame",
        "InterpreterState.imports", "_import_state.modules",
        "InterpreterState.interpreter_trampoline",
        "LongObject.long_value.lv_tag", "LongObject.long_value.ob_digit",
    }),
    "3.13": frozenset({
        "ThreadState.current_frame",
        "LongObject.long_value.lv_tag", "LongObject.long_value.ob_digit",
    }),
    "3.14": frozenset({
        "ThreadState.current_frame",
        "LongObject.long_value.lv_tag", "LongObject.long_value.ob_digit",
    }),
}

_ALL_VERSIONS = sorted(set(_VERSION_EXTRA_KEYS) | set(_VERIFIED_OFFSETS))


def _expected_keys(version):
    return _SHARED_KEYS | _VERSION_EXTRA_KEYS.get(version, frozenset())


class TestOffsetsTable:
    """L8: 键集独立 oracle——防表回归而非自指确认。"""

    @pytest.mark.parametrize("version,key", [
        (v, k) for v in _ALL_VERSIONS for k in sorted(_expected_keys(v))
    ])
    def test_key_present(self, version, key):
        """L8: 每个预期键都在表中且为 int（值由 spec-oracle L9 另行钉对）。"""
        lay = resolve_layout(version, overrides_path=NO_OVERRIDE)
        assert lay.get(key) == _VERIFIED_OFFSETS[version][key]
        assert isinstance(lay.get(key), int)

    @pytest.mark.parametrize("version", _ALL_VERSIONS)
    def test_table_has_exactly_expected_keys(self, version):
        """L8: 键集恰好为 oracle 预期（少键=功能缺失，多键=未知漂移）。"""
        lay = resolve_layout(version, overrides_path=NO_OVERRIDE)
        assert set(lay.table) == _expected_keys(version)

    def test_oracle_versions_match_supported(self):
        """L8: oracle 与表互为封面——防加了版本忘更 oracle 或反之。"""
        oracle_only = set(_VERSION_EXTRA_KEYS) - set(_VERIFIED_OFFSETS)
        uncovered = set(_VERIFIED_OFFSETS) - set(_VERSION_EXTRA_KEYS)
        assert not oracle_only, (
            f"versions expected by the test oracle but missing from "
            f"_VERIFIED_OFFSETS (fill in the table): {sorted(oracle_only)}")
        assert not uncovered, (
            f"versions in _VERIFIED_OFFSETS but not covered by the test "
            f"oracle (update _VERSION_EXTRA_KEYS): {sorted(uncovered)}")


class TestSpecOracle:
    """L9 (§10.2-1): 内置表 vs gen_offsets 真实头文件产物（入库 offsets.json）。"""

    def test_builtin_table_matches_generated_json(self):
        """L9: json._version 对应的内置表必须与生成器输出逐键一致（生成器
        从真实 CPython 头文件产出，是布局知识的权威来源）。"""
        data = json.loads(
            (Path(layout_mod.__file__).parent.parent / "offsets.json")
            .read_text())
        json_version = data.pop("_version")
        table = _VERIFIED_OFFSETS.get(json_version)
        if table is None:  # pragma: no cover - dev machine without match
            pytest.skip(f"no built-in table for json version {json_version}")
        assert dict(table) == data
