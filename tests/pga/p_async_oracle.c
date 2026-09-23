/* p_async_oracle.c - pixel verifier for the EXA async prototype (PGA-GAP-2). NOT a Gate A oracle.
 *
 *   DISPLAY=:3 p_async_oracle [--seed N] [--ops N] [--mutant skip-copy|none]
 *
 * Random solid fills, CopyAreas (window <-> depth-24 pixmaps, depth-32 pixmap -> itself and
 * another), Render Src composites (depth-32 pixmap -> window), short-lived pixmaps that are
 * filled, copied from and freed immediately, all issued in unsynchronised groups of 1..12 so
 * GPU work is still in flight when the next request, the FreePixmap or the GetImage arrives.
 * After every group one GetImage of a random target region is compared with a client-side
 * software model:
 *   RGB     (low 24 bits of depth-24 pixels, all 32 bits of depth-32 pixels) must match exactly.
 *   X byte  (top byte of depth-24 pixels) is not modelled: the synchronous GPU path is known to
 *           differ from the CPU there. All X bytes read are folded into xdigest instead; the
 *           async build must reproduce the sync build's xdigest for the same seed and ops.
 * --mutant skip-copy leaves CopyArea out of the model: the run MUST report mismatches (a
 * verifier that cannot go red is not a verifier).
 * Output: ASYNC_ORACLE {"seed":..,"ops":..,"groups":..,"getimages":..,"rgb_mismatch_images":..,
 *         "rgb_mismatch_pixels":..,"x_errors":..,"xdigest":"..","x_nonzero":..,"first_fail":".."}
 *   cc -O2 -Wall -o p_async_oracle p_async_oracle.c -lxcb -lxcb-render
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <xcb/xcb.h>
#include <xcb/render.h>

#define S 256                                  /* every target is S x S */
enum { T_WIN, T_P24A, T_P24B, T_P32A, T_P32B, T_N };
static const char *TN[T_N] = {"win", "p24a", "p24b", "p32a", "p32b"};
static const int TDEPTH[T_N] = {24, 24, 24, 32, 32};

static xcb_connection_t *C;
static xcb_drawable_t D[T_N];
static xcb_gcontext_t GC24, GC32;
static xcb_render_picture_t PICWIN, PICP32A, PICP32B;
static uint32_t M[T_N][S * S];                /* model, RGB (depth 24) or ARGB (depth 32) */
static uint64_t rng;
static int mutant_skip_copy;
static int x_errors;

static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t) rng; }
static int rr(int n) { return (int) (rnd() % (uint32_t) n); }
static uint32_t mask(int t) { return TDEPTH[t] == 24 ? 0x00ffffffu : 0xffffffffu; }

static void rect(int *x, int *y, int *w, int *h) {
    *w = 1 + rr(S / 2); *h = 1 + rr(S / 2);
    *x = rr(S - *w + 1); *y = rr(S - *h + 1);
}

static void m_fill(int t, int x, int y, int w, int h, uint32_t px) {
    for (int j = y; j < y + h; j++)
        for (int i = x; i < x + w; i++) M[t][j * S + i] = px & mask(t);
}

static void m_copy(int s, int d, int sx, int sy, int dx, int dy, int w, int h) {
    static uint32_t tmp[S * S];
    for (int j = 0; j < h; j++) memcpy(&tmp[j * w], &M[s][(sy + j) * S + sx], (size_t) w * 4);
    for (int j = 0; j < h; j++) memcpy(&M[d][(dy + j) * S + dx], &tmp[j * w], (size_t) w * 4);
}

static xcb_gcontext_t gc_for(int t) { return TDEPTH[t] == 24 ? GC24 : GC32; }

static void op_solid(void) {
    int t = rr(T_N), x, y, w, h;
    /* depth 24: mostly X byte 0, sometimes not (exercises the async X-byte bookkeeping) */
    uint32_t px = rnd();
    if (TDEPTH[t] == 24 && rr(4)) px &= 0x00ffffffu;
    rect(&x, &y, &w, &h);
    xcb_change_gc(C, gc_for(t), XCB_GC_FOREGROUND, &px);
    xcb_poly_fill_rectangle(C, D[t], gc_for(t), 1, &(xcb_rectangle_t){(int16_t) x, (int16_t) y, (uint16_t) w, (uint16_t) h});
    m_fill(t, x, y, w, h, px);
}

