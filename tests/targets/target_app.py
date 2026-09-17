"""Target process for integration tests.

Spawns a main thread (sleeping) plus a background worker thread, prints its
PID, and runs until killed.  Used by ``tests/test_integration.py`` to exercise
the pyprobe library API against a live child process (so ``process_vm_readv``
works under the default ``ptrace_scope=1``).

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
    print(f"target PID: {os.getpid()}", flush=True)
    while True:
        time.sleep(2)


if __name__ == "__main__":
    main()
