"""Target holding a -g C frame for native source-line resolution.

Fixture for contracts.md NA11 / ADR A12: reads ``PYPROBE_DWARF_MOD`` (a
``-g`` shared object built by the ``dwarf_mod`` test fixture), then calls
``pyprobe_spin_ms`` on the main thread in a loop. The ctypes call releases
the GIL, so a helper thread prints the READY handshake once the C frame is
definitely live.

    PYPROBE_DWARF_MOD=/path/libdwarfmod.so python -m tests.targets.dwarf_app
"""
import ctypes
import os
import threading
import time


def _ready(pid):
    time.sleep(0.5)  # ensure the main thread is already inside the C frame
    print(f"READY {pid}", flush=True)


def main():
    lib = ctypes.CDLL(os.environ["PYPROBE_DWARF_MOD"])
    spin = lib.pyprobe_spin_ms
    spin.argtypes = [ctypes.c_long]
    spin.restype = ctypes.c_int
    threading.Thread(target=_ready, args=(os.getpid(),), daemon=True).start()
    while True:
        spin(2000)


if __name__ == "__main__":
    main()