static void op_copy(void) {
    int s, d, x, y, w, h, dx, dy;
    if (rr(3)) { s = rr(3); d = rr(3); }                       /* depth-24 family (incl. src == dst) */
    else { s = T_P32A + rr(2); d = T_P32A + rr(2); }
    rect(&x, &y, &w, &h);
    dx = rr(S - w + 1); dy = rr(S - h + 1);
    xcb_copy_area(C, D[s], D[d], gc_for(d), (int16_t) x, (int16_t) y, (int16_t) dx, (int16_t) dy, (uint16_t) w, (uint16_t) h);
    if (!mutant_skip_copy) m_copy(s, d, x, y, dx, dy, w, h);
}

static void op_composite_src(void) {                          /* Render Src a8r8g8b8 -> x8r8g8b8 window */
    int x, y, w, h, dx, dy;
    xcb_render_picture_t src = rr(2) ? PICP32A : PICP32B;
    int s = src == PICP32A ? T_P32A : T_P32B;
    rect(&x, &y, &w, &h);
    dx = rr(S - w + 1); dy = rr(S - h + 1);
    xcb_render_composite(C, XCB_RENDER_PICT_OP_SRC, src, XCB_RENDER_PICTURE_NONE, PICWIN,
                         (int16_t) x, (int16_t) y, 0, 0, (int16_t) dx, (int16_t) dy, (uint16_t) w, (uint16_t) h);
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) M[T_WIN][(dy + j) * S + dx + i] = M[s][(y + j) * S + x + i] & 0x00ffffffu;
}

static void op_temp_pixmap(void) {                             /* fill, copy out, free - no sync in between */
    int d = rr(3), x, y, w, h, dx, dy;
    xcb_pixmap_t tp = xcb_generate_id(C);
    uint32_t px = rnd() & 0x00ffffffu;
    xcb_create_pixmap(C, 24, tp, D[T_WIN], S, S);
    xcb_change_gc(C, GC24, XCB_GC_FOREGROUND, &px);
    xcb_poly_fill_rectangle(C, tp, GC24, 1, &(xcb_rectangle_t){0, 0, S, S});
    rect(&x, &y, &w, &h);
    dx = rr(S - w + 1); dy = rr(S - h + 1);
    xcb_copy_area(C, tp, D[d], GC24, (int16_t) x, (int16_t) y, (int16_t) dx, (int16_t) dy, (uint16_t) w, (uint16_t) h);
    xcb_free_pixmap(C, tp);
    if (!mutant_skip_copy) m_fill(d, dx, dy, w, h, px);
}

static uint64_t xdig = 1469598103934665603ull, xnz;
static long rgb_bad_images, rgb_bad_pixels, getimages;
static char first_fail[160];

static void check(int t) {
    int x, y, w, h;
    rect(&x, &y, &w, &h);
    xcb_get_image_reply_t *r = xcb_get_image_reply(C, xcb_get_image(C, XCB_IMAGE_FORMAT_Z_PIXMAP, D[t],
                                  (int16_t) x, (int16_t) y, (uint16_t) w, (uint16_t) h, ~0u), NULL);
    getimages++;
    if (!r) { x_errors++; return; }
    const uint32_t *p = (const uint32_t *) xcb_get_image_data(r);
    int stride = xcb_get_image_data_length(r) / 4 / h;          /* 32bpp rows */
    long bad = 0;
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) {
            uint32_t got = p[j * stride + i], want = M[t][(y + j) * S + x + i];
            if ((got & mask(t)) != want) {
                if (!bad && !first_fail[0])
                    snprintf(first_fail, sizeof(first_fail), "%s(%d,%d) got=%08x want=%08x getimage#%ld",
                             TN[t], x + i, y + j, got & mask(t), want, getimages);
                bad++;
            }
            if (TDEPTH[t] == 24) {
                uint8_t xb = (uint8_t) (got >> 24);
                xdig = (xdig ^ xb) * 1099511628211ull;
                xnz += xb != 0;
            }
        }
    if (bad) { rgb_bad_images++; rgb_bad_pixels += bad; }
    free(r);
}

