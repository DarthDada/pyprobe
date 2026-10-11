"""Contract tests for observe/native.py (NA1–NA8).

libdw is replaced by a FakeLibdw scripting threads/frames at the C API
surface (token pointers map to scripted data), so dwfl wiring, frame
mapping, and error paths are covered without elfutils. The ptrace side
uses a StubEngine at the PtraceEngine seam (A2 调用序列守护 NA8).
The old tree had no unit coverage here (integration-or-skip only) — this
file adds the missing guards (contracts.md 批 5 旧测试标注).
"""

import ctypes

import pytest

import pyprobe.observe.native as nat_mod
from pyprobe.errors import AttachFailed
from pyprobe.observe.native import collect_native

PID = 4242


class StubEngine:
    def __init__(self, pid, *, feature="ptrace", restart_op=24):
        self.pid = pid
        self.feature = feature
        self.calls = []

    def seize(self, options=0):
        self.calls.append(("seize", options))

    def stop_all(self):
        self.calls.append(("stop_all",))

    def detach(self):
        self.calls.append(("detach",))


class FakeLibdw:
    """Scripts the dwfl C API: threads → frames → (pc, isact, symbol, module).

    threads: list of dicts {tid, comm, frames, getframes_rc}
    frames:  list of (pc, isact, symbol|None, module_path|None)
    src_map: optional {pc: (filename, line)} — DWARF source positions (A12)
    """

    def __init__(self, threads=(), begin_ok=True, report_rc=0, attach_rc=0,
                 src_map=None):
        self.threads = list(threads)
        self.begin_ok = begin_ok
        self.report_rc = report_rc
        self.attach_rc = attach_rc
        self.calls = []
        self.addrname_lookups = []  # NA4: lookup 地址记录（pc−1 语义守护）
        self.src_map = src_map or {}  # NA11: pc → (filename, line)
        self._tokens = {}

    # -- helpers ---------------------------------------------------------
    def _token(self, key):
        tok = ctypes.c_void_p(hash(key) & 0x7FFFFFFF)
        self._tokens[tok.value] = key
        return ctypes.pointer(tok)

    # -- dwfl API surface -------------------------------------------------
    def dwfl_begin(self, callbacks):
        self.calls.append("dwfl_begin")
        return self._token("dwfl") if self.begin_ok else None

    def dwfl_end(self, dwfl):
        self.calls.append("dwfl_end")

    def dwfl_errmsg(self, code):
        return b"fake dwfl error"

    def dwfl_linux_proc_report(self, dwfl, pid):
        self.calls.append(("dwfl_linux_proc_report", pid))
        return self.report_rc

    def dwfl_report_end(self, dwfl, a, b):
        self.calls.append("dwfl_report_end")

    def dwfl_linux_proc_attach(self, dwfl, pid, assume_stopped):
        self.calls.append(("dwfl_linux_proc_attach", pid, assume_stopped))
        return self.attach_rc

    def dwfl_getthreads(self, dwfl, cb, arg):
        self.calls.append("dwfl_getthreads")
        for i, _t in enumerate(self.threads):
            cb(self._token(("thread", i)), None)
        return 0

    def dwfl_thread_getframes(self, thread, cb, arg):
        key = self._tokens[thread.contents.value]
        t = self.threads[key[1]]
        for j, _f in enumerate(t["frames"]):
            if cb(self._token(("frame", key[1], j)), arg) != 0:
                break
        return t.get("getframes_rc", 0)

    def dwfl_frame_pc(self, frame, pc_ref, isact_ref):
        key = self._tokens[frame.contents.value]
        pc, isact, _sym, _mod = self.threads[key[1]]["frames"][key[2]]
        ctypes.cast(pc_ref, ctypes.POINTER(ctypes.c_uint64)).contents.value = pc
        ctypes.cast(isact_ref, ctypes.POINTER(ctypes.c_bool)).contents.value = \
            isact
        return True

    def dwfl_thread_tid(self, thread):
        key = self._tokens[thread.contents.value]
        return self.threads[key[1]]["tid"]

    def dwfl_frame_thread(self, frame):
        return frame

    def dwfl_thread_dwfl(self, thread):
        return self._token("dwfl")

    def dwfl_addrmodule(self, dwfl, addr):
        self.calls.append(("dwfl_addrmodule", addr))
        return self._token(("mod", addr))

    def _frame_for(self, mod_tok):
        """Resolve the scripted frame a module token was minted for."""
        addr = self._tokens[mod_tok.contents.value][1]
        for t in self.threads:
            for pc, _isact, _sym, _m in t["frames"]:
                if addr in (pc, pc - 1):
                    return pc, _isact, _sym, _m
        return None

    def _pc_for_mod(self, mod_tok):
        frame = self._frame_for(mod_tok)
        return frame[0] if frame else None

    def dwfl_module_getsrc(self, mod, addr):
        """NA11: Dwarf_Line* for a module address with DWARF line info."""
        pc = self._pc_for_mod(mod)
        if pc in self.src_map:
            return self._token(("src", pc))
        return None

    def dwfl_lineinfo(self, line, addr_ref, linep, colp, mtime, length):
        """NA11: fill *linep and return the filename for a Dwarf_Line*."""
        key = self._tokens[line.contents.value]
        fname, lineno = self.src_map[key[1]]
        if linep:
            ctypes.cast(linep,
                        ctypes.POINTER(ctypes.c_int)).contents.value = lineno
        return fname.encode() if fname else None

    def dwfl_module_addrname(self, mod, addr):
        self.addrname_lookups.append(addr)
        frame = self._frame_for(mod)
        if frame is None or frame[2] is None:
            return None
        return frame[2].encode()

    def dwfl_module_info(self, mod, a, b, c, d, e, mainfile_ref, g):
        frame = self._frame_for(mod)
        if frame is not None and frame[3]:
            ctypes.cast(mainfile_ref, ctypes.POINTER(
                ctypes.c_char_p)).contents.value = frame[3].encode()
        return b"fake-mod"


