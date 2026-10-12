"""Contract tests for web/monitor.py (WM1–WM8, 批 8a).

后台采样线程经模块级 ``time`` 假时钟接缝（同 test_profile.py 模式）+
门控 StubSampler 驱动：脚本轮次按假时钟确定性耗尽，随后 sample() 停摆
在放行事件上——测试线程据此拿到冻结的队列状态，避免热自旋竞态。
"""

import queue
import threading

import pytest

import pyprobe.web.monitor as mon_mod
from pyprobe import dto
from pyprobe.errors import ProcessExited, ProcessNotFound

PID = 4242
PROC = {"pid": PID, "cmdline": "python3 x", "exe_path": "/x/python",
        "python_version": "?", "version_warning": None}


def _frame(name, filename="app/x.py", line=1):
    return dto.FrameInfo(name=name, filename=filename, line=line)


def _thread(native_tid=101, name="", frames=(), idle=False, thread_id=0):
    return dto.ThreadInfo(native_tid=native_tid, thread_id=thread_id,
                          name=name, idle=idle, frames=list(frames))


class FakeClock:
    """monotonic/sleep 假时钟：sleep 推进时间（WM2 调度的确定性守护）。"""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, d):
        self.t += d


class StubSampler:
    """脚本化采样器：按序耗尽 rounds（记录各次调用的假时钟时刻，异常
    元素原样上抛），耗尽后置 done 并停摆在 release 上（放行后返回空
    样本）——采样调度的观测点与测试停机位。"""

    def __init__(self, pid, rounds, clock, release, done, advance=0.0,
                 warning=None):
        self.pid = pid
        self.rounds = list(rounds)
        self.clock = clock
        self.release = release
        self.done = done
        self.advance = advance
        self.proc_info = dto.ProcessInfo(pid=pid, cmdline="python3 x",
                                         exe_path="/x/python")
        self.session = type("S", (), {"version_warning": warning})()
        self.times = []
        self.refresh_calls = 0

    def sample(self):
        if not self.rounds:
            self.done.set()
            self.release.wait()
            return []
        r = self.rounds.pop(0)
        self.times.append(self.clock.monotonic())
        if self.advance:
            self.clock.t += self.advance
        if isinstance(r, BaseException):
            raise r
        return r

    def refresh_names(self):
        self.refresh_calls += 1


def _harness(monkeypatch, rounds, *, rate=10, interval=1.0, advance=0.0,
             warning=None):
    """装配假时钟 + 门控 StubSampler + Monitor（不 start，由测试驱动）。"""
    clock = FakeClock()
    release = threading.Event()
    done = threading.Event()
    stub = StubSampler(PID, rounds, clock, release, done, advance, warning)
    monkeypatch.setattr(mon_mod, "time", clock)

    def factory(pid):
        return stub

    m = mon_mod.Monitor(PID, rate=rate, interval=interval,
                        sampler_factory=factory)
    return m, stub, release


def _get(q, timeout=2):
    """取一帧；超时转 pytest.fail——让 Red 失败落在预期断言上。"""
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        pytest.fail("订阅队列 2s 内无帧到达（WM2/WM3：采样线程未广播）")


def _drain(q):
    """非阻塞清空队列，返回帧列表（停摆/停机后的确定性快照）。"""
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def _shutdown(m, release):
    """放行门控并停止（停摆中的线程被唤醒后见停止标志即退出）。"""
    release.set()
    m.stop()


class TestConstruction:
    def test_pyprobe_error_raises_at_construction(self, monkeypatch):
        """WM1：构造即解析目标——sampler_factory 抛 PyProbeError 子类时
        异常同步上抛（不进后台线程、不需 start 才暴露）。"""
        release = threading.Event()
        done = threading.Event()
        stub = StubSampler(PID, [], FakeClock(), release, done)
        monkeypatch.setattr(mon_mod, "time", FakeClock())

        def factory(pid):
            raise ProcessNotFound(pid)

        with pytest.raises(ProcessNotFound):
            mon_mod.Monitor(PID, sampler_factory=factory)
        assert stub.times == []  # WM1：未消耗任何样本


class TestSamplingThread:
    def test_absolute_schedule_no_drift(self, monkeypatch):
        """WM2：绝对时间调度——rate=10 下样本恰落 0.1s 一格
        [0, .1, .2, .3, .4]，不漂移不少采。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])]] * 5)
        m.start()
        try:
            assert stub.done.wait(2), "脚本轮次未耗尽（采样线程未运行）"
            assert stub.times == pytest.approx([0, 0.1, 0.2, 0.3, 0.4])
        finally:
            _shutdown(m, release)

    def test_behind_schedule_resets_baseline(self, monkeypatch):
        """WM2（PF2 语义承接）：单轮耗时超过采样间隔 → 重置基线而非
        补发爆发——落后不追，采样率不失真。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])]] * 3, advance=0.25)
        m.start()
        try:
            assert stub.done.wait(2)
            assert stub.times == pytest.approx([0, 0.25, 0.5])
        finally:
            _shutdown(m, release)


