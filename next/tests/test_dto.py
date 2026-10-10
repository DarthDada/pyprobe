"""Contract tests for pyprobe.dto (contracts.md 批 1 表 D1/D2, ADR A5).

D1: field names/order/defaults replicate old ``types.py`` — the
``dataclasses.asdict`` key sets ARE the JSON output schema (复刻约束).
D2: DTOs are pure data — no ``format()``/``format_header()`` methods, no
color imports (those moved to present/text.py, batch 6).
"""

import dataclasses

from pyprobe import dto


def _keys(instance):
    return list(dataclasses.asdict(instance))


def test_frame_info_fields():
    assert _keys(dto.FrameInfo("f", "f.py", 1)) == ["name", "filename", "line"]


def test_thread_info_fields_and_defaults():
    t = dto.ThreadInfo(7101)
    assert _keys(dto.ThreadInfo(7101)) == [
        "native_tid", "thread_id", "name", "frames", "idle"]
    assert (t.thread_id, t.name, t.frames, t.idle) == (0, "", [], False)


def test_process_info_fields_and_defaults():
    p = dto.ProcessInfo(100, "python3 x.py", "/usr/bin/python3")
    assert _keys(dto.ProcessInfo(100, "c", "/x")) == [
        "pid", "cmdline", "exe_path", "python_version"]
    assert p.python_version == "?"


def test_native_frame_fields_and_defaults():
    f = dto.NativeFrame(0xDEADBEEF)
    assert _keys(dto.NativeFrame(1)) == ["pc", "symbol", "module"]
    assert (f.symbol, f.module) == ("??", None)


def test_native_thread_info_fields_and_defaults():
    t = dto.NativeThreadInfo(7101)
    assert _keys(dto.NativeThreadInfo(7101)) == [
        "tid", "comm", "frames", "unwind_failed"]
    assert (t.comm, t.frames, t.unwind_failed) == ("", [], False)


def test_syscall_event_fields_and_defaults():
    e = dto.SyscallEvent(7101, 257, "openat")
    assert _keys(dto.SyscallEvent(7101, 257, "openat")) == [
        "tid", "nr", "name", "args", "rendered", "ret", "error", "elapsed"]
    assert (e.args, e.rendered, e.ret, e.error, e.elapsed) == (
        [], "", 0, None, 0.0)


def test_profile_data_fields_and_defaults():
    p = dto.ProfileData(dto.ProcessInfo(100, "c", "/x"))
    assert list(dataclasses.asdict(p)) == [
        "proc_info", "counts", "samples", "idle_samples", "elapsed",
        "version_warning"]
    assert (p.counts, p.samples, p.idle_samples, p.elapsed,
            p.version_warning) == ({}, 0, 0, 0.0, None)


def test_dtos_have_no_format_methods():
    # A5 guard: presentation must live in present/, not on the data objects.
    for cls in (dto.FrameInfo, dto.ThreadInfo, dto.ProcessInfo,
                dto.NativeFrame, dto.NativeThreadInfo, dto.SyscallEvent,
                dto.ProfileData):
        assert not hasattr(cls, "format"), cls.__name__
        assert not hasattr(cls, "format_header"), cls.__name__


def test_dto_imports_no_presentation_modules():
    # A5/A8 guard: dto must not import presentation machinery. (Checked at
    # the import level, not docstring prose — docstrings may *mention*
    # present/text.py as a pointer. test_structure.py pins the rule across
    # the whole tree.)
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(dto))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert "colors" not in name
            assert "present" not in name
