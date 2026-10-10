"""CPU-bound target for sampling integration tests (§10.2-4).

Unlike ``target_app.py`` (both threads sleep → 全 idle，folded 必空),
this one keeps a worker thread in a pure-Python busy loop — no syscalls,
no GIL release — so sampling-based tests (``record`` / ``top``) are
guaranteed to observe the ``burn`` frame in 'R' state. The main thread
sleeps like target_app's.

Also runnable standalone:

    python -m tests.targets.spin_app
"""
import os
import threading
import time


def burn():
    x = 0
    while True:
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF


def main():
    t = threading.Thread(target=burn, name="spin-worker", daemon=True)
    t.start()
    print(f"READY {os.getpid()}", flush=True)
    while True:
        time.sleep(2)


if __name__ == "__main__":
    main()
