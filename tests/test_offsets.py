"""Unit tests for pyprobe.offsets — version handling, configure, fallback."""

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