int main(int argc, char **argv) {
    int nops = 4000;
    rng = 20260923;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--seed")) rng = strtoull(argv[i + 1], NULL, 10) | 1;
        else if (!strcmp(argv[i], "--ops")) nops = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--mutant")) mutant_skip_copy = !strcmp(argv[i + 1], "skip-copy");
        else { printf("FAIL usage %s\n", argv[i]); return 64; }
    }
    uint64_t seed = rng;
    int scr;
    C = xcb_connect(NULL, &scr);
    if (xcb_connection_has_error(C)) { printf("FAIL connect\n"); return 1; }
    xcb_screen_t *SC = xcb_setup_roots_iterator(xcb_get_setup(C)).data;
    if (SC->root_depth != 24) { printf("FAIL root depth %u\n", SC->root_depth); return 1; }
    D[T_WIN] = xcb_generate_id(C);
    uint32_t wv[] = {SC->black_pixel, 1};
    xcb_create_window(C, 24, D[T_WIN], SC->root, 0, 0, S, S, 0, XCB_WINDOW_CLASS_INPUT_OUTPUT, SC->root_visual,
                      XCB_CW_BACK_PIXEL | XCB_CW_OVERRIDE_REDIRECT, wv);
    xcb_map_window(C, D[T_WIN]);
    for (int t = 1; t < T_N; t++) {
        D[t] = xcb_generate_id(C);
        xcb_create_pixmap(C, (uint8_t) TDEPTH[t], D[t], D[T_WIN], S, S);
    }
    uint32_t gv[] = {0, 0};                                     /* fg, graphics_exposures off */
    GC24 = xcb_generate_id(C); xcb_create_gc(C, GC24, D[T_WIN], XCB_GC_FOREGROUND | XCB_GC_GRAPHICS_EXPOSURES, gv);
    GC32 = xcb_generate_id(C); xcb_create_gc(C, GC32, D[T_P32A], XCB_GC_FOREGROUND | XCB_GC_GRAPHICS_EXPOSURES, gv);
    /* formats */
    xcb_render_query_pict_formats_reply_t *fr = xcb_render_query_pict_formats_reply(C, xcb_render_query_pict_formats(C), NULL);
    xcb_render_pictformat_t fa = 0, fw = 0;
    for (xcb_render_pictforminfo_iterator_t it = xcb_render_query_pict_formats_formats_iterator(fr); it.rem; xcb_render_pictforminfo_next(&it)) {
        xcb_render_pictforminfo_t *f = it.data;
        if (f->type != XCB_RENDER_PICT_TYPE_DIRECT || f->direct.red_shift != 16 || f->direct.red_mask != 0xff) continue;
        if (f->depth == 32 && f->direct.alpha_mask == 0xff && f->direct.alpha_shift == 24) fa = f->id;
        if (f->depth == 24 && !f->direct.alpha_mask) fw = f->id;
    }
    free(fr);
    if (!fa || !fw) { printf("FAIL formats\n"); return 1; }
    PICWIN = xcb_generate_id(C); xcb_render_create_picture(C, PICWIN, D[T_WIN], fw, 0, NULL);
    PICP32A = xcb_generate_id(C); xcb_render_create_picture(C, PICP32A, D[T_P32A], fa, 0, NULL);
    PICP32B = xcb_generate_id(C); xcb_render_create_picture(C, PICP32B, D[T_P32B], fa, 0, NULL);
    /* defined start: every target filled with a known value */
    for (int t = 0; t < T_N; t++) {
        uint32_t px = 0x00102030u * (uint32_t) (t + 1) | (TDEPTH[t] == 32 ? 0xff000000u : 0);
        xcb_change_gc(C, gc_for(t), XCB_GC_FOREGROUND, &px);
        xcb_poly_fill_rectangle(C, D[t], gc_for(t), 1, &(xcb_rectangle_t){0, 0, S, S});
        m_fill(t, 0, 0, S, S, px);
    }
    free(xcb_get_input_focus_reply(C, xcb_get_input_focus(C), NULL));

    int done = 0, groups = 0;
    while (done < nops) {
        int g = 1 + rr(12);
        for (int k = 0; k < g && done < nops; k++, done++) {
            int o = rr(100);
            if (o < 45) op_solid();
            else if (o < 80) op_copy();
            else if (o < 90) op_composite_src();
            else op_temp_pixmap();
        }
        groups++;
        check(rr(T_N));
        xcb_generic_event_t *e;
        while ((e = xcb_poll_for_event(C))) { if (e->response_type == 0) x_errors++; free(e); }
    }
    for (int t = 0; t < T_N; t++) check(t);                   /* final sweep, one region per target */
    printf("ASYNC_ORACLE {\"seed\":%" PRIu64 ",\"ops\":%d,\"groups\":%d,\"getimages\":%ld,\"rgb_mismatch_images\":%ld,"
           "\"rgb_mismatch_pixels\":%ld,\"x_errors\":%d,\"xdigest\":\"%016" PRIx64 "\",\"x_nonzero\":%" PRIu64
           ",\"mutant\":\"%s\",\"first_fail\":\"%s\"}\n",
           seed, nops, groups, getimages, rgb_bad_images, rgb_bad_pixels, x_errors, xdig, xnz,
           mutant_skip_copy ? "skip-copy" : "none", first_fail);
    xcb_disconnect(C);
    return (rgb_bad_images || x_errors) ? 2 : 0;
}
