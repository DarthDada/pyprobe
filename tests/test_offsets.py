"""Unit tests for pyprobe.offsets — version handling, configure, fallback."""

import json

import pytest

from pyprobe import offsets
from pyprobe.errors import VersionNotSupported


class TestVersionKey:
    def test_full_version(self):
        assert offsets._version_key("3.12.13") == "3.12"

    def test_major_minor(self):
        assert offsets._version_key("3.11") == "3.11"

    def test_major_only(self):
        assert offsets._version_key("3") == "3"

    def test_empty(self):
        assert offsets._version_key("") == ""


class TestSupportedVersions:
    @pytest.mark.parametrize("version", ["3.11", "3.12", "3.13"])
    def test_contains_version(self, version):
        assert version in offsets.supported_versions()

    def test_sorted(self):
        versions = offsets.supported_versions()
        assert versions == sorted(versions)


class TestConfigure:
    def test_known_version_sets_active(self):
        offsets.configure("3.12.5")
        assert offsets.get("pointer_size") == 8

    def test_unknown_version_raises_and_falls_back(self, capsys):
        """Unverified version raises VersionNotSupported *after* populating
        ``_active`` with the default version's offsets (TODO §8.5).  No
        stderr printing happens at the offsets layer — the caller decides
        whether to surface the warning (collect layer catches and stores it
        on the ProcessSession; the dump layer prints it).
        """
        with pytest.raises(VersionNotSupported) as exc_info:
            offsets.configure("9.9.1")
        assert exc_info.value.version == "9.9.1"
        assert exc_info.value.fallback == "3.12"
        # _active is still populated (fallback) so callers that catch can
        # immediately use offsets.get / get_or.
        assert offsets.get("pointer_size") == 8
        # No printing at the offsets layer.
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_default_version(self):
        assert offsets.DEFAULT_VERSION == "3.12"

    def test_get_before_configure_uses_default(self):
        offsets._active = None
        assert offsets.get("pointer_size") == 8


class TestDevOverride:
    """offsets.json dev-time override semantics.

    A matching ``_version`` makes the json file — the complete gen_offsets
    output, generated from the requested version's real headers — *replace*
    the active table wholesale.  Merging it over the fallback base would
    leak keys whose struct field no longer exists in that version (e.g.
    3.12's ``InterpreterState.imports`` into a 3.14 session, sending
    ``get_thread_names`` down the wrong version branch).
    """

    def _write_override(self, tmp_path, monkeypatch, payload):
        path = tmp_path / "offsets.json"
        path.write_text(json.dumps(payload))
        monkeypatch.setattr(offsets, "_OVERRIDES_PATH", str(path))

    def test_matching_override_replaces_fallback_entirely(self, tmp_path, monkeypatch):
        self._write_override(tmp_path, monkeypatch, {
            "_version": "9.9", "pointer_size": 8, "custom.key": 42})
        with pytest.raises(VersionNotSupported):
            offsets.configure("9.9.1")
        assert set(offsets._active) == {"pointer_size", "custom.key"}
        assert offsets.get("pointer_size") == 8
        assert offsets.get("custom.key") == 42
        assert offsets.get_or("InterpreterState.imports") is None

    def test_non_matching_override_ignored(self, tmp_path, monkeypatch):
        self._write_override(tmp_path, monkeypatch, {
            "_version": "3.12", "pointer_size": 99})
        with pytest.raises(VersionNotSupported):
            offsets.configure("9.9.1")
        assert set(offsets._active) == set(offsets._VERIFIED_OFFSETS["3.12"])
        assert offsets.get("pointer_size") == 8

    def test_override_replaces_verified_table_too(self, tmp_path, monkeypatch):
        self._write_override(tmp_path, monkeypatch, {
            "_version": "3.13", "pointer_size": 8, "custom.key": 7})
        offsets.configure("3.13.5")
        assert set(offsets._active) == {"pointer_size", "custom.key"}
        assert offsets.get("custom.key") == 7


#: Test oracle, intentionally independent of ``_VERIFIED_OFFSETS`` so that
#: table regressions are caught instead of tautologically confirmed.  Keys
#: present in every supported version's table:
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
#: divergence between versions).  Adding a new CPython version is TDD-style:
#: write the expected extras here first (tests go red), then fill in
#: ``_VERIFIED_OFFSETS`` (tests go green).  Versions with no extra keys must
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

_ALL_VERSIONS = sorted(set(_VERSION_EXTRA_KEYS) | set(offsets._VERIFIED_OFFSETS))


def _expected_keys(version):
    return _SHARED_KEYS | _VERSION_EXTRA_KEYS.get(version, frozenset())


class TestOffsetsTable:
    @pytest.mark.parametrize("version,key", [
        (v, k) for v in _ALL_VERSIONS for k in sorted(_expected_keys(v))
    ])
    def test_key_present(self, version, key):
        offsets.configure(version)
        assert offsets.get(key) == offsets._VERIFIED_OFFSETS[version][key]
        assert isinstance(offsets.get(key), int)

    @pytest.mark.parametrize("version", _ALL_VERSIONS)
    def test_table_has_exactly_expected_keys(self, version):
        offsets.configure(version)
        assert set(offsets._active) == _expected_keys(version)

    def test_oracle_versions_match_supported(self):
        oracle_only = set(_VERSION_EXTRA_KEYS) - set(offsets._VERIFIED_OFFSETS)
        uncovered = set(offsets._VERIFIED_OFFSETS) - set(_VERSION_EXTRA_KEYS)
        assert not oracle_only, (
            f"versions expected by the test oracle but missing from "
            f"_VERIFIED_OFFSETS (fill in the table): {sorted(oracle_only)}")
        assert not uncovered, (
            f"versions in _VERIFIED_OFFSETS but not covered by the test "
            f"oracle (update _VERSION_EXTRA_KEYS): {sorted(uncovered)}")

    @pytest.mark.parametrize("version", _ALL_VERSIONS)
    def test_keyerror_for_missing_key(self, version):
        offsets.configure(version)
        with pytest.raises(KeyError):
            offsets.get("nonexistent.key")

    @pytest.mark.parametrize("version", _ALL_VERSIONS)
    def test_active_is_copy(self, version):
        """Mutating the returned active dict must not corrupt the verified table."""
        offsets.configure(version)
        original = offsets._VERIFIED_OFFSETS[version]["pointer_size"]
        offsets._active["pointer_size"] = 999
        assert offsets._VERIFIED_OFFSETS[version]["pointer_size"] == original
