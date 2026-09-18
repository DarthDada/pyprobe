"""Thread name lookup via threading._active dict traversal."""

from .memory import RemoteReader, PTR_SIZE
from . import offsets
from .dict_iter import DictIter
from .pyobject import read_pylong, read_pyunicode


def _find_sys_modules(reader, interp_addr):
    """Locate sys.modules via the sys module's __dict__ (3.11/3.13).

    3.12 exposes it directly as ``imports._import_state.modules``; elsewhere
    we walk the sysdict dict looking for the "modules" key.
    """
    sysdict = reader.read_ptr(
        interp_addr + offsets.get("InterpreterState.sysdict"))
    if sysdict is None or sysdict == 0:
        return None

    it = DictIter(reader)
    if not it.from_dict(sysdict):
        return None

    while True:
        pair = it.next()
        if pair is None:
            return None
        key, value = pair
        if read_pyunicode(reader, key) == "modules":
            return value


def get_thread_names(reader, interp_addr):
    names = {}

    imports_off = offsets.get_or("InterpreterState.imports")
    if imports_off is not None:
        # 3.12: direct pointer to the sys.modules dict
        mod_off = imports_off + offsets.get("_import_state.modules")
        modules_addr = reader.read_ptr(interp_addr + mod_off)
        if modules_addr is None or modules_addr == 0:
            return names
    else:
        modules_addr = _find_sys_modules(reader, interp_addr)
        if modules_addr is None or modules_addr == 0:
            return names

    mod_it = DictIter(reader)
    if not mod_it.from_dict(modules_addr):
        return names

    while True:
        pair = mod_it.next()
        if pair is None:
            break
        key, value = pair
        mod_name = read_pyunicode(reader, key)
        if mod_name is None or mod_name != "threading":
            continue

        mod_type_addr = reader.read_ptr(value + offsets.get("Object.ob_type"))
        if mod_type_addr is None:
            break
        dictoffset = reader.read_u64(
            mod_type_addr + offsets.get("TypeObject.tp_dictoffset"))
        if dictoffset is None or dictoffset == 0:
            break
        mod_dict_addr = reader.read_ptr(value + dictoffset)
        if mod_dict_addr is None or mod_dict_addr == 0:
            break

        dict_it = DictIter(reader)
        if not dict_it.from_dict(mod_dict_addr):
            break

        while True:
            pair = dict_it.next()
            if pair is None:
                break
            dk, dv = pair
            var_name = read_pyunicode(reader, dk)
            if var_name is None or var_name != "_active":
                continue

            active_it = DictIter(reader)
            if not active_it.from_dict(dv):
                break

            while True:
                pair = active_it.next()
                if pair is None:
                    break
                ak, av = pair
                tid = read_pylong(reader, ak)
                if tid is None:
                    continue

                inst_it = DictIter(reader)
                if not _get_instance_dict_iter(inst_it, reader, av):
                    continue

                while True:
                    pair = inst_it.next()
                    if pair is None:
                        break
                    ik, iv = pair
                    attr_name = read_pyunicode(reader, ik)
                    if attr_name is None or attr_name != "_name":
                        continue
                    thread_name = read_pyunicode(reader, iv)
                    if thread_name is not None:
                        names[tid] = thread_name
                    break
            return names
    return names


def _get_instance_dict_iter(it, reader, obj_addr):
    type_addr = reader.read_ptr(obj_addr + offsets.get("Object.ob_type"))
    if type_addr is None:
        return False

    flags = reader.read_u64(type_addr + offsets.get("TypeObject.tp_flags"))
    if flags is None:
        return False

    if flags & offsets.get("Py_TPFLAGS_MANAGED_DICT"):
        tagged = reader.read_ptr(obj_addr - 3 * PTR_SIZE)
        if tagged is None:
            return False
        pre_values = offsets.get_or("PyObject.pre_values")
        if pre_values is not None:
            # 3.11: two separate pre-header slots — the dict pointer at
            # obj-3 (NULL until the dict is materialized) and an untagged
            # PyDictValues* at obj-4.
            if tagged:
                return it.from_dict(tagged)
            values = reader.read_ptr(obj_addr + pre_values)
            if values is None or values == 0:
                return False
            return it.from_managed_values(values, type_addr)
        if tagged & 1:
            # 3.12: bit0 set marks an inline PyDictValues (bare array)
            return it.from_managed_values(tagged + 1, type_addr)
        if tagged == 0:
            # 3.13: untagged pointer; NULL means the values are embedded in
            # the object, after the PyObject header and the 3.13-only
            # PyDictValues header. On 3.12 NULL means an empty dict.
            header = offsets.get("dictvalues_header")
            if not header:
                return False
            off = offsets.get("PyObject_size") + header
            return it.from_managed_values(obj_addr + off, type_addr)
        return it.from_dict(tagged)

    dictoffset = reader.read_u64(type_addr + offsets.get("TypeObject.tp_dictoffset"))
    if dictoffset is None or dictoffset == 0:
        return False

    dict_addr = reader.read_ptr(obj_addr + dictoffset)
    if dict_addr is None or dict_addr == 0:
        return False

    return it.from_dict(dict_addr)
