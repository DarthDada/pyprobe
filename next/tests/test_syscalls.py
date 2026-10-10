"""Contract tests for observe/syscalls.py (SY1–SY10).

The tracing consumer is driven by a StubEngine at the PtraceEngine seam
(scripted events/regs, recorded calls), so the full pairing/emit state
machine is covered without real ptrace. Argument decoding uses FakeMemory
via the LiveView seam. A7 修复守护：SY5（execve 走用户过滤器）、
SY8（混合表达式 include−exclude）、SY10（timespec_out exit 重读、
ret==0 buf_in 空串）。
"""

import struct
from types import SimpleNamespace

import pytest

import pyprobe.observe.syscalls as sc_mod
from pyprobe.errors import AttachFailed
from pyprobe.kernel.ptrace import (
    ARG_REGS,
    ExecEvent,
    Exited,
    SyscallStop,
)
from pyprobe.observe.syscalls import (
    PTRACE_OPTIONS,
    TraceFilter,
    collect_syscalls,
    decode_args,
    decode_flags,
    escape_bytes,
    fill_out_args,
    read_available,
    read_cstr,
    read_timespec,
    render_scalar,
    render_str_arg,
    truncate_escaped,
)
from tests.fakemem import FakeMemory

PID = 4242


# ---------------------------------------------------------------------------
# Engine seam stubs
# ---------------------------------------------------------------------------

class StubEngine:
    """Scripted PtraceEngine double: queued wait_event results, per-tid
    regs queues, full call recording."""

    def __init__(self, pid, *, feature="ptrace", restart_op=24):
        self.pid = pid
        self.feature = feature
        self.restart_op = restart_op
        self.calls = []
        self.events = []
        self.regs = {}

    def seize(self, options=0):
        self.calls.append(("seize", options))

    def stop_all(self):
        self.calls.append(("stop_all",))

    def restart_all(self):
        self.calls.append(("restart_all",))

    def resume(self, tid, sig=0):
        self.calls.append(("resume", tid, sig))

    def wait_event(self):
        if not self.events:
            return None
        ev = self.events.pop(0)
        if isinstance(ev, BaseException):
            raise ev
        return ev

    def getregs(self, tid):
        r = self.regs[tid]
        return r.pop(0) if isinstance(r, list) else r

    def detach(self):
        self.calls.append(("detach",))


def make_regs(nr, rax=0, args=(0, 0, 0, 0, 0, 0)):
    """Fake UserRegs: orig_rax/rax + the six arg registers by name."""
    ns = SimpleNamespace(orig_rax=nr, rax=rax)
    for reg, val in zip(ARG_REGS, args, strict=True):
        setattr(ns, reg, val)
    return ns


@pytest.fixture
def engine_factory(monkeypatch):
    """Install StubEngine at the PtraceEngine seam; the returned ``script``
    preloads events/regs (applied when collect_syscalls constructs the
    engine) and yields (events, regs, mem, created)."""
    created = []
    scripted = {}

    def factory(pid, **kwargs):
        eng = StubEngine(pid, **kwargs)
        eng.events = list(scripted.get("events", []))
        eng.regs = {k: list(v) for k, v in scripted.get("regs", {}).items()}
        created.append(eng)
        return eng

    monkeypatch.setattr(sc_mod, "PtraceEngine", factory)
    # 参数解码的内存视图换 FakeMemory（LiveView 语义见 SY4 守护）
    mem = FakeMemory()
    monkeypatch.setattr(sc_mod, "LiveView", lambda transport: mem)

    def script(events, regs):
        scripted["events"] = events
        scripted["regs"] = regs
        return events, regs, mem, created

    return script


