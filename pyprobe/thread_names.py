"""Thread name lookup via threading._active dict traversal."""

from .memory import RemoteReader, PTR_SIZE
from . import offsets
from .dict_iter import DictIter
from .pyobject import read_pylong, read_pyunicode


def get_thread_names(reader, interp_addr):
    names = {}

    mod_off = (offsets.get("InterpreterState.imports")
               + offsets.get("_import_state.modules"))
    modules_addr = reader.read_ptr(interp_addr + mod_off)
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
        if tagged is None or tagged == 0:
            return False
        if tagged & 1:
            return it.from_managed_values(tagged + 1, type_addr)
        else:
            return it.from_dict(tagged)

    dictoffset = reader.read_u64(type_addr + offsets.get("TypeObject.tp_dictoffset"))
    if dictoffset is None or dictoffset == 0:
        return False

    dict_addr = reader.read_ptr(obj_addr + dictoffset)
    if dict_addr is None or dict_addr == 0:
        return False

    return it.from_dict(dict_addr)
