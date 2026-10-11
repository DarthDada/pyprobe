"""Deterministic syscall target for the batch-7 old/new CLI diff gate.

Loops ``os.getuid()`` — a no-argument syscall with a constant return value —
so both tracing sessions observe byte-identical events modulo elapsed timing.
The READY handshake mirrors the other targets (E2E1).
"""
import os


def main():
    print(f"READY {os.getpid()}", flush=True)
    while True:
        os.getuid()


if __name__ == "__main__":
    main()