class TestCollectEngineFlow:
    def test_engine_sequence_and_detach(self, engine_factory):
        """SY1：seize(OPTIONS)→stop_all→restart_all→wait_event→detach
        的引擎调用序列（A2 消费契约）；feature 名透传（T16 错误文案）。"""
        events, regs, mem, created = engine_factory([], {})
        out = collect_syscalls(PID)
        eng = created[0]
        assert eng.feature == "syscall tracing"
        assert eng.calls[:3] == [("seize", PTRACE_OPTIONS), ("stop_all",),
                                 ("restart_all",)]
        assert eng.calls[-1] == ("detach",)
        assert out == []

    def test_detach_on_error(self, engine_factory, monkeypatch):
        """SY1 失败模式：循环内异常仍 detach（finally 语义——tracee 不得
        滞留停止态）。"""
        events, regs, mem, created = engine_factory([RuntimeError("boom")],
                                                    {})
        with pytest.raises(RuntimeError):
            collect_syscalls(PID)
        assert created[0].calls[-1] == ("detach",)

    def test_attach_failed_propagates(self, monkeypatch):
        """SY1 失败模式：seize 失败 → AttachFailed 原样传播（CLI `[!]`）。"""
        def factory(pid, **kw):
            raise AttachFailed(pid, "Operation not permitted")

        monkeypatch.setattr(sc_mod, "PtraceEngine", factory)
        with pytest.raises(AttachFailed):
            collect_syscalls(PID)


class TestEntryExitPairing:
    def test_pairing_emits_event(self, engine_factory):
        """SY2/SY3：entry 存 (nr, 6 寄存器 args 全显, t0)，exit 配对发射——
        nr/name/args/ret/tid/elapsed 全字段。"""
        entry = make_regs(257, rax=-1 % (1 << 64),
                          args=(0x9C, 0x1000, 0, 0, 0, 0))
        exit_ = make_regs(257, rax=3)
        events = [SyscallStop(100), SyscallStop(100), Exited(100, 0, False)]
        *_, created = engine_factory(events, {100: [entry, exit_]})
        out = collect_syscalls(PID)
        assert len(out) == 1
        ev = out[0]
        assert (ev.nr, ev.name, ev.ret, ev.error, ev.tid) == (
            257, "openat", 3, None, 100)
        assert ev.args == [0x9C, 0x1000, 0, 0, 0, 0]  # 6 寄存器全显复刻
        assert ev.elapsed >= 0.0

    def test_errno_decode(self, engine_factory):
        """SY3：rax 为 -errno 的无符号形态 → error=errno、ret=-1。"""
        entry = make_regs(0, args=(0, 0x2000, 8, 0, 0, 0))
        exit_ = make_regs(0, rax=(1 << 64) - 2)  # -ENOENT
        events = [SyscallStop(100), SyscallStop(100), Exited(100, 0, False)]
        *_, created = engine_factory(events, {100: [entry, exit_]})
        (ev,) = collect_syscalls(PID)
        assert ev.error == 2
        assert ev.ret == -1

    def test_unknown_syscall_name(self, engine_factory):
        """SY3：表外 nr → "sys_<nr>"（新内核 syscall 的前向兼容）。"""
        entry = make_regs(9999)
        exit_ = make_regs(9999, rax=0)
        events = [SyscallStop(100), SyscallStop(100), Exited(100, 0, False)]
        *_, created = engine_factory(events, {100: [entry, exit_]})
        (ev,) = collect_syscalls(PID)
        assert ev.name == "sys_9999"

    def test_multithread_events(self, engine_factory):
        """SY2：两线程各自配对（stash 按 tid 隔离，交错 stop 不串）。"""
        e1, x1 = make_regs(1, args=(1, 0x10, 5, 0, 0, 0)), make_regs(1, rax=5)
        e2, x2 = make_regs(1, args=(1, 0x20, 5, 0, 0, 0)), make_regs(1, rax=5)
        events = [SyscallStop(100), SyscallStop(101), SyscallStop(100),
                  SyscallStop(101), Exited(100, 0, False),
                  Exited(101, 0, False)]
        *_, created = engine_factory(
            events, {100: [e1, x1], 101: [e2, x2]})
        out = collect_syscalls(PID)
        assert {ev.tid for ev in out} == {100, 101}
        assert all(ev.name == "write" for ev in out)

    def test_main_thread_exit_ends_loop(self, engine_factory):
        """SY6：主线程 Exited 即结束（残余线程事件不再等待）。"""
        events = [Exited(PID, 0, False), SyscallStop(101)]
        *_, created = engine_factory(events, {})
        assert collect_syscalls(PID) == []

    def test_none_from_engine_ends_loop(self, engine_factory):
        """SY6：wait_event None（ECHILD，全部 tracee 消失）→ 结束。"""
        *_, created = engine_factory([], {})
        assert collect_syscalls(PID) == []

    def test_max_events_stops(self, engine_factory):
        """SY7：到量即停（多余 stop 不消费）。"""
        pairs = []
        regs = []
        for _ in range(5):
            pairs += [SyscallStop(100), SyscallStop(100)]
            regs += [make_regs(0, args=(0, 0x10, 1, 0, 0, 0)),
                     make_regs(0, rax=1)]
        *_, created = engine_factory(pairs, {100: regs})
        out = collect_syscalls(PID, max_events=3)
        assert len(out) == 3