@pytest.fixture
def fake_libdw(monkeypatch):
    """Install FakeLibdw at the libdw seam (bypassing real _init_libs)."""
    holder = {}

    def install(fake):
        holder["fake"] = fake
        monkeypatch.setattr(nat_mod, "_init_libs",
                            lambda: setattr(nat_mod, "libdw", fake))

    install.fake = holder
    return install


@pytest.fixture
def stub_engine(monkeypatch):
    created = []

    def factory(pid, **kwargs):
        eng = StubEngine(pid, **kwargs)
        created.append(eng)
        return eng

    monkeypatch.setattr(nat_mod, "PtraceEngine", factory)
    return created


class TestEngineIntegration:
    def test_engine_sequence(self, fake_libdw, stub_engine, monkeypatch):
        """NA8（A2 迁移）：seize(options=0) → stop_all → dwfl 展开 →
        detach 的调用序列；feature 名透传；assume_ptrace_stopped=True。"""
        fake_libdw(FakeLibdw(threads=[
            {"tid": 100, "comm": "python3",
             "frames": [(0x1000, True, "main", "/usr/bin/python3.12")]}]))
        monkeypatch.setattr(nat_mod, "read_comm",
                            lambda pid, tid: "python3")
        collect_native(PID)
        eng = stub_engine[0]
        assert eng.feature == "native stack dump"
        assert eng.calls[0] == ("seize", 0)
        assert eng.calls[1] == ("stop_all",)
        assert eng.calls[-1] == ("detach",)
        fake = fake_libdw.fake["fake"]
        assert ("dwfl_linux_proc_attach", PID, True) in fake.calls

    def test_detach_on_dwfl_error(self, fake_libdw, stub_engine, monkeypatch):
        """NA8 失败模式：dwfl report 失败仍 detach（finally 语义——tracee
        不得滞留停止态）。"""
        fake_libdw(FakeLibdw(report_rc=-1))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        with pytest.raises(AttachFailed):
            collect_native(PID)
        assert stub_engine[0].calls[-1] == ("detach",)


