/* p_depth_probe.c - isolate which EXA operation returns wrong pixels (RCA probe, NOT a fixture).
 *
 *   DISPLAY=:3 p_depth_probe
 *
 * p_async_oracle on 0245ff3 (G and GA, seed 11) failed where C and Xvfb pass; the first bad
 * pixel was a depth-32 pixmap reading 00000000 instead of the modelled value. Each step below
 * does ONE kind of request on fresh drawables, then GetImage of one pixel inside and one pixel
 * just outside the touched rectangle, and prints
 *   STEP <name> in got=<hex> want=<hex> <PASS|FAIL> out got=<hex> want=<hex> <PASS|FAIL>
 * Values compared under the drawable's depth mask (24: low 24 bits).
 *   cc -O2 -Wall -o p_depth_probe p_depth_probe.c -lxcb -lxcb-render
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <xcb/xcb.h>
#include <xcb/render.h>

static xcb_connection_t *C;
static xcb_screen_t *SC;
static xcb_window_t W;
static int fails;

static uint32_t px(xcb_drawable_t d, int x, int y) {
    xcb_get_image_reply_t *r = xcb_get_image_reply(C, xcb_get_image(C, XCB_IMAGE_FORMAT_Z_PIXMAP, d, (int16_t) x, (int16_t) y, 1, 1, ~0u), NULL);
    uint32_t v = 0xdeadbeef;
    if (r) { v = *(uint32_t *) xcb_get_image_data(r); free(r); }
    return v;
}

static void report(const char *name, xcb_drawable_t d, int depth, int ix, int iy, uint32_t win, int ox, int oy, uint32_t wout) {
    uint32_t m = depth == 24 ? 0xffffffu : 0xffffffffu;
    uint32_t gi = px(d, ix, iy) & m, go = px(d, ox, oy) & m;
    int pi = gi == (win & m), po = go == (wout & m);
    fails += !pi + !po;
    printf("STEP %-28s in got=%08x want=%08x %s  out got=%08x want=%08x %s\n", name, gi, win & m, pi ? "PASS" : "FAIL",
           go, wout & m, po ? "PASS" : "FAIL");
}

static xcb_pixmap_t mkpix(int depth) {
    xcb_pixmap_t p = xcb_generate_id(C);
    xcb_create_pixmap(C, (uint8_t) depth, p, W, 64, 64);
    return p;
}

static xcb_gcontext_t mkgc(xcb_drawable_t d, uint32_t fg) {
    xcb_gcontext_t g = xcb_generate_id(C);
    uint32_t v[] = {fg, 0};
    xcb_create_gc(C, g, d, XCB_GC_FOREGROUND | XCB_GC_GRAPHICS_EXPOSURES, v);
    return g;
}

static void fill(xcb_drawable_t d, xcb_gcontext_t g, uint32_t fg, int x, int y, int w, int h) {
    xcb_change_gc(C, g, XCB_GC_FOREGROUND, &fg);
    xcb_poly_fill_rectangle(C, d, g, 1, &(xcb_rectangle_t){(int16_t) x, (int16_t) y, (uint16_t) w, (uint16_t) h});
}

int main(void) {
    int scr;
    C = xcb_connect(NULL, &scr);
    if (xcb_connection_has_error(C)) { printf("FAIL connect\n"); return 1; }
    SC = xcb_setup_roots_iterator(xcb_get_setup(C)).data;
    W = xcb_generate_id(C);
    uint32_t wv[] = {SC->black_pixel, 1};
    xcb_create_window(C, 24, W, SC->root, 0, 0, 128, 128, 0, XCB_WINDOW_CLASS_INPUT_OUTPUT, SC->root_visual,
                      XCB_CW_BACK_PIXEL | XCB_CW_OVERRIDE_REDIRECT, wv);
    xcb_map_window(C, W);
    free(xcb_get_input_focus_reply(C, xcb_get_input_focus(C), NULL));

    /* depth 32 solid: whole pixmap, then a sub-rectangle */
    xcb_pixmap_t a = mkpix(32); xcb_gcontext_t ga = mkgc(a, 0);
    fill(a, ga, 0xff102030u, 0, 0, 64, 64);
    report("d32 solid full", a, 32, 5, 5, 0xff102030u, 63, 63, 0xff102030u);
    fill(a, ga, 0xa6f6d190u, 10, 10, 20, 20);
    report("d32 solid rect", a, 32, 15, 15, 0xa6f6d190u, 40, 40, 0xff102030u);
    fill(a, ga, 0x00000000u, 30, 30, 5, 5);
    report("d32 solid alpha0", a, 32, 31, 31, 0x00000000u, 40, 40, 0xff102030u);
    fill(a, ga, 0x80ff0000u, 40, 40, 8, 8);
    report("d32 solid alpha80", a, 32, 41, 41, 0x80ff0000u, 50, 50, 0xff102030u);

    /* depth 32 copy: a -> b */
    xcb_pixmap_t b = mkpix(32); xcb_gcontext_t gb = mkgc(b, 0);
    fill(b, gb, 0xff445566u, 0, 0, 64, 64);
    report("d32 solid full (b)", b, 32, 1, 1, 0xff445566u, 60, 60, 0xff445566u);
    xcb_copy_area(C, a, b, gb, 10, 10, 0, 0, 20, 20);
    report("d32 copy a->b", b, 32, 5, 5, 0xa6f6d190u, 30, 30, 0xff445566u);
    xcb_copy_area(C, a, b, gb, 40, 40, 50, 50, 8, 8);
    report("d32 copy alpha80 a->b", b, 32, 51, 51, 0x80ff0000u, 45, 45, 0xff445566u);

    /* depth 24: pixmap solid, copy to window, solid on window */
    xcb_pixmap_t c = mkpix(24); xcb_gcontext_t gc = mkgc(c, 0);
    fill(c, gc, 0x00123456u, 0, 0, 64, 64);
    report("d24 solid full", c, 24, 5, 5, 0x123456u, 63, 63, 0x123456u);
    fill(c, gc, 0x00abcdefu, 10, 10, 20, 20);
    report("d24 solid rect", c, 24, 15, 15, 0xabcdefu, 40, 40, 0x123456u);
    xcb_gcontext_t gw = mkgc(W, 0);
    fill(W, gw, 0x00010203u, 0, 0, 128, 128);
    report("win solid full", W, 24, 5, 5, 0x010203u, 127, 127, 0x010203u);
    xcb_copy_area(C, c, W, gw, 10, 10, 60, 60, 20, 20);
    report("d24 copy pix->win", W, 24, 65, 65, 0xabcdefu, 100, 100, 0x010203u);
    xcb_copy_area(C, W, c, gc, 60, 60, 0, 0, 5, 5);
    report("d24 copy win->pix", c, 24, 2, 2, 0xabcdefu, 7, 7, 0x123456u);

    /* Render Src depth 32 -> window */
    xcb_render_query_pict_formats_reply_t *fr = xcb_render_query_pict_formats_reply(C, xcb_render_query_pict_formats(C), NULL);
    xcb_render_pictformat_t fa = 0, fw = 0;
    for (xcb_render_pictforminfo_iterator_t it = xcb_render_query_pict_formats_formats_iterator(fr); it.rem; xcb_render_pictforminfo_next(&it)) {
        xcb_render_pictforminfo_t *f = it.data;
        if (f->type != XCB_RENDER_PICT_TYPE_DIRECT || f->direct.red_shift != 16 || f->direct.red_mask != 0xff) continue;
        if (f->depth == 32 && f->direct.alpha_mask == 0xff && f->direct.alpha_shift == 24) fa = f->id;
        if (f->depth == 24 && !f->direct.alpha_mask) fw = f->id;
    }
    free(fr);
    xcb_render_picture_t pa = xcb_generate_id(C), pw = xcb_generate_id(C);
    xcb_render_create_picture(C, pa, a, fa, 0, NULL);
    xcb_render_create_picture(C, pw, W, fw, 0, NULL);
    xcb_render_composite(C, XCB_RENDER_PICT_OP_SRC, pa, 0, pw, 10, 10, 0, 0, 100, 0, 20, 20);
    report("render src d32->win", W, 24, 105, 5, 0xf6d190u, 125, 25, 0x010203u);
    /* depth 32 solid after a composite read it */
    fill(a, ga, 0xff777777u, 0, 0, 8, 8);
    report("d32 solid after composite", a, 32, 3, 3, 0xff777777u, 15, 15, 0xa6f6d190u);
    printf("RESULT p_depth_probe fails=%d\n", fails);
    xcb_disconnect(C);
    return fails ? 2 : 0;
}
