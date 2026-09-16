/* gen_offsets.c - Generate CPython struct offsets as JSON for Python port.
 *
 * Build:
 *   cc -O2 -I$(python3-config --includes) -DPy_BUILD_CORE -DPy_BUILD_CORE_BUILTIN \
 *      -o gen_offsets gen_offsets.c && ./gen_offsets > offsets.json
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
    printf("  \"pointer_size\": %zu,\n", S(void *));
    printf("  \"int_size\": %zu,\n", S(int));

    /* _PyRuntimeState */
    printf("  \"RuntimeState.interpreters\": %zu,\n", O((_PyRuntimeState, interpreters)));
    printf("  \"pyinterpreters.main\": %zu,\n", O((struct pyinterpreters, main)));
    printf("  \"pyinterpreters.head\": %zu,\n", O((struct pyinterpreters, head)));

    /* PyInterpreterState */
    printf("  \"InterpreterState.threads\": %zu,\n", O((PyInterpreterState, threads)));
    printf("  \"pythreads.head\": %zu,\n", O((struct pythreads, head)));
    printf("  \"InterpreterState.imports\": %zu,\n", O((PyInterpreterState, imports)));
    printf("  \"_import_state.modules\": %zu,\n", O((struct _import_state, modules)));
    printf("  \"InterpreterState.interpreter_trampoline\": %zu,\n",
           O((PyInterpreterState, interpreter_trampoline)));

    /* PyThreadState */
    printf("  \"ThreadState.next\": %zu,\n", O((PyThreadState, next)));
    printf("  \"ThreadState.cframe\": %zu,\n", O((PyThreadState, cframe)));
    printf("  \"ThreadState.thread_id\": %zu,\n", O((PyThreadState, thread_id)));
    printf("  \"ThreadState.native_thread_id\": %zu,\n", O((PyThreadState, native_thread_id)));

    /* _PyCFrame */
    printf("  \"CFrame.current_frame\": %zu,\n", O((_PyCFrame, current_frame)));

    /* _PyInterpreterFrame */
    printf("  \"InterpreterFrame.f_code\": %zu,\n", O((_PyInterpreterFrame, f_code)));
    printf("  \"InterpreterFrame.previous\": %zu,\n", O((_PyInterpreterFrame, previous)));
    printf("  \"InterpreterFrame.prev_instr\": %zu,\n", O((_PyInterpreterFrame, prev_instr)));

    /* PyCodeObject */
    printf("  \"CodeObject.co_firstlineno\": %zu,\n", O((PyCodeObject, co_firstlineno)));
    printf("  \"CodeObject.co_qualname\": %zu,\n", O((PyCodeObject, co_qualname)));
    printf("  \"CodeObject.co_filename\": %zu,\n", O((PyCodeObject, co_filename)));
    printf("  \"CodeObject.co_name\": %zu,\n", O((PyCodeObject, co_name)));
    printf("  \"CodeObject.co_linetable\": %zu,\n", O((PyCodeObject, co_linetable)));
    printf("  \"CodeObject.co_code_adaptive\": %zu,\n", O((PyCodeObject, co_code_adaptive)));

    /* PyLongObject */
    printf("  \"LongObject.long_value.lv_tag\": %zu,\n", O((PyLongObject, long_value.lv_tag)));
    printf("  \"LongObject.long_value.ob_digit\": %zu,\n", O((PyLongObject, long_value.ob_digit)));
    printf("  \"digit_size\": %zu,\n", S(digit));

    /* PyObject / PyTypeObject */
    printf("  \"Object.ob_type\": %zu,\n", O((PyObject, ob_type)));
    printf("  \"TypeObject.tp_flags\": %zu,\n", O((PyTypeObject, tp_flags)));
    printf("  \"TypeObject.tp_dictoffset\": %zu,\n", O((PyTypeObject, tp_dictoffset)));
    printf("  \"Py_TPFLAGS_MANAGED_DICT\": %lu,\n", (unsigned long)Py_TPFLAGS_MANAGED_DICT);

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
