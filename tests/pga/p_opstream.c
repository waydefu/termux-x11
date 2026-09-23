/* p_opstream.c - isolated, time-stamped X operations for a scheduler-trace decomposition
 * (PGA-GAP-2 step 2 instrument, NOT a qualification fixture).
 *
 *   DISPLAY=:3 p_opstream [--phase op:size:shape:count:gap_ms]... [--settle-ms 1500]
 *
 * p_oplat measures how long one EXA op takes end to end. This one makes each op findable
 * in an ftrace (atrace sched) capture so the 1-3 ms can be split into its parts: every op
 * is followed by one GetInputFocus round trip and then gap_ms of client idleness, so no
 * two ops overlap in X or in the renderer. Each op prints
 *   OP <phase> <i> <t0_boot_ns> <t1_boot_ns>
 * in CLOCK_BOOTTIME - the clock Android's ftrace uses (trace_clock = boot) - so the
 * client's window [t0, t1] can be laid over the trace directly.
 *   op     nop | solid | copy_pix | over_argb | src_argb      (same meaning as p_oplat)
 *   shape  single (1 request) | burst16 (16 requests, one round trip)
 * Default phases: nop, solid, copy_pix, over_argb at 64x64 with 5 ms gaps (~190 op/s, the
 * XFCE rate from RCA-XFCE-3), solid 64 with 120 ms gaps (lets the GPU go idle between
 * ops), solid 64 burst16, solid 1024.
 *   cc -O2 -Wall -o p_opstream p_opstream.c -lxcb -lxcb-render
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <xcb/xcb.h>
#include <xcb/render.h>

enum { OP_NOP, OP_SOLID, OP_COPY_PIX, OP_OVER, OP_SRC, OP_N };
static const char *OPN[OP_N] = {"nop", "solid", "copy_pix", "over_argb", "src_argb"};

static xcb_connection_t *C;
static xcb_window_t W;
static xcb_gcontext_t GC;
static xcb_pixmap_t PIX24, PIX32;
static xcb_render_picture_t PWIN, PSRC;

static uint64_t boot_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_BOOTTIME, &t);
    return (uint64_t) t.tv_sec * 1000000000ull + (uint64_t) t.tv_nsec;
}

static void sleep_ms(int ms) {
    struct timespec t = {ms / 1000, (long) (ms % 1000) * 1000000L};
    nanosleep(&t, NULL);
}

static xcb_render_pictformat_t find_format(uint8_t depth) {
    xcb_render_query_pict_formats_reply_t *r =
        xcb_render_query_pict_formats_reply(C, xcb_render_query_pict_formats(C), NULL);
    xcb_render_pictformat_t f = 0;
    if (!r) return 0;
    xcb_render_pictforminfo_iterator_t it = xcb_render_query_pict_formats_formats_iterator(r);
    for (; it.rem; xcb_render_pictforminfo_next(&it)) {
        xcb_render_pictforminfo_t *i = it.data;
        if (i->type != XCB_RENDER_PICT_TYPE_DIRECT || i->depth != depth) continue;
        if (i->direct.red_mask != 0xff || i->direct.red_shift != 16) continue;
        if (i->direct.alpha_mask != 0xff || i->direct.alpha_shift != 24) continue;
        f = i->id;
        break;
    }
    free(r);
    return f;
}

static xcb_render_pictformat_t window_format(xcb_visualid_t vis) {
    xcb_render_query_pict_formats_reply_t *r =
        xcb_render_query_pict_formats_reply(C, xcb_render_query_pict_formats(C), NULL);
    xcb_render_pictformat_t f = 0;
    if (!r) return 0;
    xcb_render_pictscreen_iterator_t s = xcb_render_query_pict_formats_screens_iterator(r);
    for (; s.rem && !f; xcb_render_pictscreen_next(&s)) {
        xcb_render_pictdepth_iterator_t d = xcb_render_pictscreen_depths_iterator(s.data);
        for (; d.rem && !f; xcb_render_pictdepth_next(&d)) {
            xcb_render_pictvisual_iterator_t v = xcb_render_pictdepth_visuals_iterator(d.data);
            for (; v.rem; xcb_render_pictvisual_next(&v))
                if (v.data->visual == vis) { f = v.data->format; break; }
        }
    }
    free(r);
    return f;
}

static void roundtrip(void) {
    free(xcb_get_input_focus_reply(C, xcb_get_input_focus(C), NULL));
}

static void issue(int op, int s, int k) {
    int16_t x = (int16_t) ((k * 37) % 64), y = (int16_t) ((k * 53) % 64);
    xcb_rectangle_t r = {x, y, (uint16_t) s, (uint16_t) s};
    uint32_t fg = 0xff000000u | (uint32_t) ((k * 2654435761u) & 0xffffff);
    switch (op) {
    case OP_NOP: break;
    case OP_SOLID:
        xcb_change_gc(C, GC, XCB_GC_FOREGROUND, &fg);
        xcb_poly_fill_rectangle(C, W, GC, 1, &r);
        break;
    case OP_COPY_PIX:
        xcb_copy_area(C, PIX24, W, GC, 0, 0, x, y, (uint16_t) s, (uint16_t) s);
        break;
    case OP_OVER:
    case OP_SRC:
        xcb_render_composite(C, op == OP_OVER ? XCB_RENDER_PICT_OP_OVER : XCB_RENDER_PICT_OP_SRC,
                             PSRC, XCB_RENDER_PICTURE_NONE, PWIN, 0, 0, 0, 0, x, y,
                             (uint16_t) s, (uint16_t) s);
        break;
    }
}

typedef struct { int op, size, burst, count, gap; } Phase;

static int parse_phase(const char *s, Phase *p) {
    char op[32], shape[16];
    if (sscanf(s, "%31[^:]:%d:%15[^:]:%d:%d", op, &p->size, shape, &p->count, &p->gap) != 5) return 0;
    p->op = -1;
    for (int i = 0; i < OP_N; i++) if (!strcmp(op, OPN[i])) p->op = i;
    if (p->op < 0 || p->size < 1 || p->size > 1024 || p->count < 1 || p->gap < 0) return 0;
    if (!strcmp(shape, "single")) p->burst = 0;
    else if (!strcmp(shape, "burst16")) p->burst = 1;
    else return 0;
    return 1;
}

int main(int argc, char **argv) {
    Phase ph[32];
    int np = 0, settle = 1500;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--phase")) {
            if (np == 32 || !parse_phase(argv[i + 1], &ph[np])) { printf("FAIL bad phase %s\n", argv[i + 1]); return 64; }
            np++;
        } else if (!strcmp(argv[i], "--settle-ms")) settle = atoi(argv[i + 1]);
        else { printf("FAIL usage %s\n", argv[i]); return 64; }
    }
    if (!np) {
        const char *def[] = {"nop:64:single:200:5", "solid:64:single:200:5", "copy_pix:64:single:200:5",
                             "over_argb:64:single:200:5", "solid:64:single:50:120",
                             "solid:64:burst16:100:5", "solid:1024:single:100:5"};
        for (unsigned i = 0; i < sizeof(def) / sizeof(*def); i++) parse_phase(def[i], &ph[np++]);
    }
    setvbuf(stdout, NULL, _IOFBF, 1 << 20);   /* no write() between ops: output is flushed at the end */
    int scr;
    C = xcb_connect(NULL, &scr);
    if (xcb_connection_has_error(C)) { printf("FAIL connect\n"); return 1; }
    xcb_screen_t *S = xcb_setup_roots_iterator(xcb_get_setup(C)).data;
    uint16_t ww = S->width_in_pixels, wh = S->height_in_pixels;
    printf("ROOT %ux%u depth %u\n", ww, wh, S->root_depth);
    if (ww < 1100 || wh < 1100) { printf("FAIL root too small\n"); return 1; }

    W = xcb_generate_id(C);
    uint32_t wv[] = {S->black_pixel, 1};
    xcb_create_window(C, S->root_depth, W, S->root, 0, 0, ww, wh, 0, XCB_WINDOW_CLASS_INPUT_OUTPUT,
                      S->root_visual, XCB_CW_BACK_PIXEL | XCB_CW_OVERRIDE_REDIRECT, wv);
    xcb_map_window(C, W);
    GC = xcb_generate_id(C);
    uint32_t gv[] = {0xff336699, 0};
    xcb_create_gc(C, GC, W, XCB_GC_FOREGROUND | XCB_GC_GRAPHICS_EXPOSURES, gv);
    PIX24 = xcb_generate_id(C);
    xcb_create_pixmap(C, S->root_depth, PIX24, W, 1024, 1024);
    PIX32 = xcb_generate_id(C);
    xcb_create_pixmap(C, 32, PIX32, W, 1024, 1024);
    uint8_t *img = malloc(1024 * 64 * 4);
    for (int i = 0; i < 1024 * 64; i++) ((uint32_t *) img)[i] = 0x80000000u | (uint32_t) (i * 2654435761u >> 8);
    xcb_gcontext_t gc32 = xcb_generate_id(C);
    xcb_create_gc(C, gc32, PIX32, 0, NULL);
    for (int y = 0; y < 1024; y += 64)
        xcb_put_image(C, XCB_IMAGE_FORMAT_Z_PIXMAP, PIX32, gc32, 1024, 64, 0, (int16_t) y, 0, 32, 1024 * 64 * 4, img);
    xcb_poly_fill_rectangle(C, PIX24, GC, 1, &(xcb_rectangle_t){0, 0, 1024, 1024});
    xcb_render_pictformat_t fa = find_format(32), fw = window_format(S->root_visual);
    if (!fa || !fw) { printf("FAIL pictformat a8r8g8b8=%u window=%u\n", fa, fw); return 1; }
    PSRC = xcb_generate_id(C);
    xcb_render_create_picture(C, PSRC, PIX32, fa, 0, NULL);
    PWIN = xcb_generate_id(C);
    xcb_render_create_picture(C, PWIN, W, fw, 0, NULL);
    roundtrip();
    sleep_ms(settle);
    roundtrip();

    int errors = 0;
    for (int p = 0; p < np; p++) {
        Phase *q = &ph[p];
        printf("PHASE %d %s %d %s %d %d %" PRIu64 "\n", p, OPN[q->op], q->size, q->burst ? "burst16" : "single",
               q->count, q->gap, boot_ns());
        for (int i = 0; i < q->count; i++) {
            uint64_t t0 = boot_ns();
            for (int b = 0; b < (q->burst ? 16 : 1); b++) issue(q->op, q->size, i * 16 + b);
            roundtrip();
            uint64_t t1 = boot_ns();
            printf("OP %d %d %" PRIu64 " %" PRIu64 "\n", p, i, t0, t1);
            xcb_generic_event_t *e;
            while ((e = xcb_poll_for_event(C))) { if (e->response_type == 0) errors++; free(e); }
            sleep_ms(q->gap);
        }
        sleep_ms(300);                            /* phase boundary, visible in the trace */
    }
    printf("RESULT p_opstream phases=%d errors=%d\n", np, errors);
    xcb_disconnect(C);
    return 0;
}