class TestExecEvent:
    def _script(self):
        entry = make_regs(59, args=(0x1000, 0x2000, 0x3000, 0, 0, 0))
        events = [SyscallStop(100), ExecEvent(100), Exited(100, 0, False)]
        return events, {100: [entry]}

    def test_execve_exit_emitted_on_exec_event(self, engine_factory):
        """SY5：execve 的 exit 事件由 PTRACE_EVENT_EXEC 补发（ret=0——
        成功 execve 无返回）。"""
        events, regs = self._script()
        *_, created = engine_factory(events, regs)
        (ev,) = collect_syscalls(PID)
        assert ev.name == "execve"
        assert ev.ret == 0

    def test_execve_respects_user_filter(self, engine_factory):
        """SY5（A7 P1-5 修复）：-e trace=!execve 时补发事件被过滤——旧树
        用 TraceFilter("") 全匹配绕过用户过滤器（实测复现的语义反转）。"""
        events, regs = self._script()
        *_, created = engine_factory(events, regs)
        assert collect_syscalls(PID, trace="!execve") == []

    def test_execve_included_by_matching_filter(self, engine_factory):
        """SY5 配套：trace=process 组含 execve → 补发事件保留。"""
        events, regs = self._script()
        *_, created = engine_factory(events, regs)
        (ev,) = collect_syscalls(PID, trace="process")
        assert ev.name == "execve"


class TestLiveViewUsage:
    def test_arg_reads_use_live_view(self, engine_factory, monkeypatch):
        """SY4（A4）：参数解码经 LiveView（长时追踪不缓存——快照缓存会
        在多次 stop 间供陈旧数据，旧 _UncachedReader 语义）。"""
        seen = []

        class SpyLive:
            def __init__(self, transport):
                seen.append(transport)

        monkeypatch.setattr(sc_mod, "LiveView", SpyLive)
        *_, created = engine_factory([], {})
        collect_syscalls(PID)
        assert len(seen) == 1  # 每次追踪一个 LiveView


class TestOnEvent:
    def test_on_event_called_per_emission(self, engine_factory):
        """SY14：on_event 在每次发射即回调（dump 层流式输出的通道；
        且回调先于 return——KeyboardInterrupt 时已发事件不丢）。"""
        entry = make_regs(0, args=(0, 0x10, 1, 0, 0, 0))
        exit_ = make_regs(0, rax=1)
        events = [SyscallStop(100), SyscallStop(100), Exited(100, 0, False)]
        *_, created = engine_factory(events, {100: [entry, exit_]})
        seen = []
        out = collect_syscalls(PID, on_event=seen.append)
        assert seen == out  # 回调序列与返回列表一致

    def test_on_event_none_by_default(self, engine_factory):
        """SY14：默认 None 不改变批 5 语义（无回调、行为不变）。"""
        *_, created = engine_factory([], {})
        assert collect_syscalls(PID) == []


