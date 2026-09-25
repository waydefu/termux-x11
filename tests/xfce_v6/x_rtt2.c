/* x_rtt2.c - X request round-trip latency probe, v2 (XFCE-V6-BASELINE-01 tool T1).
 * Same probe as tests/pga/x_rtt.c: one GetInputFocus round trip every <period_ms> on one
 * persistent connection, printed as
 *   RTT <epoch_s> <latency_ms>
 * until the connection dies or <duration_s> passes. v2 adds one first line
 *   TRACER <TracerPid>
 * read from /proc/self/status before connecting, so every capture proves whether the probe
 * itself was ptrace-traced (by PRoot). Nothing else changes: rca_report.py only parses the
 * 3-field RTT lines and ignores the extra line.
 * Built twice (see build.sh): glibc inside PRoot = the traced probe; bionic with Termux clang,
 * launched through TermuxService = the untraced probe.
 *   x_rtt2 :3 250 400
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <xcb/xcb.h>
static double now(clockid_t c) { struct timespec t; clock_gettime(c, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static long tracer_pid(void) {
    char line[256];
    long tp = -1;
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return -1;
    while (fgets(line, sizeof line, f))
        if (strncmp(line, "TracerPid:", 10) == 0) { tp = strtol(line + 10, NULL, 10); break; }
    fclose(f);
    return tp;
}
int main(int argc, char **argv) {
    if (argc < 4) return 64;
    setvbuf(stdout, NULL, _IOLBF, 0);
    printf("TRACER %ld\n", tracer_pid());
    xcb_connection_t *c = xcb_connect(argv[1], NULL);
    if (xcb_connection_has_error(c)) { printf("RTT_CONNECT_FAIL\n"); return 1; }
    double period = atof(argv[2]) / 1000.0, end = now(CLOCK_MONOTONIC) + atof(argv[3]);
    while (now(CLOCK_MONOTONIC) < end) {
        double t0 = now(CLOCK_MONOTONIC);
        xcb_get_input_focus_reply_t *r = xcb_get_input_focus_reply(c, xcb_get_input_focus(c), NULL);
        double dt = now(CLOCK_MONOTONIC) - t0;
        if (!r) { printf("RTT_DEAD %.3f\n", now(CLOCK_REALTIME)); return 0; }
        free(r);
        printf("RTT %.3f %.3f\n", now(CLOCK_REALTIME), dt * 1000.0);
        double rest = period - dt;
        if (rest > 0) { struct timespec s = { (time_t) rest, (long) ((rest - (time_t) rest) * 1e9) }; nanosleep(&s, NULL); }
    }
    return 0;
}
