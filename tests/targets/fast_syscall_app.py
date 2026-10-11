"""Fast-syscall target for syscall-tracing integration tests (§10.2-7).

Loops ``time.sleep(0.2)`` — a 0.2s-scale ``clock_nanosleep`` per round, so
tracing tests capture events quickly while keeping the elapsed≥0.1s
assertion meaningful (the old 1s-scale target made every trace test pay
a full second per event window).

Also runnable standalone:

    python -m tests.targets.fast_syscall_app
"""
import os
import time


def main():
    print(f"READY {os.getpid()}", flush=True)
    while True:
        time.sleep(0.2)


if __name__ == "__main__":
    main()