class TestBroadcast:
    def test_every_interval_to_all_subscribers(self, monkeypatch):
        """WM3：每 interval 一帧投递至全部订阅队列；帧内容逐队列一致；
        首帧 t=0 即广播（elapsed 0，不等满一个 interval）。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])]] * 6,
            rate=100, interval=0.017)
        q1 = m.subscribe()
        q2 = m.subscribe()
        m.start()
        try:
            assert stub.done.wait(2)
            f1 = _drain(q1)
            f2 = _drain(q2)
            # 边界 0.017 落在样本时刻 .01/.02 之间（margin 0.003）：
            # 广播严格发生在首个越过边界的样本上 → 恰 3 帧。
            assert [f["data"]["elapsed"] for f in f1] == pytest.approx(
                [0.0, 0.02, 0.04])
            assert f1 == f2
        finally:
            _shutdown(m, release)

    def test_frame_threads_are_latest_sample(self, monkeypatch):
        """WM2/WM3：留存最新快照——帧内 threads 是最近一次采样的完整
        ThreadInfo 序列化（旧样本不残留）；samples 随每样本 TopStats.update
        累计。"""
        a = _thread(native_tid=1, name="a", frames=[_frame("fa")])
        b = _thread(native_tid=2, name="b", thread_id=9,
                    frames=[_frame("fb", "app/b.py", 7)])
        m, stub, release = _harness(
            monkeypatch, [[a], [a], [a], [b]], rate=10, interval=0.25)
        q = m.subscribe()
        m.start()
        try:
            assert stub.done.wait(2)
            frames = _drain(q)
            # 广播在 t=0（threads=[a]）与 t=.3（threads=[b]，.3 ≥ .25）。
            assert len(frames) == 2
            last = frames[-1]["data"]
            assert last["samples"] == 4
            assert last["threads"] == [{
                "native_tid": 2, "thread_id": 9, "name": "b", "idle": False,
                "own_pct": pytest.approx(25.0),
                "frames": [{"name": "fb", "filename": "app/b.py", "line": 7}],
            }]
        finally:
            _shutdown(m, release)


class TestStatsFrame:
    def test_stats_frame_key_set_exact(self, monkeypatch):
        """WM4：stats 帧键集 pin——process 五键（version_warning 透出）、
        elapsed/samples/idle_samples、top_functions 六键条目（仅 own 字典
        成员入榜，render_top 同口径；pct 占 samples 百分比、time=own/rate）、
        threads 七键条目（完整帧链不丢弃，own_pct 为顶帧函数 own 占比）。"""
        t1 = _thread(native_tid=101, thread_id=7, name="worker",
                     frames=[_frame("f1", "app/x.py", 1),
                             _frame("f2", "app/y.py", 2)])
        t2 = _thread(native_tid=103, name="helper",
                     frames=[_frame("f2", "app/y.py", 2),
                             _frame("f3", "app/z.py", 3)])
        idle = _thread(native_tid=102, idle=True)
        m, stub, release = _harness(
            monkeypatch, [[t1, t2, idle]], rate=50, interval=1.0)
        q = m.subscribe()
        m.start()
        try:
            frame = _get(q)
            assert frame == {
                "type": "stats",
                "data": {
                    "process": dict(PROC),
                    "elapsed": pytest.approx(0.0),
                    "samples": 2,
                    "idle_samples": 1,
                    # 仅出现在栈中、从未当过叶子帧的 f3 不入榜（own 字典
                    # 成员资格）；f2 的 total（2）> own（1）区分两口径。
                    "top_functions": [
                        {"name": "f1", "own": 1, "total": 1,
                         "own_pct": 50.0, "total_pct": 50.0, "time": 0.02},
                        {"name": "f2", "own": 1, "total": 2,
                         "own_pct": 50.0, "total_pct": 100.0, "time": 0.02},
                    ],
                    "threads": [
                        {"native_tid": 101, "thread_id": 7, "name": "worker",
                         "idle": False, "own_pct": 50.0,
                         "frames": [{"name": "f1", "filename": "app/x.py",
                                     "line": 1},
                                    {"name": "f2", "filename": "app/y.py",
                                     "line": 2}]},
                        {"native_tid": 103, "thread_id": 0, "name": "helper",
                         "idle": False, "own_pct": 50.0,
                         "frames": [{"name": "f2", "filename": "app/y.py",
                                     "line": 2},
                                    {"name": "f3", "filename": "app/z.py",
                                     "line": 3}]},
                        {"native_tid": 102, "thread_id": 0, "name": "",
                         "idle": True, "own_pct": 0.0, "frames": []},
                    ],
                },
            }
        finally:
            _shutdown(m, release)

    def test_boundary_zero_samples(self, monkeypatch):
        """WM4 边界：全 idle 首样本 → samples==0，pct/time 为 0、
        top_functions 空、idle 线程 frames 为 []。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(native_tid=103, idle=True)]],
            rate=50, interval=1.0)
        q = m.subscribe()
        m.start()
        try:
            data = _get(q)["data"]
            assert data["samples"] == 0
            assert data["idle_samples"] == 1
            assert data["top_functions"] == []
            assert data["threads"] == [{
                "native_tid": 103, "thread_id": 0, "name": "", "idle": True,
                "own_pct": 0.0, "frames": []}]
        finally:
            _shutdown(m, release)

    def test_boundary_active_thread_without_frames(self, monkeypatch):
        """WM4 边界：活跃线程无顶帧（in-C 状态，TS4）→ own_pct 为 0、
        frames 为 []、idle 仍为 False（与 idle 线程可区分）。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(native_tid=104, frames=[])]],
            rate=50, interval=1.0)
        q = m.subscribe()
        m.start()
        try:
            data = _get(q)["data"]
            assert data["samples"] == 1
            assert data["top_functions"] == []
            assert data["threads"][0]["idle"] is False
            assert data["threads"][0]["own_pct"] == 0.0
            assert data["threads"][0]["frames"] == []
        finally:
            _shutdown(m, release)

    def test_top_functions_ranked_and_capped(self, monkeypatch):
        """WM4：top_functions 按 (-own, name) 排序、截 15 条（对齐
        render_top 的 top_n 惯例）——own 计数与 time=own/rate 口径。"""
        hot = _thread(native_tid=1, frames=[_frame("hot")])
        others = [_thread(native_tid=10 + i, frames=[_frame(f"f{i:02d}")])
                  for i in range(1, 20)]
        rounds = [[*others, hot]] + [[hot]] * 4
        m, stub, release = _harness(
            monkeypatch, rounds, rate=10, interval=0.045)
        q = m.subscribe()
        m.start()
        try:
            assert stub.done.wait(2)
            last = _drain(q)[-1]["data"]  # 末帧聚合全部 5 轮
            assert last["samples"] == 24  # 20 + 4 个活跃观测
            names = [t["name"] for t in last["top_functions"]]
            assert names == ["hot"] + [f"f{i:02d}" for i in range(1, 15)]
            assert last["top_functions"][0] == {
                "name": "hot", "own": 5, "total": 5,
                "own_pct": pytest.approx(5 / 24 * 100),
                "total_pct": pytest.approx(5 / 24 * 100),
                "time": pytest.approx(0.5)}
        finally:
            _shutdown(m, release)


class TestFanout:
    def test_subscribe_returns_bounded_queue(self, monkeypatch):
        """WM5：subscribe() 返回有界队列（maxsize=64）——慢消费者不拖垮
        采样线程的前提。"""
        m, _, release = _harness(monkeypatch, [])
        q = m.subscribe()
        assert q.maxsize == 64
        _shutdown(m, release)

    def test_full_queue_drops_oldest_keeps_latest(self, monkeypatch):
        """WM5：队列满丢最旧保最新（监控取最新语义）——100 次广播后
        仅留末 64 帧；且队列积压不阻塞采样（100 轮全部完成）。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])]] * 100,
            rate=1000, interval=0.001)
        q = m.subscribe()
        m.start()
        try:
            assert stub.done.wait(2)
            assert len(stub.times) == 100  # 广播积压从未阻塞采样
            frames = _drain(q)
            assert len(frames) == 64
            elapseds = [f["data"]["elapsed"] for f in frames]
            assert elapseds[0] > 0  # 最初若干帧已被丢弃
            assert elapseds == sorted(elapseds)  # 保序
            assert elapseds[-1] == pytest.approx(0.099, abs=2e-3)
        finally:
            _shutdown(m, release)

    def test_unsubscribe_idempotent_and_stops_delivery(self, monkeypatch):
        """WM5：unsubscribe 幂等（重复调用无异常）；退订后不再收到帧，
        在订队列照常收帧。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])]] * 3)
        q1 = m.subscribe()
        q2 = m.subscribe()
        m.start()
        try:
            assert stub.done.wait(2)
            m.unsubscribe(q1)
            m.unsubscribe(q1)  # 幂等：重复退订不抛
            _drain(q1)  # 记录此刻存量（退订前到达的帧可保留）
            release.set()
            _get(q2)  # 放行后空样本仍广播 → 在订队列有新帧
            _drain(q1)
            with pytest.raises(queue.Empty):
                q1.get(timeout=0.3)  # 退订队列不再收到任何帧
        finally:
            m.stop()

    def test_subscribe_midrun_replays_last_frame(self, monkeypatch):
        """WM5/WA3 支点：订阅时已有广播帧 → 立即收到最近一帧（SSE 首帧
        即时性的 monitor 侧支点）；未广播过则不投递。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])]], rate=10)
        m.start()
        try:
            assert stub.done.wait(2)  # 首帧（t=0）已广播
            q2 = m.subscribe()
            assert q2.qsize() == 1
            assert q2.get_nowait()["type"] == "stats"
        finally:
            _shutdown(m, release)

    def test_subscribe_before_any_frame_empty(self, monkeypatch):
        """WM5 边界：尚无任何广播帧时订阅 → 队列为空（不投递哨兵帧）。"""
        m, _, release = _harness(monkeypatch, [])
        q = m.subscribe()
        assert q.qsize() == 0
        _shutdown(m, release)