class TestDwflSetup:
    def test_begin_failure(self, fake_libdw, stub_engine, monkeypatch):
        """NA2 失败模式：dwfl_begin 失败 → AttachFailed 带 dwfl 错误信息。"""
        fake_libdw(FakeLibdw(begin_ok=False))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        with pytest.raises(AttachFailed, match="dwfl_begin failed"):
            collect_native(PID)

    def test_report_failure(self, fake_libdw, stub_engine, monkeypatch):
        """NA3 失败模式：proc_report 失败 → AttachFailed（strerror 诊断）。"""
        fake_libdw(FakeLibdw(report_rc=-1))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        with pytest.raises(AttachFailed, match="dwfl_linux_proc_report"):
            collect_native(PID)

    def test_attach_failure(self, fake_libdw, stub_engine, monkeypatch):
        """NA3 失败模式：proc_attach 失败 → AttachFailed。"""
        fake_libdw(FakeLibdw(attach_rc=-1))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        with pytest.raises(AttachFailed, match="dwfl_linux_proc_attach"):
            collect_native(PID)


class TestFrameMapping:
    def test_frames_and_symbols(self, fake_libdw, stub_engine, monkeypatch):
        """NA4：帧 pc/符号/模块路径映射；结果线程按 tid 升序（NA7）。"""
        fake_libdw(FakeLibdw(threads=[
            {"tid": 102, "comm": "worker",
             "frames": [(0x2000, True, "worker_fn", "/lib/w.so")]},
            {"tid": 101, "comm": "main",
             "frames": [(0x1000, True, "main", "/usr/bin/python3.12"),
                        (0x1500, True, "PyRun", "/usr/bin/python3.12")]}]))
        comms = {101: "main", 102: "worker"}
        monkeypatch.setattr(nat_mod, "read_comm",
                            lambda pid, tid: comms[tid])
        threads = collect_native(PID)
        assert [t.tid for t in threads] == [101, 102]  # NA7 升序
        main = threads[0]
        assert main.comm == "main"
        assert [(f.pc, f.symbol, f.module) for f in main.frames] == [
            (0x1000, "main", "/usr/bin/python3.12"),
            (0x1500, "PyRun", "/usr/bin/python3.12")]

    def test_non_activated_frame_lookup_pc_minus_one(self, fake_libdw,
                                                     stub_engine,
                                                     monkeypatch):
        """NA4：非激活帧以 pc−1 查符号（返回地址语义——调用点在下一条
        指令，减一回到调用指令内）。"""
        fake_libdw(FakeLibdw(threads=[
            {"tid": 100, "comm": "",
             "frames": [(0x1000, True, "top", None),
                        (0x2000, False, "caller", None)]}]))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        fake = fake_libdw.fake["fake"]
        collect_native(PID)
        assert fake.addrname_lookups == [0x1000, 0x1FFF]

    def test_missing_symbol_is_question_marks(self, fake_libdw, stub_engine,
                                              monkeypatch):
        """NA4 失败模式：符号缺失 → "??"（strip 过的二进制常态占位）。"""
        fake_libdw(FakeLibdw(threads=[
            {"tid": 100, "comm": "", "frames": [(0x9000, True, None, None)]}]))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        # dwfl_addrmodule 返回模块但 addrname 命中不了 → 需 FakeLibdw 支持
        # 无符号帧：sym=None → addrname 返回 None
        threads = collect_native(PID)
        assert threads[0].frames[0].symbol == "??"

    def test_frame_cap(self, fake_libdw, stub_engine, monkeypatch):
        """NA5：单线程帧上限 MAX_FRAMES=256（失控展开防护，复刻约束）。"""
        frames = [(0x1000 + i * 16, True, f"f{i}", None) for i in range(300)]
        fake_libdw(FakeLibdw(threads=[{"tid": 100, "comm": "",
                                       "frames": frames}]))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        threads = collect_native(PID)
        assert len(threads[0].frames) == 256

    def test_unwind_failed_flag(self, fake_libdw, stub_engine, monkeypatch):
        """NA6（ADR A10 修正）：getframes 非 0 且 0 帧 → unwind_failed=True；
        有真实帧则 False（错误不掩蔽已读到的帧）；lone pc=0 哨兵帧（gdb
        "Backtrace stopped" 场景，如 io_uring iou-sqp-*）亦为 True。"""
        fake_libdw(FakeLibdw(threads=[
            {"tid": 100, "comm": "", "frames": [], "getframes_rc": -1},
            {"tid": 101, "comm": "",
             "frames": [(0x1000, True, "f", None)], "getframes_rc": -1},
            {"tid": 102, "comm": "",
             "frames": [(0x0, True, "??", None)], "getframes_rc": 0}]))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        threads = collect_native(PID)
        assert threads[0].unwind_failed is True    # 无帧 + dwfl 错误
        assert threads[1].unwind_failed is False   # 有真实帧
        assert threads[2].unwind_failed is True    # lone pc=0 哨兵帧


