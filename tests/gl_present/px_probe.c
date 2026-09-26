// px_probe - DIAGNOSTIC: sample one root-window pixel N times (xcb GetImage 1x1, Z_PIXMAP).
// Shows whether a client's frames actually land in the X server's framebuffer.
//   px_probe <x> <y> <samples> <interval_ms>   -> "PX t=<ms> 0x<pixel>" per sample
//   cc -O2 -Wall -o px_probe px_probe.c -lxcb
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <xcb/xcb.h>
int main(int argc, char **argv) {
    if (argc != 5) { fprintf(stderr, "usage: px_probe x y samples interval_ms\n"); return 2; }
    int x = atoi(argv[1]), y = atoi(argv[2]), n = atoi(argv[3]), ms = atoi(argv[4]);
    xcb_connection_t *c = xcb_connect(NULL, NULL);
    if (xcb_connection_has_error(c)) { printf("PX_FAIL connect\n"); return 3; }
    xcb_window_t root = xcb_setup_roots_iterator(xcb_get_setup(c)).data->root;
    struct timespec t0, t;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < n; i++) {
        xcb_get_image_reply_t *r = xcb_get_image_reply(c, xcb_get_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, root, x, y, 1, 1, ~0u), NULL);
        clock_gettime(CLOCK_MONOTONIC, &t);
        long el = (t.tv_sec - t0.tv_sec) * 1000 + (t.tv_nsec - t0.tv_nsec) / 1000000;
        if (!r || xcb_get_image_data_length(r) < 4) printf("PX t=%ld null\n", el);
        else printf("PX t=%ld 0x%08x\n", el, *(unsigned *) xcb_get_image_data(r));
        fflush(stdout);
        free(r);
        usleep(ms * 1000);
    }
    xcb_disconnect(c);
    return 0;
}
