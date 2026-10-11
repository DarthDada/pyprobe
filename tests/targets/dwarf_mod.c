/* pyprobe P1-5 integration fixture (contracts.md NA11 / ADR A12).
 *
 * A tiny shared object compiled with -g so native unwinding can resolve a
 * precise `file:line` for `pyprobe_spin_ms`. The function busy-waits (no
 * syscalls) so its frame stays on the target's stack while the test
 * attaches; the ctypes call releases the GIL, so a helper thread can still
 * print the READY handshake.
 */
#include <time.h>

int pyprobe_spin_ms(long ms) {
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);
    do {
        clock_gettime(CLOCK_MONOTONIC, &now);
    } while ((now.tv_sec - start.tv_sec) * 1000
             + (now.tv_nsec - start.tv_nsec) / 1000000 < ms);
    return (int)ms;
}