# ---------------------------------------------------------------------------
# TraceFilter (SY8, A7 P0-3)
# ---------------------------------------------------------------------------

class TestTraceFilter:
    def test_empty_traces_everything(self):
        """SY8：空 spec → 全追踪（默认行为）。"""
        assert TraceFilter("").matches("anything") is True

    def test_explicit_names(self):
        """SY8：显式名单仅匹配名单内。"""
        f = TraceFilter("read,write")
        assert f.matches("read") is True
        assert f.matches("openat") is False

    def test_group(self):
        """SY8：组名展开（file 组含 open/openat/close）。"""
        f = TraceFilter("file")
        assert f.matches("openat") is True
        assert f.matches("read") is False

    def test_mixed_groups_and_names(self):
        """SY8：组与单名并集。"""
        f = TraceFilter("network,read")
        assert f.matches("connect") is True
        assert f.matches("read") is True
        assert f.matches("openat") is False

    def test_pure_exclusion(self):
        """SY8：纯排除 → 除名单外全追踪。"""
        f = TraceFilter("!futex")
        assert f.matches("futex") is False
        assert f.matches("read") is True

    def test_pure_exclusion_group(self):
        """SY8：组也可排除（!file → 非文件全放行）。"""
        f = TraceFilter("!file")
        assert f.matches("openat") is False
        assert f.matches("read") is True

    def test_mixed_include_exclude(self):
        """SY8（A7 P0-3 修复）：混合表达式 = include − exclude——旧树把
        ! 项并入包含集致 openat 反被追踪（实测复现的语义反转）。"""
        f = TraceFilter("file,!openat")
        assert f.matches("openat") is False
        assert f.matches("open") is True
        f2 = TraceFilter("read,!write")
        assert f2.matches("read") is True
        assert f2.matches("write") is False

    def test_unknown_name_matches_nothing(self):
        """SY8：未知名静默不匹配（不报错，防拼写错误炸掉追踪）。"""
        assert TraceFilter("nosuchcall").matches("read") is False

    def test_whitespace_tolerant(self):
        """SY8：项间空白容忍。"""
        f = TraceFilter(" read , write ")
        assert f.matches("write") is True


# ---------------------------------------------------------------------------
# Rendering helpers (SY9)
# ---------------------------------------------------------------------------

class TestEscapeBytes:
    def test_plain_ascii(self):
        """SY9：可打印 ASCII 原样。"""
        assert escape_bytes(b"hello") == "hello"

    def test_quotes_and_backslash(self):
        """SY9：引号与反斜杠转义（strace 风格）。"""
        assert escape_bytes(b'a"b\\c') == 'a\\"b\\\\c'

    def test_nonprintable_octal(self):
        """SY9：不可打印字节 \\NNN 八进制（换行/ NUL/ 高位字节）。"""
        assert escape_bytes(b"a\nb\x00\xc3\xa9") == "a\\012b\\000\\303\\251"

    def test_empty(self):
        """SY9 边界：空字节串。"""
        assert escape_bytes(b"") == ""


class TestTruncate:
    def test_short_untouched(self):
        """SY9：短串不动。"""
        assert truncate_escaped("abc") == "abc"

    def test_exact_limit_untouched(self):
        """SY9 边界：恰好 32 字符不动。"""
        assert truncate_escaped("a" * 32) == "a" * 32

    def test_over_limit_ellipsis(self):
        """SY9（复刻约束 32 字符截断）：超长截 32 + "..."。"""
        assert truncate_escaped("a" * 40) == "a" * 32 + "..."

    def test_render_str_arg(self):
        """SY9：render_str_arg 加引号；非 verbose 截断、verbose 保留。"""
        assert render_str_arg(b"hello") == '"hello"'
        assert render_str_arg(b"a" * 40) == '"' + "a" * 32 + '..."'
        assert render_str_arg(b"a" * 40, verbose=True) == '"' + "a" * 40 + '"'


