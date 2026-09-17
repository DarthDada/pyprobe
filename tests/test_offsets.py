"""Unit tests for pyprobe.offsets — version handling, configure, fallback."""

import pytest

from pyprobe import offsets


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
    def test_contains_312(self):
        assert "3.12" in offsets.supported_versions()

    def test_sorted(self):
        versions = offsets.supported_versions()
        assert versions == sorted(versions)


class TestConfigure:
    def test_known_version_sets_active(self):
        offsets.configure("3.12.5")
        assert offsets.get("pointer_size") == 8

    def test_unknown_version_falls_back(self, capsys):
        offsets.configure("9.9.1")
        captured = capsys.readouterr()
        assert "not a verified version" in captured.err
        assert offsets.get("pointer_size") == 8

    def test_default_version(self):
        assert offsets._DEFAULT_VERSION == "3.12"

    def test_get_before_configure_uses_default(self):
        offsets._active = None
        assert offsets.get("pointer_size") == 8


class TestOffsetsTable:
    @pytest.mark.parametrize("key", [
        "pointer_size", "int_size",
        "RuntimeState.interpreters", "pyinterpreters.main", "pyinterpreters.head",
        "InterpreterState.threads", "pythreads.head",
        "InterpreterState.interpreter_trampoline",
        "ThreadState.next", "ThreadState.cframe", "ThreadState.thread_id",
        "ThreadState.native_thread_id",
        "CFrame.current_frame",
        "InterpreterFrame.f_code", "InterpreterFrame.previous",
        "InterpreterFrame.prev_instr",
        "CodeObject.co_firstlineno", "CodeObject.co_qualname",
        "CodeObject.co_filename", "CodeObject.co_name",
        "CodeObject.co_linetable", "CodeObject.co_code_adaptive",
        "LongObject.long_value.lv_tag", "LongObject.long_value.ob_digit",
        "digit_size", "Object.ob_type", "TypeObject.tp_flags",
        "TypeObject.tp_dictoffset", "Py_TPFLAGS_MANAGED_DICT",
        "HeapTypeObject.ht_cached_keys",
        "DictObject.ma_keys", "DictObject.ma_values",
        "dictkeysobject_size", "dictkeysobject.dk_log2_index_bytes",
        "dictkeysobject.dk_kind", "dictkeysobject.dk_nentries",
        "PyDictKeyEntry_size", "PyDictUnicodeEntry_size",
        "BytesObject.ob_sval", "VarObject.ob_size",
        "PyASCIIObject_size", "PyCompactUnicodeObject_size",
        "PyUnicodeObject.data_any", "_Py_CODEUNIT_size",
    ])
    def test_key_present(self, key):
        offsets.configure("3.12")
        val = offsets.get(key)
        assert isinstance(val, int)
        assert val >= 0

    def test_keyerror_for_missing_key(self):
        offsets.configure("3.12")
        with pytest.raises(KeyError):
            offsets.get("nonexistent.key")

    def test_active_is_copy(self):
        """Mutating the returned active dict must not corrupt the verified table."""
        offsets.configure("3.12")
        original = offsets._VERIFIED_OFFSETS["3.12"]["pointer_size"]
        offsets._active["pointer_size"] = 999
        assert offsets._VERIFIED_OFFSETS["3.12"]["pointer_size"] == original