class TestSourceLocation:
    """NA11（ADR A12 / TODO §3 P1-5）：帧源码位置 file:line 解析。"""

    def test_resolves_file_and_line(self, fake_libdw, stub_engine, monkeypatch):
        """有 DWARF 行表且能力位具备 → 帧带 filename/line。"""
        fake_libdw(FakeLibdw(
            threads=[{"tid": 100, "comm": "uvloop",
                      "frames": [(0x1000, True, "uv__io_poll", "/lib/loop.so")]}],
            src_map={0x1000: ("src/unix/linux.c", 1432)}))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "uvloop")
        monkeypatch.setattr(nat_mod, "available_symbols",
                            frozenset({"dwfl_module_getsrc", "dwfl_lineinfo"}))
        threads = collect_native(PID)
        f = threads[0].frames[0]
        assert (f.filename, f.line) == ("src/unix/linux.c", 1432)

    def test_absent_when_capability_missing(self, fake_libdw, stub_engine,
                                            monkeypatch):
        """失败模式：库缺 dwfl_module_getsrc/lineinfo 能力位 → None（保底
        NA4 的 from module，不崩）。"""
        fake_libdw(FakeLibdw(
            threads=[{"tid": 100, "comm": "",
                      "frames": [(0x1000, True, "f", "/lib/x.so")]}],
            src_map={0x1000: ("x.c", 3)}))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        monkeypatch.setattr(nat_mod, "available_symbols", frozenset())
        threads = collect_native(PID)
        assert (threads[0].frames[0].filename,
                threads[0].frames[0].line) == (None, None)

    def test_no_dwarf_leaves_none(self, fake_libdw, stub_engine, monkeypatch):
        """失败模式：模块无 DWARF（getsrc 返回 NULL）→ filename/line 为 None
        （Python 主程序无 .debug_* 的常态，与 gdb 持平）。"""
        fake_libdw(FakeLibdw(
            threads=[{"tid": 100, "comm": "",
                      "frames": [(0x1000, True, "PyRun",
                                  "/usr/bin/python3.12")]}]))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        monkeypatch.setattr(nat_mod, "available_symbols",
                            frozenset({"dwfl_module_getsrc", "dwfl_lineinfo"}))
        threads = collect_native(PID)
        assert (threads[0].frames[0].filename,
                threads[0].frames[0].line) == (None, None)