class TestExit:
    def test_process_exit_broadcast_and_sticky(self, monkeypatch):
        """WM6：ProcessExited → 广播 exit 帧（data 空）→ 采样线程终止
        （其后无新帧）；exit 粘性——新订阅立即收到恰好一帧 exit；
        .exited 为 True。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])],
                          ProcessExited(PID)])
        q = m.subscribe()
        m.start()
        try:
            assert _get(q)["type"] == "stats"
            assert _get(q) == {"type": "exit", "data": {}}
            assert m.exited is True
            q2 = m.subscribe()  # 粘性：新订阅立即收 exit
            assert q2.get_nowait() == {"type": "exit", "data": {}}
            assert q2.qsize() == 0  # 恰好一帧，不重复
            _drain(q)
            with pytest.raises(queue.Empty):
                q.get(timeout=0.3)  # 采样线程已终止
        finally:
            m.stop()  # 线程已死：join 立即返回


class TestStop:
    def test_stop_idempotent_and_joins(self, monkeypatch):
        """WM7：stop() 幂等——未 start 先 stop 无异常；重复调用无副作用；
        stop 后线程确已终止（队列冻结）。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])]])
        m.stop()  # 未 start：无异常
        q = m.subscribe()
        m.start()
        try:
            _get(q)  # 首帧到达：线程确在运行
            release.set()
            m.stop()
            m.stop()  # 重复调用无副作用
            _drain(q)
            with pytest.raises(queue.Empty):
                q.get(timeout=0.5)  # join 生效：停机后不再有帧
        finally:
            m.stop()


