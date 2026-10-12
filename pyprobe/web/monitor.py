"""采样调度 + TopStats 聚合 + 订阅扇出（WM1–WM8，批 8a）。

纯 Python、框架无关（不 import fastapi/uvicorn）——monitor 不知 SSE 存在，
事件经订阅队列以 dict 载体外发，编码归 web/app.py（模块契约表）。线程内
仅捕获 ProcessExited（WM6 粘性终态）；其余异常照常终止线程。

订阅语义补充（WM5 域内的实现决策，WA3「首帧立即到达」的 monitor 侧
支点）：订阅时若已有广播帧，立即向新队列重放最近一帧；exit 帧同径粘性。
"""

import queue
import threading
import time
from dataclasses import asdict

from .. import dto
from ..errors import ProcessExited
from ..observe.sampling import Sampler
from ..observe.topstats import TopStats

_QUEUE_MAX = 64  # WM5：订阅队列上界（慢消费者不拖垮采样线程）
_TOP_N = 15      # WM4：top_functions 截断（对齐 render_top 的 top_n 惯例）


class Monitor:
    """Background sampling + aggregation + subscriber fan-out for one pid."""

    def __init__(self, pid: int, *, rate: float = 50, interval: float = 1.0,
                 sampler_factory=Sampler):
        self.pid = pid
        self.rate = rate
        self.interval = interval
        # WM1/WM8 — resolve the target at construction: sampler_factory
        # failures (PyProbeError subclasses) raise here, never inside the
        # background thread (SE1 语义承接).
        self.sampler = sampler_factory(pid)
        self.proc_info = self.sampler.proc_info
        # WM8 — surfaced for the serve/CLI layer, never printed here (SE2).
        self.version_warning = self.sampler.session.version_warning

        self.exited = False
        self._stats = TopStats()
        self._latest: list[dto.ThreadInfo] = []
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._last_frame: dict | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time = 0.0

    def start(self) -> None:
        """WM2 — spawn the background sampling thread.

        Clears a pre-existing stop flag: ``stop()`` before ``start()`` is a
        harmless no-op, not a bricked monitor.
        """
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """WM7 — idempotent stop: set the flag, join once (≤5s)."""
        self._stop_event.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=5)

    def subscribe(self) -> queue.Queue:
        """WM5 — a bounded subscriber queue (drop-oldest under pressure)."""
        q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        with self._lock:
            self._subscribers.append(q)
            last = self._last_frame
            if last is not None:
                q.put_nowait(last)  # 重放最近帧：SSE 首帧即时性（WA3 支点）
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """WM5 — idempotent unsubscribe."""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # -- sampling thread -------------------------------------------------

    def _run(self) -> None:
        sample_interval = 1.0 / self.rate
        self._start_time = time.monotonic()
        next_sample = self._start_time
        # WM3 — the first frame goes out with the first sample (t=0), then
        # once per interval: subscribers never wait a full interval for
        # their first frame.
        next_broadcast = self._start_time
        try:
            while not self._stop_event.is_set():
                threads = self.sampler.sample()  # ProcessExited → WM6
                self._stats.update(threads)      # WM2 — fold every sample
                self._latest = threads           # WM2 — latest snapshot
                now = time.monotonic()
                if now >= next_broadcast:
                    self._broadcast(self._stats_frame(now))
                    self.sampler.refresh_names()
                    next_broadcast += self.interval
                    if next_broadcast <= now:    # 落后重置，不补发帧
                        next_broadcast = now + self.interval
                # WM2 (PF2) — absolute-time scheduling: slow samples do not
                # accumulate drift; falling behind resets the baseline
                # instead of bursting catch-up samples.
                next_sample += sample_interval
                delay = next_sample - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_sample = time.monotonic()
        except ProcessExited:
            # WM6 — sticky terminal state; the server outlives the target
            # (A13), new subscribers get the exit frame immediately.
            self.exited = True
            self._broadcast({"type": "exit", "data": {}})

    def _stats_frame(self, now: float) -> dict:
        """WM4 — the stats frame payload (键集 pin，见契约行)."""
        samples = self._stats.samples
        data = {
            "process": {**asdict(self.proc_info),
                        "version_warning": self.version_warning},
            "elapsed": now - self._start_time,
            "samples": samples,
            "idle_samples": self._stats.idle_samples,
            "top_functions": self._top_functions(samples),
            "threads": self._threads_serialized(samples),
        }
        return {"type": "stats", "data": data}

    def _top_functions(self, samples: int) -> list[dict]:
        # WM4 — ranked by (-own, name), capped at _TOP_N; pct is the share
        # of samples, time = own/rate seconds; samples==0 → empty list.
        ranked = sorted(self._stats.own.items(),
                        key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
        out = []
        for name, own in ranked:
            total = self._stats.total.get(name, 0)
            out.append({
                "name": name,
                "own": own,
                "total": total,
                "own_pct": own / samples * 100 if samples else 0.0,
                "total_pct": total / samples * 100 if samples else 0.0,
                "time": own / self.rate,
            })
        return out

    def _threads_serialized(self, samples: int) -> list[dict]:
        # WM4/WM3 — the latest sample's full ThreadInfo list; the frame
        # chain is never truncated (A13: 实时调用栈增强点). Idle threads
        # carry idle=True with an empty chain.
        out = []
        for t in self._latest:
            own_pct = 0.0
            if t.frames and samples:
                top = t.frames[0]
                # Same "?" normalization the aggregation used (TX9 口径).
                key = top.name if top.name is not None else "?"
                own_pct = self._stats.own.get(key, 0) / samples * 100
            out.append({
                "native_tid": t.native_tid,
                "thread_id": t.thread_id,
                "name": t.name,
                "idle": t.idle,
                "own_pct": own_pct,
                "frames": [{"name": f.name, "filename": f.filename,
                            "line": f.line} for f in t.frames],
            })
        return out

    def _broadcast(self, frame: dict) -> None:
        # WM5 — put_nowait with drop-oldest: a full queue sheds its oldest
        # frame and keeps the newest (monitoring takes the latest); the
        # sampling thread is never blocked on delivery.
        with self._lock:
            self._last_frame = frame
            subscribers = list(self._subscribers)
        for q in subscribers:
            while True:
                try:
                    q.put_nowait(frame)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
