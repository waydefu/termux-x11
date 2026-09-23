/* x_rtt.c - X request round-trip latency probe (RCA instrument, not a fixture).
 * One GetInputFocus round trip every <period_ms>, printed as
 *   RTT <epoch_s> <latency_ms>
 * until the connection dies or <duration_s> passes. A server whose dispatch loop is
 * blocked (in a synchronous wait, or starved of CPU) shows up here directly; spawning a
 * process per sample under PRoot would add ~100 ms of exec cost and hide it.
 *   cc -O2 -o x_rtt x_rtt.c -lxcb && x_rtt :3 250 400
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <xcb/xcb.h>
static double now(clockid_t c) { struct timespec t; clock_gettime(c, &t); return t.tv_sec + t.tv_nsec / 1e9; }
int main(int argc, char **argv) {
    if (argc < 4) return 64;
    xcb_connection_t *c = xcb_connect(argv[1], NULL);
    if (xcb_connection_has_error(c)) { printf("RTT_CONNECT_FAIL\n"); return 1; }
    double period = atof(argv[2]) / 1000.0, end = now(CLOCK_MONOTONIC) + atof(argv[3]);
    setvbuf(stdout, NULL, _IOLBF, 0);
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