class TestReadCstr:
    def test_reads_nul_terminated(self):
        """SY9：NUL 结尾字符串读至 NUL。"""
        mem = FakeMemory({0x1000: b"/tmp/x\x00rest"})
        assert read_cstr(mem, 0x1000) == '"/tmp/x"'

    def test_truncates_long(self):
        """SY9：超长走 32 字符截断。"""
        mem = FakeMemory({0x1000: b"a" * 100 + b"\x00"})
        assert read_cstr(mem, 0x1000) == '"' + "a" * 32 + '..."'

    def test_null_addr(self):
        """SY9：addr==0 → "NULL"。"""
        assert read_cstr(FakeMemory(), 0) == "NULL"

    def test_unreadable_addr(self):
        """SY9：全不可读 → 原始 0x 地址（strace 对不可读内存的行为）。"""
        assert read_cstr(FakeMemory(), 0xDEAD) == "0xdead"

    def test_partial_mapping_probed(self):
        """SY9：首读因映射边缘整体失败 → read_available 二分探测出实际
        可读部分（短映射末尾的字符串不丢数据）。"""
        mem = FakeMemory({0x1000: b"/" + b"x" * 99})  # 100B 区域 < chunk
        # read(0x1000, 256) 失败（区域不含全段）→ 二分找到 100B → 32 截断
        assert read_cstr(mem, 0x1000) == '"/' + "x" * 31 + '..."'

    def test_continuation_failure_keeps_read_prefix(self):
        """SY9：首块整读成功但无 NUL、续读不可读 → 已读前缀返回并截断
        （映射尾恰好落在 chunk 边界的情形）。"""
        mem = FakeMemory({0x1000: b"/" + b"x" * 255})  # 恰 256B 无 NUL
        assert read_cstr(mem, 0x1000) == '"/' + "x" * 31 + '..."'


class TestReadAvailable:
    def test_full_read(self):
        """SY9：整段可满足 → 全量返回。"""
        mem = FakeMemory({0x1000: b"abcdefgh"})
        assert read_available(mem, 0x1000, 4) == b"abcd"

    def test_binary_search_largest(self):
        """SY9：边缘映射——二分最大可满足长度（映射尾部读取不丢数据）。"""
        mem = FakeMemory({0x1000: b"abc"})
        assert read_available(mem, 0x1000, 8) == b"abc"


class TestReadTimespec:
    def test_reads_struct(self):
        """SY9：{tv_sec, tv_nsec} 双 i64 解码。"""
        mem = FakeMemory({0x1000: struct.pack("<qq", 5, 123456789)})
        assert read_timespec(mem, 0x1000) == "{5, 123456789}"

    def test_null(self):
        """SY9：NULL → "NULL"。"""
        assert read_timespec(FakeMemory(), 0) == "NULL"

    def test_unreadable(self):
        """SY9：不可读 → 0x 地址。"""
        assert read_timespec(FakeMemory(), 0xDEAD) == "0xdead"


class TestDecodeFlags:
    def test_zero_accmode(self):
        """SY9：0 值命中表中 0 项（O_RDONLY 特例）。"""
        from pyprobe.observe.syscall_abi import OPEN_FLAGS

        assert decode_flags(0, OPEN_FLAGS) == "O_RDONLY"

    def test_or_combination(self):
        """SY9：位或组合按名拼接。"""
        from pyprobe.observe.syscall_abi import OPEN_FLAGS

        assert decode_flags(0x42, OPEN_FLAGS) == "O_RDWR|O_CREAT"

    def test_unknown_bits_hex(self):
        """SY9：残余未知位以 hex 追加。"""
        from pyprobe.observe.syscall_abi import OPEN_FLAGS

        assert decode_flags(0x42 | 0x800000, OPEN_FLAGS) == \
            "O_RDWR|O_CREAT|0x800000"