class TestInjectionAndSurfacing:
    def test_factory_called_with_pid_and_attrs(self, monkeypatch):
        """WM8：sampler_factory 注入点以 pid 调用；proc_info /
        version_warning 自 session 透出（SE2 承接），None 亦原样。"""
        clock = FakeClock()
        release = threading.Event()
        done = threading.Event()
        stub = StubSampler(PID, [], clock, release, done)
        warn_stub = StubSampler(PID, [], clock, release, done,
                                warning="[!] stub warn")
        monkeypatch.setattr(mon_mod, "time", clock)
        calls = []

        def factory(pid):
            calls.append(pid)
            return stub

        m = mon_mod.Monitor(PID, sampler_factory=factory)
        assert calls == [PID]
        assert m.proc_info is stub.proc_info
        assert m.version_warning is None

        m2 = mon_mod.Monitor(
            PID, sampler_factory=lambda pid: warn_stub)
        assert m2.version_warning == "[!] stub warn"

    def test_monitor_never_prints(self, monkeypatch, capsys):
        """WM8：Monitor 全生命周期不打印（呈现归 serve/CLI 层）——
        构造、采样、广播、停止均无 stdout/stderr 输出。"""
        m, stub, release = _harness(
            monkeypatch, [[_thread(frames=[_frame("f")])]])
        q = m.subscribe()
        m.start()
        try:
            _get(q)
        finally:
            _shutdown(m, release)
        out, err = capsys.readouterr()
        assert out == ""
        assert err == ""
