/* gen_offsets.c - Generate CPython struct offsets as JSON for Python port.
 *
 * Build:
 *   cc -O2 -I$(python3-config --includes) -DPy_BUILD_CORE -DPy_BUILD_CORE_BUILTIN \
 *      -o gen_offsets gen_offsets.c && ./gen_offsets > offsets.json
 *
 * Version compatibility:
 *  - Keys whose struct field does not exist in a version are simply omitted
 *    from that version's output; the Python side treats a missing key as
 *    "feature unavailable" (offsets.get_or).
 *  - 3.11/3.12 share the same _dictvalues bare-array layout, but differ in
 *    the managed-dict pre-header: 3.11 keeps a separate untagged
 *    PyDictValues* at obj-4 (emitted as PyObject.pre_values; dict pointer
 *    at obj-3 stays NULL until the dict is materialized), while 3.12 merged
 *    both slots into one tagged pointer at obj-3.
 *  - 3.13 renamed _PyInterpreterFrame.f_code -> f_executable and
 *    prev_instr -> instr_ptr, removed PyInterpreterState.interpreter_trampoline
 *    and the tstate->cframe->current_frame indirection (replaced by a direct
 *    PyThreadState.current_frame field); renamed fields are emitted under the
 *    legacy key names (pyprobe semantics).
 *  - "_version" tags the output so the runtime override (offsets.json) only
 *    applies when it matches the target process's CPython version.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stddef.h>
#include <stdint.h>
#include <Python.h>
#include <internal/pycore_runtime.h>
#include <internal/pycore_interp.h>
#include <internal/pycore_frame.h>
#include <internal/pycore_code.h>
#include <internal/pycore_dict.h>
#include <internal/pycore_object.h>

#define O(field)  ((size_t)(offsetof field))
#define S(type)   ((size_t)(sizeof(type)))

int main(void)
{
    printf("{\n");
    printf("  \"_version\": \"%d.%d\",\n", PY_MAJOR_VERSION, PY_MINOR_VERSION);
    printf("  \"pointer_size\": %zu,\n", S(void *));
    printf("  \"int_size\": %zu,\n", S(int));

    /* _PyRuntimeState */
    printf("  \"RuntimeState.interpreters\": %zu,\n", O((_PyRuntimeState, interpreters)));
    printf("  \"pyinterpreters.main\": %zu,\n", O((struct pyinterpreters, main)));
    printf("  \"pyinterpreters.head\": %zu,\n", O((struct pyinterpreters, head)));

    /* PyInterpreterState */
    printf("  \"InterpreterState.threads\": %zu,\n", O((PyInterpreterState, threads)));
    printf("  \"pythreads.head\": %zu,\n", O((struct pythreads, head)));
    printf("  \"InterpreterState.sysdict\": %zu,\n", O((PyInterpreterState, sysdict)));
#if PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030D0000
    /* the _import_state struct and the interpreter trampoline are 3.12 only;
     * on 3.11 sys.modules must be reached via the sysdict dict instead, and in
     * 3.13 the trampoline was removed (direct PyThreadState.current_frame) */
    printf("  \"InterpreterState.imports\": %zu,\n", O((PyInterpreterState, imports)));
    printf("  \"_import_state.modules\": %zu,\n", O((struct _import_state, modules)));
    printf("  \"InterpreterState.interpreter_trampoline\": %zu,\n",
           O((PyInterpreterState, interpreter_trampoline)));
#endif

    /* PyThreadState */
    printf("  \"ThreadState.next\": %zu,\n", O((PyThreadState, next)));
#if PY_VERSION_HEX >= 0x030D0000
    /* the cframe indirection was removed in 3.13: current_frame is direct */
    printf("  \"ThreadState.current_frame\": %zu,\n", O((PyThreadState, current_frame)));
#else
    printf("  \"ThreadState.cframe\": %zu,\n", O((PyThreadState, cframe)));
#endif
    printf("  \"ThreadState.thread_id\": %zu,\n", O((PyThreadState, thread_id)));
    printf("  \"ThreadState.native_thread_id\": %zu,\n", O((PyThreadState, native_thread_id)));

    /* _PyCFrame */
#if PY_VERSION_HEX < 0x030D0000
    printf("  \"CFrame.current_frame\": %zu,\n", O((_PyCFrame, current_frame)));
#endif

    /* _PyInterpreterFrame */
#if PY_VERSION_HEX >= 0x030D0000
    printf("  \"InterpreterFrame.f_code\": %zu,\n", O((_PyInterpreterFrame, f_executable)));
    printf("  \"InterpreterFrame.previous\": %zu,\n", O((_PyInterpreterFrame, previous)));
    printf("  \"InterpreterFrame.prev_instr\": %zu,\n", O((_PyInterpreterFrame, instr_ptr)));
#else
    printf("  \"InterpreterFrame.f_code\": %zu,\n", O((_PyInterpreterFrame, f_code)));
    printf("  \"InterpreterFrame.previous\": %zu,\n", O((_PyInterpreterFrame, previous)));
    printf("  \"InterpreterFrame.prev_instr\": %zu,\n", O((_PyInterpreterFrame, prev_instr)));