class TestRenderScalar:
    def test_at_fdcwd(self):
        """SY9：dirfd 的 AT_FDCWD 魔数识别（-100 的无符号形态）。"""
        assert render_scalar("dirfd", 0xFFFFFFFFFFFFFF9C) == "AT_FDCWD"

    def test_mode_octal(self):
        """SY9：mode 八进制渲染。"""
        assert render_scalar("mode", 0o644) == "0o644"

    def test_signal(self):
        """SY9：信号号 SIG<n>。"""
        assert render_scalar("signal", 9) == "SIG9"

    def test_int_range(self):
        """SY9：32 位有符号范围内十进制，范围外 hex。"""
        assert render_scalar("count", 42) == "42"
        assert render_scalar("count", 0xFFFFFFFFFFFF0000) == \
            "0xffffffffffff0000"


class TestDecodeArgs:
    def test_openat_full(self):
        """SY9：openat(dirfd, path, flags, mode) 全要素解码。"""
        mem = FakeMemory({0x1000: b"/tmp/x\x00"})
        out = decode_args(mem, "openat",
                          [0xFFFFFFFFFFFFFF9C, 0x1000, 0x42, 0o644, 0, 0])
        assert out == 'AT_FDCWD, "/tmp/x", O_RDWR|O_CREAT, 0o644, 0x0, 0x0'

    def test_read_buf_as_addr_at_entry(self):
        """SY9：buf_in 在 entry 阶段渲染为地址（内容 exit 才填充）。"""
        out = decode_args(FakeMemory(), "read", [3, 0x7F00, 64, 0, 0, 0])
        assert out == "3, 0x7f00, 64, 0x0, 0x0, 0x0"

    def test_mmap_flags(self):
        """SY9：mmap prot/map_flags 双表解码。"""
        out = decode_args(FakeMemory(), "mmap",
                          [0, 4096, 3, 0x22, 0xFFFFFFFF, 0])
        assert out == ("0x0, 4096, PROT_READ|PROT_WRITE, "
                       "MAP_PRIVATE|MAP_ANONYMOUS, -1, 0")

    def test_clock_nanosleep_timespec(self):
        """SY9：timespec 参数 entry 即读（输入参数非输出）。"""
        mem = FakeMemory({0x1000: struct.pack("<qq", 0, 200000000)})
        out = decode_args(mem, "clock_nanosleep",
                          [1, 0, 0x1000, 0, 0, 0])
        assert out == "1, 0, {0, 200000000}, NULL, 0x0, 0x0"

    def test_unknown_syscall_raw_hex(self):
        """SY9：无 DECODE 元数据的 syscall 六参数全 hex（复刻约束）。"""
        out = decode_args(FakeMemory(), "getrandom", [0x1000, 8, 0, 0, 0, 0])
        assert out == "0x1000, 0x8, 0x0, 0x0, 0x0, 0x0"

    def test_no_args_syscall(self):
        """SY9 边界：零参数 syscall（getpid DECODE 为空表）。"""
        assert decode_args(FakeMemory(), "getpid", []) == ""

    def test_null_path(self):
        """SY9 边界：path 参数为 0 → NULL。"""
        out = decode_args(FakeMemory(), "unlink", [0])
        assert out == "NULL"


