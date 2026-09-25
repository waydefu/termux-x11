/* x_grab_stall.c - deliberate X server stalls for the probe-sensitivity preflight
 * (XFCE-V6-BASELINE-01 tool T3, freeze section 5.2).
 * While one client holds GrabServer, the server processes no request from any other client,
 * so a round-trip probe on another connection must see its reply delayed until the ungrab.
 *   x_grab_stall <display> <count> <hold_ms> <interval_s>
 * prints, per stall,
 *   GRAB <start_epoch_s> <end_epoch_s>
 * start = just before GrabServer is sent (the server may process it a little later),
 * end   = after UngrabServer was processed (a GetInputFocus round trip on the same
 *         connection returns only after the preceding UngrabServer).
 * Never pointed at Stable :1.
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <xcb/xcb.h>
static double wall(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static void sleep_s(double s) { struct timespec t = { (time_t) s, (long) ((s - (time_t) s) * 1e9) }; nanosleep(&t, NULL); }
static int sync_rt(xcb_connection_t *c) {
    xcb_get_input_focus_reply_t *r = xcb_get_input_focus_reply(c, xcb_get_input_focus(c), NULL);
    if (!r) return -1;
    free(r);
    return 0;
}
int main(int argc, char **argv) {
    if (argc < 5) return 64;
    int count = atoi(argv[2]);
    double hold = atof(argv[3]) / 1000.0, interval = atof(argv[4]);
    setvbuf(stdout, NULL, _IOLBF, 0);
    xcb_connection_t *c = xcb_connect(argv[1], NULL);
    if (xcb_connection_has_error(c)) { printf("GRAB_CONNECT_FAIL\n"); return 1; }
    for (int i = 0; i < count; i++) {
        sleep_s(interval);
        double t0 = wall();
        xcb_grab_server(c);
        if (sync_rt(c)) { printf("GRAB_DEAD\n"); return 1; }   /* the grab is now in force */
        sleep_s(hold);
        xcb_ungrab_server(c);
        if (sync_rt(c)) { printf("GRAB_DEAD\n"); return 1; }
        printf("GRAB %.3f %.3f\n", t0, wall());
    }
    xcb_disconnect(c);
    return 0;
}
