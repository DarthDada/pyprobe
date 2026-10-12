"""Busy-loop target for the web E2E test (contracts.md E2E-W1).

target_app 的两个线程都处于睡眠态（S），会被空闲启发式剪枝——采样帧
永远不含函数名；本目标提供一个持续 R 态的命名工作线程，保证首个 stats
帧即含活跃帧链。就绪握手同 conftest 模式（READY 行在 worker 线程启动
后打印）。
"""
import os
import threading
import time


def worker():
    while True:
        pass  # R 态忙循环：采样可见活跃 Python 帧


def main():
    t = threading.Thread(target=worker, name="busy-worker", daemon=True)
    t.start()
    print(f"READY {os.getpid()}", flush=True)
    while True:
        time.sleep(2)


if __name__ == "__main__":
    main()