class TestFillOutArgs:
    def test_read_content_spliced(self):
        """SY10：read 成功 → 缓冲区内容按 min(ret,32) 拼入 0xaddr/"..."。"""
        mem = FakeMemory({0x2000: b"hello world"})
        rendered = decode_args(FakeMemory(), "read", [3, 0x2000, 64, 0, 0, 0])
        out = fill_out_args(mem, "read", [3, 0x2000, 64, 0, 0, 0], 5,
                            rendered)
        assert out == '3, 0x2000/"hello", 64, 0x0, 0x0, 0x0'

    def test_write_content_spliced(self):
        """SY10：write 的 buf_in 同样按 ret 截取（写的即内核接受的）。"""
        mem = FakeMemory({0x2000: b"payload!"})
        rendered = decode_args(FakeMemory(), "write", [1, 0x2000, 8, 0, 0, 0])
        out = fill_out_args(mem, "write", [1, 0x2000, 8, 0, 0, 0], 8,
                            rendered)
        assert '0x2000/"payload!"' in out

    def test_eof_renders_empty_string(self):
        """SY10（A7 P1-7 修复）：ret==0（EOF）→ buf_in 渲染空串——旧树
        按 32 字节读出内核未写的陈旧缓冲区内容展示（实测误导）。"""
        mem = FakeMemory({0x2000: b"stale-bytes-not-written"})
        rendered = decode_args(FakeMemory(), "read", [3, 0x2000, 64, 0, 0, 0])
        out = fill_out_args(mem, "read", [3, 0x2000, 64, 0, 0, 0], 0,
                            rendered)
        assert out == '3, 0x2000/"", 64, 0x0, 0x0, 0x0'

    def test_buf_out_success_content(self):
        """SY10：buf_out 的 ret 是状态码（stat 成功=0）→ 仍读内容
        （与 buf_in 的 EOF 语义区分——P1-7 修复不得误伤 stat 类）。"""
        mem = FakeMemory({0x2000: b"\x01" * 16})
        rendered = decode_args(FakeMemory(), "stat", [0x1000, 0x2000])
        out = fill_out_args(mem, "stat", [0x1000, 0x2000], 0, rendered)
        assert '0x2000/"' in out  # 内容拼接发生

    def test_unreadable_buf_unchanged(self):
        """SY10 失败模式：缓冲区不可读 → 保持 entry 的地址渲染。"""
        rendered = decode_args(FakeMemory(), "read", [3, 0x2000, 64, 0, 0, 0])
        out = fill_out_args(FakeMemory(), "read", [3, 0x2000, 64, 0, 0, 0],
                            5, rendered)
        assert out == "3, 0x2000, 64, 0x0, 0x0, 0x0"

    def test_truncated_content(self):
        """SY10：内容超 32 字符截断（复刻约束）。"""
        mem = FakeMemory({0x2000: b"x" * 64})
        rendered = decode_args(FakeMemory(), "read", [3, 0x2000, 64, 0, 0, 0])
        out = fill_out_args(mem, "read", [3, 0x2000, 64, 0, 0, 0], 64,
                            rendered)
        assert out == '3, 0x2000/"' + "x" * 32 + '...", 64, 0x0, 0x0, 0x0'

    def test_verbose_content(self):
        """SY10：-v 不截断。"""
        mem = FakeMemory({0x2000: b"x" * 64})
        rendered = decode_args(FakeMemory(), "read", [3, 0x2000, 64, 0, 0, 0])
        out = fill_out_args(mem, "read", [3, 0x2000, 64, 0, 0, 0], 64,
                            rendered, verbose=True)
        assert '0x2000/"' + "x" * 64 + '"' in out

    def test_timespec_out_reread_at_exit(self):
        """SY10（A7 P1-6 修复）：clock_gettime 的 timespec_out 于 exit
        重读——旧树在 entry 读到调用前陈旧内容（strace 在 exit 读）。
        模拟内核在调用期间写入新值。"""
        mem = FakeMemory({0x2000: struct.pack("<qq", 1, 0)})  # entry 旧值
        rendered = decode_args(mem, "clock_gettime", [0, 0x2000])
        assert "{1, 0}" in rendered
        mem.add(0x2000, struct.pack("<qq", 2, 500))  # exit 时内核已更新
        out = fill_out_args(mem, "clock_gettime", [0, 0x2000], 0, rendered)
        assert "{2, 500}" in out
        assert "{1, 0}" not in out
