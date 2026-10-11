"""Contract tests for cpython/names.py (N1–N6).

The whole sys.modules → threading → _active → instance-dict graph is built
Layout-driven, so each supported version exercises its own offsets and its
own managed-dict variant (N5 分支矩阵: 3.11 pre_values / 3.12 tagged /
3.13+ embedded). The old tree covered this only via integration tests —
this file adds the missing unit-level guard (contracts.md 批 3 旧测试标注).
"""

import struct

import pytest

from pyprobe.cpython.names import get_thread_names
from pyprobe.target.layout import resolve_layout
from tests.fakemem import (
    FakeMemory,
    build_dict,
    build_long,
    build_unicode,
)

NO_OVERRIDE = "/nonexistent/pyprobe-offsets-override.json"

INTERP = 0x200000
SYSDICT, SYSKEYS = 0x210000, 0x220000
MODDICT, MODKEYS = 0x230000, 0x240000
MODOBJ, MODTYPE = 0x250000, 0x260000
MODOBJDICT, MODOBJKEYS = 0x270000, 0x280000
ACTIVE, ACTKEYS = 0x290000, 0x2A0000
TIDLONG = 0x2B0000
THREADOBJ, THREADTYPE = 0x2C0000, 0x2D0000
NAMEKEYS = 0x2E0000
VALUES = 0x2F0000
S_MODULES, S_THREADING, S_ACTIVE, S_NAME, S_VALUE = (
    0x300000, 0x300100, 0x300200, 0x300300, 0x300400)

TID = 12345
NAME = "worker-1"
MOD_DICTOFFSET = 64  # 测试自选 tp_dictoffset（≥ PyObject 头宽即可）


def _build_type(mem, layout, addr, *, flags, dictoffset=0, cached_keys=0):
    """HeapTypeObject with tp_flags/tp_dictoffset/ht_cached_keys."""
    end = max(layout.get("TypeObject.tp_flags"),
              layout.get("TypeObject.tp_dictoffset"),
              layout.get("HeapTypeObject.ht_cached_keys")) + 8
    blob = bytearray(end)
    struct.pack_into("<Q", blob, layout.get("TypeObject.tp_flags"), flags)
    struct.pack_into("<Q", blob, layout.get("TypeObject.tp_dictoffset"),
                     dictoffset)
    struct.pack_into("<Q", blob, layout.get("HeapTypeObject.ht_cached_keys"),
                     cached_keys)
    mem.add(addr, bytes(blob))


def _name_keys(mem, layout):
    """cached keys object holding one entry: key = the "_name" unicode."""
    build_unicode(mem, layout, S_NAME, "_name")
    keys_sz = layout.get("dictkeysobject_size")
    blob = bytearray(keys_sz)
    blob[layout.get("dictkeysobject.dk_kind")] = 0
    struct.pack_into("<q", blob, layout.get("dictkeysobject.dk_nentries"), 1)
    mem.add(NAMEKEYS, bytes(blob))
    # entry: hash=0, key=S_NAME, value ignored (values come from the array)
    mem.add(NAMEKEYS + 1 + keys_sz, struct.pack("<QQQ", 0, S_NAME, 0))


