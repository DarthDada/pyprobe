"""Thread name lookup via threading._active dict traversal (A3).

Walks: sys.modules → "threading" module → its __dict__ → "_active" dict →
per-thread instance dict → "_name". Every failure mode degrades to a
partial or empty mapping — never raises (契约 N1–N6).
"""

from ..kernel.views import PTR_SIZE
from .dicts import DictReader
from .objects import read_long, read_unicode


def get_thread_names(view, layout, interp_addr: int) -> dict[int, str]:
    """Map threading idents to thread names for one interpreter.

    sys.modules is located via ``InterpreterState.imports`` on 3.12 (N1)
    or via the sysdict "modules" key elsewhere. Instance dicts are
    resolved across the 3.11/3.12/3.13 managed-dict variants (N5).
    """
    names = {}

    imports_off = layout.get_or("InterpreterState.imports")
    if imports_off is not None:
        # N1 — 3.12: direct pointer to the sys.modules dict.
        modules_addr = view.read_ptr(
            interp_addr + imports_off + layout.get("_import_state.modules"))
    else:
        # N1 — other versions: walk the sysdict for the "modules" key.
        modules_addr = _find_sys_modules(view, layout, interp_addr)
    if modules_addr is None or modules_addr == 0:
        return names

    # N2 — find the "threading" module in sys.modules.
    mod_it = DictReader(view, layout)
    if not mod_it.from_dict(modules_addr):
        return names

    while True:
        pair = mod_it.next()
        if pair is None:
            break
        key, value = pair
        mod_name = read_unicode(view, layout, key)
        if mod_name is None or mod_name != "threading":
            continue

        # N3 — module __dict__ via ob_type → tp_dictoffset.
        mod_type_addr = view.read_ptr(value + layout.get("Object.ob_type"))
        if mod_type_addr is None:
            break
        dictoffset = view.read_u64(
            mod_type_addr + layout.get("TypeObject.tp_dictoffset"))
        if dictoffset is None or dictoffset == 0:
            break
        mod_dict_addr = view.read_ptr(value + dictoffset)
        if mod_dict_addr is None or mod_dict_addr == 0:
            break

        dict_it = DictReader(view, layout)
        if not dict_it.from_dict(mod_dict_addr):
            break

        while True:
            pair = dict_it.next()
            if pair is None:
                break
            dk, dv = pair
            var_name = read_unicode(view, layout, dk)
            if var_name is None or var_name != "_active":
                continue

            # N4 — _active maps threading idents to Thread objects.
            active_it = DictReader(view, layout)
            if not active_it.from_dict(dv):
                break

            while True:
                pair = active_it.next()
                if pair is None:
                    break
                ak, av = pair
                tid = read_long(view, layout, ak)
                if tid is None:
                    continue  # N4 — unreadable ident: skip the entry

                inst_it = _instance_dict_iter(view, layout, av)
                if inst_it is None:
                    continue  # N5 — no usable instance dict

                while True:
                    pair = inst_it.next()
                    if pair is None:
                        break
                    ik, iv = pair
                    attr_name = read_unicode(view, layout, ik)
                    if attr_name is None or attr_name != "_name":
                        continue
                    thread_name = read_unicode(view, layout, iv)
                    if thread_name is not None:
                        names[tid] = thread_name  # N6
                    break
            return names
    return names


def _find_sys_modules(view, layout, interp_addr: int) -> int | None:
    """Locate sys.modules via the sys module's __dict__ (N1, non-3.12).

    3.12 exposes it directly as ``imports._import_state.modules``; here we
    walk the sysdict dict looking for the "modules" key.
    """
    sysdict = view.read_ptr(
        interp_addr + layout.get("InterpreterState.sysdict"))
    if sysdict is None or sysdict == 0:
        return None

    it = DictReader(view, layout)
    if not it.from_dict(sysdict):
        return None

    while True:
        pair = it.next()
        if pair is None:
            return None
        key, value = pair
        if read_unicode(view, layout, key) == "modules":
            return value


def _instance_dict_iter(view, layout, obj_addr: int) -> DictReader | None:
    """Bind a DictReader to an object's instance dict (N5).

    Handles the managed-dict variants (3.11 pre-header slots / 3.12 tagged
    pointer / 3.13+ embedded values) and the plain tp_dictoffset fallback;
    None when no instance dict is available.
    """
    type_addr = view.read_ptr(obj_addr + layout.get("Object.ob_type"))
    if type_addr is None:
        return None

    flags = view.read_u64(type_addr + layout.get("TypeObject.tp_flags"))
    if flags is None:
        return None

    it = DictReader(view, layout)
    if flags & layout.get("Py_TPFLAGS_MANAGED_DICT"):
        tagged = view.read_ptr(obj_addr - 3 * PTR_SIZE)
        if tagged is None:
            return None
        pre_values = layout.get_or("PyObject.pre_values")
        if pre_values is not None:
            # N5 3.11 — two separate pre-header slots: the dict pointer at
            # obj-3 (NULL until the dict is materialized) and an untagged
            # PyDictValues* at obj-4.
            if tagged:
                return it if it.from_dict(tagged) else None
            values = view.read_ptr(obj_addr + pre_values)
            if values is None or values == 0:
                return None
            return it if it.from_managed_values(values, type_addr) else None
        if tagged & 1:
            # N5 3.12 — bit0 set marks an inline PyDictValues (bare array).
            return it if it.from_managed_values(tagged + 1,
                                                type_addr) else None
        if tagged == 0:
            # N5 — untagged NULL slot. 3.12: the instance dict is simply
            # empty (dictvalues_header==0 there) → nothing to iterate.
            # 3.13+: the values are embedded in the object, after the
            # PyObject header and the 3.13-only PyDictValues header.
            header = layout.get("dictvalues_header")
            if not header:
                return None
            embedded = obj_addr + layout.get("PyObject_size") + header
            return it if it.from_managed_values(embedded, type_addr) else None
        # N5 — untagged non-zero slot: a materialized dict pointer.
        return it if it.from_dict(tagged) else None

    # N5 — non-managed type: plain tp_dictoffset instance dict.
    dictoffset = view.read_u64(
        type_addr + layout.get("TypeObject.tp_dictoffset"))
    if dictoffset is None or dictoffset == 0:
        return None

    dict_addr = view.read_ptr(obj_addr + dictoffset)
    if dict_addr is None or dict_addr == 0:
        return None

    return it if it.from_dict(dict_addr) else None
