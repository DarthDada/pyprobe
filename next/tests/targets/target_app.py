"""Target process for next/ integration tests (contracts.md E2E1).

Spawns a main thread (sleeping) plus a background worker thread, then
prints a READY line with its PID — the readiness handshake the fixture
polls for (就绪轮询替代固定 sleep, §10.2-7): the line is emitted only
*after* the bg-worker thread has been started, so by the time the fixture
returns, the target's thread graph is fully up.

Also runnable standalone for manual testing:

    python -m tests.targets.target_app
"""
import os
import threading
import time


def worker():
    while True:
        time.sleep(1)


def main():
    t = threading.Thread(target=worker, name="bg-worker", daemon=True)
    t.start()
    print(f"READY {os.getpid()}", flush=True)
    while True:
        time.sleep(2)


if __name__ == "__main__":
    main()