class TestLibdwLoad:
    """NA1（ADR A9 / TODO §3 P0-1）：libdw 加载名回退链。

    防护：ldconfig 只注册 ``libdw.so.1`` 或 ``libdw.so`` 其一的环境，以及
    ``find_library("dw")`` 末位兜底；首候选命中即停（不产生多余 dlopen）。
    """

    def test_prefers_primary_soname(self):
        """首候选 libdw.so.1 命中即返回，不再尝试后续候选。"""
        tried = []
        sentinel = object()

        def loader(name, use_errno=False):
            tried.append(name)
            return sentinel

        got = nat_mod._load_libdw(loader=loader, finder=lambda _n: None)
        assert got is sentinel
        assert tried == ["libdw.so.1"]

    def test_falls_back_to_bare_soname(self):
        """libdw.so.1 缺失 → 回退 libdw.so（只注册其一的 ldconfig 环境）。"""
        tried = []
        sentinel = object()

        def loader(name, use_errno=False):
            tried.append(name)
            if name == "libdw.so.1":
                raise OSError("libdw.so.1: cannot open shared object file")
            return sentinel

        got = nat_mod._load_libdw(loader=loader, finder=lambda _n: None)
        assert got is sentinel
        assert tried == ["libdw.so.1", "libdw.so"]

    def test_falls_back_to_find_library(self):
        """两个 soname 均缺失 → 末位尝试 find_library("dw") 的结果。"""
        tried = []
        sentinel = object()

        def loader(name, use_errno=False):
            tried.append(name)
            if name == "/usr/lib/libdw.so.2":
                return sentinel
            raise OSError(name)

        got = nat_mod._load_libdw(
            loader=loader, finder=lambda _n: "/usr/lib/libdw.so.2")
        assert got is sentinel
        assert tried == ["libdw.so.1", "libdw.so", "/usr/lib/libdw.so.2"]

    def test_find_library_result_not_duplicated(self):
        """find_library 返回已尝试过的 soname 时不重复尝试。"""
        tried = []
        sentinel = object()

        def loader(name, use_errno=False):
            tried.append(name)
            if name == "libdw.so":
                return sentinel
            raise OSError(name)

        got = nat_mod._load_libdw(loader=loader, finder=lambda _n: "libdw.so")
        assert got is sentinel
        assert tried == ["libdw.so.1", "libdw.so"]

    def test_raises_last_error_when_all_fail(self):
        """全部候选失败 → 抛最后的 OSError（不得静默吞掉加载失败）。"""
        def loader(name, use_errno=False):
            raise OSError(f"cannot load {name}")

        with pytest.raises(OSError, match=r"libdw\.so$"):
            nat_mod._load_libdw(loader=loader, finder=lambda _n: None)


class TestOptionalSymbolProbe:
    """NA10（ADR A9 / TODO §3 P0-2）：可选符号探测降级。

    防护：旧 elfutils（缺 dwarf_getscopes/dwfl_module_getsrc/dwarf_cfi_addrframe）
    下核心"符号名 + 回溯"必须照常；P1/P2 特性按能力位跳过而非崩溃。
    """

    def test_reports_present_subset(self):
        """只把实际导出的可选符号记为可用。"""
        class Lib:
            dwarf_getscopes = object()
            dwfl_module_getsrc = object()

            def __getattr__(self, name):
                raise AttributeError(name)

        assert nat_mod._probe_optional(Lib()) == frozenset(
            {"dwarf_getscopes", "dwfl_module_getsrc"})

    def test_empty_when_none_present(self):
        """一个可选符号都没有 → 空集合（核心路径不受影响）。"""
        class Lib:
            def __getattr__(self, name):
                raise AttributeError(name)

        assert nat_mod._probe_optional(Lib()) == frozenset()


class _Slot:
    """Attribute sink: stands in for a writable ctypes function pointer."""


class _AnyLib:
    """Stand-in for ctypes.CDLL: any attribute resolves to a fresh _Slot."""

    def __getattr__(self, name):
        return _Slot()


class TestInitLibs:
    """NA1/NA10（ADR A9）：_init_libs 经 _load_libdw 取库并探测可选符号。"""

    def test_init_libs_loads_then_probes(self, monkeypatch):
        """_load_libdw 取库 → _probe_optional 探测 → 结果落到 available_symbols。"""
        fake = _AnyLib()
        monkeypatch.setattr(nat_mod, "libdw", None)
        monkeypatch.setattr(nat_mod, "available_symbols", frozenset())
        monkeypatch.setattr(nat_mod, "_libdw_loaded", False)
        monkeypatch.setattr(nat_mod, "_load_libdw", lambda **kw: fake)
        monkeypatch.setattr(
            nat_mod, "_probe_optional",
            lambda lib: frozenset({"dwarf_getscopes"}) if lib is fake
            else frozenset())
        nat_mod._init_libs()
        assert nat_mod.libdw is fake
        assert nat_mod.available_symbols == frozenset({"dwarf_getscopes"})