#endif

    /* PyCodeObject */
    printf("  \"CodeObject.co_firstlineno\": %zu,\n", O((PyCodeObject, co_firstlineno)));
    printf("  \"CodeObject.co_qualname\": %zu,\n", O((PyCodeObject, co_qualname)));
    printf("  \"CodeObject.co_filename\": %zu,\n", O((PyCodeObject, co_filename)));
    printf("  \"CodeObject.co_name\": %zu,\n", O((PyCodeObject, co_name)));
    printf("  \"CodeObject.co_linetable\": %zu,\n", O((PyCodeObject, co_linetable)));
    printf("  \"CodeObject.co_code_adaptive\": %zu,\n", O((PyCodeObject, co_code_adaptive)));

    /* PyLongObject: the nested _PyLongValue struct is 3.12+; 3.11 stores a
     * plain signed ob_size (sign of the int abuses the sign bit) */
#if PY_VERSION_HEX >= 0x030C0000
    printf("  \"LongObject.long_value.lv_tag\": %zu,\n", O((PyLongObject, long_value.lv_tag)));
    printf("  \"LongObject.long_value.ob_digit\": %zu,\n", O((PyLongObject, long_value.ob_digit)));
#else
    /* ob_size comes from PyObject_VAR_HEAD (inherited via ob_base) */
    printf("  \"LongObject.ob_size\": %zu,\n", O((PyLongObject, ob_base.ob_size)));
    printf("  \"LongObject.ob_digit\": %zu,\n", O((PyLongObject, ob_digit)));
#endif
    printf("  \"digit_size\": %zu,\n", S(digit));

    /* PyObject / PyTypeObject */
    printf("  \"PyObject_size\": %zu,\n", S(PyObject));
    printf("  \"Object.ob_type\": %zu,\n", O((PyObject, ob_type)));
    printf("  \"TypeObject.tp_flags\": %zu,\n", O((PyTypeObject, tp_flags)));
    printf("  \"TypeObject.tp_dictoffset\": %zu,\n", O((PyTypeObject, tp_dictoffset)));
#ifdef Py_TPFLAGS_MANAGED_DICT
    printf("  \"Py_TPFLAGS_MANAGED_DICT\": %lu,\n", (unsigned long)Py_TPFLAGS_MANAGED_DICT);
#else
    /* added in 3.12; on 3.11 the value 0 makes the Python side use the
     * tp_dictoffset path, which is the only correct one there */
    printf("  \"Py_TPFLAGS_MANAGED_DICT\": 0,\n");
#endif
#if PY_VERSION_HEX < 0x030C0000
    /* 3.11: _PyObject_ValuesPointer(obj) is ((PyDictValues **)obj)-4 — a
     * separate untagged values slot; the dict slot at obj-3 is only filled
     * once the dict is materialized. 3.12+ merged the two slots. */
    printf("  \"PyObject.pre_values\": %ld,\n",
           -(long)(4 * sizeof(PyObject *)));
#endif

    /* PyHeapTypeObject */
    printf("  \"HeapTypeObject.ht_cached_keys\": %zu,\n", O((PyHeapTypeObject, ht_cached_keys)));

    /* PyDictObject */
    printf("  \"DictObject.ma_keys\": %zu,\n", O((PyDictObject, ma_keys)));
    printf("  \"DictObject.ma_values\": %zu,\n", O((PyDictObject, ma_values)));

    /* _dictkeysobject */
    printf("  \"dictkeysobject_size\": %zu,\n", S(struct _dictkeysobject));
    printf("  \"dictkeysobject.dk_log2_index_bytes\": %zu,\n",
           O((struct _dictkeysobject, dk_log2_index_bytes)));
    printf("  \"dictkeysobject.dk_kind\": %zu,\n", O((struct _dictkeysobject, dk_kind)));
    printf("  \"dictkeysobject.dk_nentries\": %zu,\n", O((struct _dictkeysobject, dk_nentries)));

    /* _dictvalues: 3.13 prepends a capacity/size/embedded/valid header before
     * the values array; 3.11/3.12 store the bare array */
    printf("  \"dictvalues_header\": %zu,\n", O((struct _dictvalues, values)));

    /* Dict entry sizes */
    printf("  \"PyDictKeyEntry_size\": %zu,\n", S(PyDictKeyEntry));
    printf("  \"PyDictUnicodeEntry_size\": %zu,\n", S(PyDictUnicodeEntry));

    /* PyBytesObject */
    printf("  \"BytesObject.ob_sval\": %zu,\n", O((PyBytesObject, ob_sval)));
    printf("  \"VarObject.ob_size\": %zu,\n", O((PyVarObject, ob_size)));

    /* Unicode objects */
    printf("  \"PyASCIIObject_size\": %zu,\n", S(PyASCIIObject));
    printf("  \"PyCompactUnicodeObject_size\": %zu,\n", S(PyCompactUnicodeObject));
    printf("  \"PyUnicodeObject.data_any\": %zu,\n", O((PyUnicodeObject, data.any)));

    printf("  \"_Py_CODEUNIT_size\": %zu\n", S(_Py_CODEUNIT));
    printf("}\n");
    return 0;
}
