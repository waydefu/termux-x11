/* RCA-XFCE-3 instrument (NOT a qualification fixture): per-operation X latency.
 *
 *   DISPLAY=:3 p_oplat [--rounds 20] [--reps 10] [--seed 20260923]
 *
 * RCA-XFCE-3 showed X answering a bare GetInputFocus 6x (p50) to 16x (p99) slower with
 * the EXA GPU paths on (G) than with TERMUX_X11_DISABLE_EXA_GPU=1 (C), under the same
 * XFCE workload. That toggle switches four paths at once (EXA solid, EXA copy, EXA
 * composite incl. Gate A direct, present copy). This instrument separates them: each
 * case is ONE request class at ONE size, followed by one GetInputFocus round trip;
 * the client-side CLOCK_MONOTONIC time of (request + round trip) is the case's latency.
 *
 * cases   op x size x shape
 *   op     nop (round trip only) · solid (PolyFillRectangle) · copy_win (CopyArea window
 *          -> same window) · copy_pix (CopyArea offscreen depth-24 pixmap -> window) ·
 *          over_argb (Render Over a8r8g8b8 pixmap -> window, the only Gate A direct shape)
 *          · src_argb (Render Src, same pictures: never direct) · putimage (PutImage)
 *   size   16x16 64x64 256x256 1024x1024
 *   shape  single (1 request + round trip) · burst16 (16 requests + one round trip)
 * order   ROUNDS rounds; in every round each case runs REPS times, cases shuffled with a
 *         fixed seed per round (drift and order effects spread over all cases)
 * output  one line per case:  OPLAT {"op":..,"w":..,"shape":..,"n":..,"p50_us":..,...}
 *         X errors are counted per case (a case with errors measured something else).
 *         Draws go to a mapped override-redirect window at (0,0), i.e. into the screen
 *         pixmap - what an uncomposited client does.
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <xcb/xcb.h>
#include <xcb/render.h>

enum { OP_NOP, OP_SOLID, OP_COPY_WIN, OP_COPY_PIX, OP_OVER, OP_SRC, OP_PUT, OP_N };
static const char *OPN[OP_N] = {"nop", "solid", "copy_win", "copy_pix", "over_argb", "src_argb", "putimage"};
static const int SZ[] = {16, 64, 256, 1024};
#define NSZ 4
#define MAXCASE (OP_N * NSZ * 2)

typedef struct { int op, sz, burst; uint64_t *t; int n, cap, errors; } Case;

static xcb_connection_t *C;
static xcb_window_t W;
static xcb_gcontext_t GC;
static xcb_pixmap_t PIX24, PIX32;
static xcb_render_picture_t PWIN, PSRC;
static uint8_t *IMG;
static uint64_t rng = 20260923;

static uint64_t now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t) t.tv_sec * 1000000000ull + (uint64_t) t.tv_nsec;
}

static uint32_t rnd(void) {
    rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
    return (uint32_t) rng;
}

static xcb_render_pictformat_t find_format(uint8_t depth, int want_alpha) {
    xcb_render_query_pict_formats_reply_t *r =
        xcb_render_query_pict_formats_reply(C, xcb_render_query_pict_formats(C), NULL);
    xcb_render_pictformat_t f = 0;
    if (!r) return 0;
    xcb_render_pictforminfo_iterator_t it = xcb_render_query_pict_formats_formats_iterator(r);
    for (; it.rem; xcb_render_pictforminfo_next(&it)) {
        xcb_render_pictforminfo_t *i = it.data;
        if (i->type != XCB_RENDER_PICT_TYPE_DIRECT || i->depth != depth) continue;
        if (i->direct.red_mask != 0xff || i->direct.red_shift != 16) continue;
        if (want_alpha && (i->direct.alpha_mask != 0xff || i->direct.alpha_shift != 24)) continue;
        if (!want_alpha && i->direct.alpha_mask) continue;
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
    uint32_t fg = 0xff000000u | (rnd() & 0xffffff);
    switch (op) {
    case OP_NOP: break;
    case OP_SOLID:
        xcb_change_gc(C, GC, XCB_GC_FOREGROUND, &fg);
        xcb_poly_fill_rectangle(C, W, GC, 1, &r);
        break;
    case OP_COPY_WIN:
        xcb_copy_area(C, W, W, GC, 0, 1100, x, y, (uint16_t) s, (uint16_t) s);
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
    case OP_PUT:
        xcb_put_image(C, XCB_IMAGE_FORMAT_Z_PIXMAP, W, GC, (uint16_t) s, (uint16_t) s, x, y, 0, 24,
                      (uint32_t) s * s * 4, IMG);
        break;
    }
}

static int cmpu(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *) a, y = *(const uint64_t *) b;
    return x < y ? -1 : x > y;
}

static void drain_errors(Case *c) {
    xcb_generic_event_t *e;
    while ((e = xcb_poll_for_event(C))) {
        if (e->response_type == 0) c->errors++;
        free(e);
    }
}

int main(int argc, char **argv) {
    int rounds = 20, reps = 10;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--rounds")) rounds = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--reps")) reps = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--seed")) rng = strtoull(argv[i + 1], NULL, 10) | 1;
    }
    int scr;
    C = xcb_connect(NULL, &scr);
    if (xcb_connection_has_error(C)) { printf("FAIL connect\n"); return 1; }
    xcb_screen_t *S = xcb_setup_roots_iterator(xcb_get_setup(C)).data;
    uint16_t ww = S->width_in_pixels, wh = S->height_in_pixels;
    printf("ROOT %ux%u depth %u\n", ww, wh, S->root_depth);
    if (ww < 1100 || wh < 1100 + 1024) { printf("FAIL root too small\n"); return 1; }

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
    IMG = malloc(1024 * 1024 * 4);
    for (int i = 0; i < 1024 * 1024; i++) ((uint32_t *) IMG)[i] = 0x80000000u | (uint32_t) (i * 2654435761u >> 8);
    xcb_gcontext_t gc32 = xcb_generate_id(C);
    xcb_create_gc(C, gc32, PIX32, 0, NULL);
    for (int y = 0; y < 1024; y += 64)
        xcb_put_image(C, XCB_IMAGE_FORMAT_Z_PIXMAP, PIX32, gc32, 1024, 64, 0, (int16_t) y, 0, 32,
                      1024 * 64 * 4, IMG + (size_t) y * 1024 * 4);
    xcb_poly_fill_rectangle(C, PIX24, GC, 1, &(xcb_rectangle_t){0, 0, 1024, 1024});
    xcb_render_pictformat_t fa = find_format(32, 1), fw = window_format(S->root_visual);
    if (!fa || !fw) { printf("FAIL pictformat a8r8g8b8=%u window=%u\n", fa, fw); return 1; }
    PSRC = xcb_generate_id(C);
    xcb_render_create_picture(C, PSRC, PIX32, fa, 0, NULL);
    PWIN = xcb_generate_id(C);
    xcb_render_create_picture(C, PWIN, W, fw, 0, NULL);
    roundtrip();
    /* let the map + first paint settle before measuring */
    struct timespec ts = {1, 0};
    nanosleep(&ts, NULL);
    roundtrip();

    Case cs[MAXCASE];
    int nc = 0;
    for (int op = 0; op < OP_N; op++)
        for (int si = 0; si < NSZ; si++)
            for (int b = 0; b < 2; b++) {
                if (op == OP_NOP && (si || b)) continue;
                cs[nc] = (Case) {op, SZ[si], b, NULL, 0, rounds * reps, 0};
                cs[nc].t = calloc((size_t) cs[nc].cap, sizeof(uint64_t));
                nc++;
            }
    int order[MAXCASE];
    uint64_t t_begin = now_ns();
    for (int r = 0; r < rounds; r++) {
        for (int i = 0; i < nc; i++) order[i] = i;
        for (int i = nc - 1; i > 0; i--) {
            int j = (int) (rnd() % (uint32_t) (i + 1)), t = order[i];
            order[i] = order[j]; order[j] = t;
        }
        for (int oi = 0; oi < nc; oi++) {
            Case *c = &cs[order[oi]];
            for (int k = 0; k < reps; k++) {
                int m = c->burst ? 16 : 1;
                uint64_t t0 = now_ns();
                for (int q = 0; q < m; q++) issue(c->op, c->sz, k * 16 + q);
                roundtrip();
                c->t[c->n++] = now_ns() - t0;
            }
            drain_errors(c);
        }
    }
    uint64_t t_end = now_ns();
    for (int i = 0; i < nc; i++) {
        Case *c = &cs[i];
        uint64_t sum = 0;
        for (int k = 0; k < c->n; k++) sum += c->t[k];
        qsort(c->t, (size_t) c->n, sizeof(uint64_t), cmpu);
#define P(q) (c->t[(size_t) (((q) * (c->n - 1)) / 100)] / 1000)
        printf("OPLAT {\"op\":\"%s\",\"w\":%d,\"h\":%d,\"shape\":\"%s\",\"n\":%d,\"errors\":%d,"
               "\"mean_us\":%" PRIu64 ",\"p50_us\":%" PRIu64 ",\"p90_us\":%" PRIu64 ",\"p99_us\":%" PRIu64
               ",\"max_us\":%" PRIu64 "}\n",
               OPN[c->op], c->sz, c->sz, c->burst ? "burst16" : "single", c->n, c->errors,
               sum / (uint64_t) c->n / 1000, P(50), P(90), P(99), c->t[c->n - 1] / 1000);
    }
    printf("RESULT p_oplat cases=%d rounds=%d reps=%d wall_s=%.1f\n", nc, rounds, reps,
           (double) (t_end - t_begin) / 1e9);
    xcb_disconnect(C);
    return 0;
}