def _build_names_graph(mem, layout, *, with_modules_key=True,
                       with_threading=True, with_active=True,
                       instance_variant="auto"):
    """Assemble the full threading._active graph (happy path by default).

    instance_variant: "auto" picks this layout's managed-dict idiom
    (3.11 pre_values / 3.12 tagged / 3.13+ embedded); "plain" forces the
    non-managed tp_dictoffset path.
    """
    build_unicode(mem, layout, S_MODULES, "modules")
    build_unicode(mem, layout, S_THREADING, "threading")
    build_unicode(mem, layout, S_ACTIVE, "_active")
    build_unicode(mem, layout, S_VALUE, NAME)
    build_long(mem, layout, TIDLONG, TID)

    # threading module object + its __dict__ (N3)
    mem.add_ptr(MODOBJ + layout.get("Object.ob_type"), MODTYPE)
    mem.add_ptr(MODOBJ + MOD_DICTOFFSET, MODOBJDICT)
    _build_type(mem, layout, MODTYPE, flags=0, dictoffset=MOD_DICTOFFSET)

    # "_active" dict {tid: thread_obj} (N4)
    build_long(mem, layout, TIDLONG, TID)
    active_entries = [(TIDLONG, THREADOBJ)] if with_active else []
    build_dict(mem, layout, ACTIVE, ACTKEYS, active_entries, kind=0)
    modobjdict_entries = [(S_ACTIVE, ACTIVE)] if with_active else []
    build_dict(mem, layout, MODOBJDICT, MODOBJKEYS, modobjdict_entries,
               kind=1)

    # sys.modules dict {"threading": module} (N2)
    modules_entries = [(S_THREADING, MODOBJ)] if with_threading else []
    build_dict(mem, layout, MODDICT, MODKEYS, modules_entries, kind=1)

    # interpreter: 3.12 uses imports._import_state.modules (N1 直针),
    # other versions walk sysdict for the "modules" key (N1 sysdict 分支)
    imports_off = layout.get_or("InterpreterState.imports")
    if imports_off is not None:
        mem.add_ptr(INTERP + imports_off
                    + layout.get("_import_state.modules"), MODDICT)
    else:
        sysdict_entries = [(S_MODULES, MODDICT)] if with_modules_key else []
        build_dict(mem, layout, SYSDICT, SYSKEYS, sysdict_entries, kind=1)
        mem.add_ptr(INTERP + layout.get("InterpreterState.sysdict"), SYSDICT)

    # thread instance + its "_name" via the version's dict idiom (N5/N6)
    _name_keys(mem, layout)
    mem.add_ptr(VALUES, S_VALUE)  # values array: one slot = name unicode
    mem.add_ptr(THREADOBJ + layout.get("Object.ob_type"), THREADTYPE)
    managed = layout.get("Py_TPFLAGS_MANAGED_DICT")
    if instance_variant == "plain":
        _build_type(mem, layout, THREADTYPE, flags=0,
                    dictoffset=MOD_DICTOFFSET)
        inst_entries = [(S_NAME, S_VALUE)]
        build_dict(mem, layout, MODOBJDICT + 0x9000, MODOBJKEYS + 0x9000,
                   inst_entries, kind=1)
        mem.add_ptr(THREADOBJ + MOD_DICTOFFSET, MODOBJDICT + 0x9000)
        return

    _build_type(mem, layout, THREADTYPE, flags=managed, cached_keys=NAMEKEYS)
    slot = THREADOBJ - 3 * 8
    variant = instance_variant
    if variant == "auto":
        if layout.get_or("PyObject.pre_values") is not None:
            variant = "311"
        elif layout.get("dictvalues_header") == 0:
            variant = "312"
        else:
            variant = "313"
    if variant == "311":
        # 3.11: obj-3 dict 槽为 0 → obj+pre_values 非标记 values 数组
        mem.add_ptr(slot, 0)
        mem.add_ptr(THREADOBJ + layout.get("PyObject.pre_values"), VALUES)
    elif variant == "312":
        # 3.12: obj-3 槽 = values_addr − 1（bit0 标记内联 values）
        mem.add_ptr(slot, VALUES - 1)
    else:
        # 3.13/3.14: 槽为 0 → values 内嵌于对象头 + dictvalues_header 之后
        mem.add_ptr(slot, 0)
        embedded = THREADOBJ + layout.get("PyObject_size") \
            + layout.get("dictvalues_header")
        mem.add_ptr(embedded, S_VALUE)


class TestHappyPathPerVersion:
    @pytest.mark.parametrize("version", ["3.11", "3.12", "3.13", "3.14"])
    def test_thread_name_resolved(self, version):
        """N1–N6 全链路：各版本各自的 sys.modules 定位与实例字典变体下，
        threading ident → "_name" 字符串解析成功（矩阵即版本分支守护）。"""
        lay = resolve_layout(version, overrides_path=NO_OVERRIDE)
        mem = FakeMemory()
        _build_names_graph(mem, lay)
        assert get_thread_names(mem, lay, INTERP) == {TID: NAME}

    def test_plain_instance_dict_via_tp_dictoffset(self):
        """N5 非 managed 分支：无 MANAGED_DICT 标志时走 tp_dictoffset
        常规实例 dict（C 扩展类型的实例形态）。"""
        lay = resolve_layout("3.13", overrides_path=NO_OVERRIDE)
        mem = FakeMemory()
        _build_names_graph(mem, lay, instance_variant="plain")
        assert get_thread_names(mem, lay, INTERP) == {TID: NAME}

    def test_312_null_slot_means_empty_dict(self):
        """N5 3.12 边界：tagged==0 且 dictvalues_header==0 → 空实例 dict
        （跳过该线程，不得误读对象头为 values 数组）。"""
        lay = resolve_layout("3.12", overrides_path=NO_OVERRIDE)
        mem = FakeMemory()
        _build_names_graph(mem, lay)
        mem.add_ptr(THREADOBJ - 3 * 8, 0)  # 覆盖 auto 构造的 tagged 槽
        assert get_thread_names(mem, lay, INTERP) == {}


class TestDegradation:
    """任何一段失败 → 部分/空 dict，不抛异常（N 系共同失败语义）。"""

    def test_sysdict_without_modules_key(self):
        """N1 失败模式（sysdict 分支）：无 "modules" 键 → 空 dict。"""
        lay = resolve_layout("3.13", overrides_path=NO_OVERRIDE)
        mem = FakeMemory()
        _build_names_graph(mem, lay, with_modules_key=False)
        assert get_thread_names(mem, lay, INTERP) == {}

    def test_modules_without_threading(self):
        """N2 失败模式：sys.modules 无 "threading" → 空 dict。"""
        lay = resolve_layout("3.12", overrides_path=NO_OVERRIDE)
        mem = FakeMemory()
        _build_names_graph(mem, lay, with_threading=False)
        assert get_thread_names(mem, lay, INTERP) == {}

    def test_module_dict_without_active(self):
        """N3/N4 失败模式：threading 无 "_active" → 空 dict。"""
        lay = resolve_layout("3.13", overrides_path=NO_OVERRIDE)
        mem = FakeMemory()
        _build_names_graph(mem, lay, with_active=False)
        assert get_thread_names(mem, lay, INTERP) == {}

    def test_unreadable_interpreter(self):
        """N 系总失败模式：interp 不可读 → 空 dict（观测不炸）。"""
        lay = resolve_layout("3.13", overrides_path=NO_OVERRIDE)
        assert get_thread_names(FakeMemory(), lay, INTERP) == {}
