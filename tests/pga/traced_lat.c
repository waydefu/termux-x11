/* traced_lat.c - how long does a PRoot-trapped syscall wait? (RCA instrument, not a fixture)
 * Every <period_ms> it times two syscalls back to back and prints
 *   TL <epoch_s> <fstatat_us> <getppid_us>
 * until <duration_s> passes.
 *   fstatat("/usr/bin")  path-based: PRoot's seccomp filter hands it to the tracer, so it
 *                        waits for the ONE tracer thread (queue + translation).
 *   getppid()            not in the filter: runs in the kernel without the tracer. This is
 *                        the control - if it slows down as much as fstatat, the delay is
 *                        plain CPU contention, not the tracer.
 *   cc -O2 -o traced_lat traced_lat.c && traced_lat 100 300
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
static double now(clockid_t c) { struct timespec t; clock_gettime(c, &t); return t.tv_sec + t.tv_nsec / 1e9; }
int main(int argc, char **argv) {
    if (argc < 3) return 64;
    double period = atof(argv[1]) / 1000.0, end = now(CLOCK_MONOTONIC) + atof(argv[2]);
    setvbuf(stdout, NULL, _IOLBF, 0);
    struct stat st;
    while (now(CLOCK_MONOTONIC) < end) {
        double t0 = now(CLOCK_MONOTONIC);
        if (fstatat(AT_FDCWD, "/usr/bin", &st, AT_SYMLINK_NOFOLLOW) != 0) { printf("TL_STAT_FAIL\n"); return 1; }
        double t1 = now(CLOCK_MONOTONIC);
        syscall(SYS_getppid);            /* raw syscall: libc must not cache it */
        double t2 = now(CLOCK_MONOTONIC);
        printf("TL %.3f %.1f %.1f\n", now(CLOCK_REALTIME), (t1 - t0) * 1e6, (t2 - t1) * 1e6);
        double rest = period - (t2 - t0);
        if (rest > 0) { struct timespec s = { (time_t) rest, (long) ((rest - (time_t) rest) * 1e9) }; nanosleep(&s, NULL); }
    }
    return 0;
}
