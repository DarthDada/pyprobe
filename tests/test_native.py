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
    """

    def __init__(self, threads=(), begin_ok=True, report_rc=0, attach_rc=0):
        self.threads = list(threads)
        self.begin_ok = begin_ok
        self.report_rc = report_rc
        self.attach_rc = attach_rc
        self.calls = []
        self.addrname_lookups = []  # NA4: lookup 地址记录（pc−1 语义守护）
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
        """NA6（复刻旧判定）：getframes 非 0 且 0 帧 → unwind_failed=True；
        有帧则 False（§4 P0-3 修正为冻结期新功能，不实施）。"""
        fake_libdw(FakeLibdw(threads=[
            {"tid": 100, "comm": "", "frames": [], "getframes_rc": -1},
            {"tid": 101, "comm": "",
             "frames": [(0x1000, True, "f", None)], "getframes_rc": -1}]))
        monkeypatch.setattr(nat_mod, "read_comm", lambda pid, tid: "")
        threads = collect_native(PID)
        assert threads[0].unwind_failed is True
        assert threads[1].unwind_failed is False
