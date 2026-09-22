/* p_r9_boundary.c — Gate A P2 R9 lifecycle fixture.
 *
 * DISPLAY=:3 only. One cell per process. Links xcb and xcb-render.
 *
 *   cc -O2 -o p_r9_boundary p_r9_boundary.c -lxcb -lxcb-render
 *
 * WHY THIS EXISTS INSTEAD OF p_r8_lifecycle
 * -----------------------------------------
 * R9's cells arm a Gate A test fault. parseArm() in lorie_r8_obs.c hard-codes the
 * only two legal (R8 case, fault) pairs — R8-P1/destroy-while-gpu-owned and
 * R8-P2/close-while-lease — and sets r8EnvFatal for any other case combined with
 * ANY fault, which halts X at startup with x-r8-env. R9's faults are neither pair,
 * so R9 must run with TERMUX_X11_R8_ARM UNSET. But LorieR8TestExtensionInit()
 * returns early when lorieR8ValidateStartupEnv() != 1, so with R8 disarmed the
 * LORIE-R8-TEST extension is never registered and p_r8_lifecycle aborts on its
 * first r8_query. Measured both ways: r9-cold-2/attempt-01 (x-r8-env halt) and
 * attempt-02 (LORIE-R8-TEST missing). Two attempts, no cell.
 *
 * This fixture therefore uses NO extension at all. It is ordinary XCB: create a
 * 32-bit source and a 24-bit destination pixmap, wrap them in RENDER pictures and
 * issue one PictOpOver composite. That is the whole Gate A direct path trigger —
 * lorieCanAccelCompositePictures() accepts only PictOpOver with a8r8g8b8 source
 * and x8r8g8b8 destination — and it drives REGISTER, the lease, the publish and
 * the fence without the fixture naming any of them.
 *
 * The renderer-side observation stream is unaffected by disarming R8: the armed
 * guard in lorieR8ObsTuple is `role[0] == 'x'` only, so D-02's epoch records still
 * flow. The X-side R8_OBS stream is absent, and judge-r9.py reads no x rows for
 * either cell.
 */
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <xcb/render.h>
#include <xcb/xcb.h>
#include <xcb/xproto.h>

#define PAIR_W 64
#define PAIR_H 64

static FILE *client_log;
static const char *g_cell = "?";

/* One JSON object per line on the client log, plus a human line on stdout. */
static void jlog(const char *phase, const char *fmt, ...) {
    va_list ap;
    char extra[256];
    extra[0] = '\0';
    if (fmt) {
        va_start(ap, fmt);
        vsnprintf(extra, sizeof(extra), fmt, ap);
        va_end(ap);
    }
    if (client_log) {
        fprintf(client_log, "{\"v\":1,\"fixture\":\"p_r9_boundary\",\"cell\":\"%s\","
                            "\"phase\":\"%s\"%s%s}\n",
                g_cell, phase, extra[0] ? "," : "", extra);
        fflush(client_log);
    }
    printf("%s %s %s\n", g_cell, phase, extra);
    fflush(stdout);
}

static void die(const char *m) {
    jlog("FAIL", "\"what\":\"%s\",\"errno\":%d", m, errno);
    exit(1);
}

static uint32_t div255(unsigned x) {
    return (x + 128 + ((x + 128) >> 8)) >> 8;
}

static uint32_t premul(uint8_t a, uint8_t r, uint8_t g, uint8_t b) {
    return ((uint32_t)a << 24) | (div255((unsigned)r * a) << 16) |
           (div255((unsigned)g * a) << 8) | div255((unsigned)b * a);
}

struct pair {
    xcb_pixmap_t src_pm, dst_pm;
    xcb_render_picture_t src, dst;
    xcb_gcontext_t gc32, gc24;
};

/* Copied verbatim from tests/r8/p_r8_lifecycle.c:62 — the same selection that
 * produced event=5 (LEASE_GPU_OWNED) in every R8 direct cell. */
static xcb_render_pictformat_t find_fmt(xcb_render_query_pict_formats_reply_t *r,
                                        int depth, int alpha) {
    xcb_render_pictforminfo_iterator_t it =
        xcb_render_query_pict_formats_formats_iterator(r);
    for (; it.rem; xcb_render_pictforminfo_next(&it)) {
        xcb_render_pictforminfo_t *f = it.data;
        if (f->type != XCB_RENDER_PICT_TYPE_DIRECT)
            continue;
        if (f->depth != depth)
            continue;
        if (alpha && f->direct.alpha_mask == 0)
            continue;
        if (!alpha && f->direct.alpha_mask != 0)
            continue;
        return f->id;
    }
    return 0;
}

static void pair_create(xcb_connection_t *c, xcb_screen_t *s,
                        xcb_render_pictformat_t fmt32,
                        xcb_render_pictformat_t fmt24, struct pair *p,
                        uint16_t w, uint16_t h) {
    uint32_t *buf;
    uint32_t src_px = premul(0x80, 0x00, 0x80, 0x00);
    uint32_t dst_px = 0x00804000u;
    unsigned i;

    p->src_pm = xcb_generate_id(c);
    p->dst_pm = xcb_generate_id(c);
    p->src = xcb_generate_id(c);
    p->dst = xcb_generate_id(c);
    p->gc32 = xcb_generate_id(c);
    p->gc24 = xcb_generate_id(c);
    xcb_create_pixmap(c, 32, p->src_pm, s->root, w, h);
    xcb_create_pixmap(c, 24, p->dst_pm, s->root, w, h);
    xcb_create_gc(c, p->gc32, p->src_pm, 0, NULL);
    xcb_create_gc(c, p->gc24, p->dst_pm, 0, NULL);
    xcb_render_create_picture(c, p->src, p->src_pm, fmt32, 0, NULL);
    xcb_render_create_picture(c, p->dst, p->dst_pm, fmt24, 0, NULL);
    buf = calloc((size_t)w * h, sizeof(uint32_t));
    if (!buf)
        die("calloc");
    for (i = 0; i < (unsigned)w * h; i++)
        buf[i] = src_px;
    xcb_put_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, p->src_pm, p->gc32, w, h, 0, 0,
                  0, 32, (uint32_t)(w * h * 4), (const uint8_t *)buf);
    for (i = 0; i < (unsigned)w * h; i++)
        buf[i] = dst_px;
    xcb_put_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, p->dst_pm, p->gc24, w, h, 0, 0,
                  0, 24, (uint32_t)(w * h * 4), (const uint8_t *)buf);
    free(buf);
    xcb_flush(c);
}

/* Returns 0 = composite completed, 1 = X replied with an error,
 * 2 = the connection broke (X halted or died). The caller decides what that
 * means for the cell; the fixture never judges. */
static int pair_composite(xcb_connection_t *c, struct pair *p, uint16_t w,
                          uint16_t h) {
    xcb_generic_error_t *e;
    xcb_void_cookie_t ck;

    ck = xcb_render_composite_checked(c, XCB_RENDER_PICT_OP_OVER, p->src,
                                      XCB_NONE, p->dst, 0, 0, 0, 0, 0, 0, w, h);
    jlog("COMPOSITE_SENT", "\"op\":\"PictOpOver\",\"w\":%u,\"h\":%u",
         (unsigned)w, (unsigned)h);
    e = xcb_request_check(c, ck);
    if (xcb_connection_has_error(c)) {
        free(e);
        return 2;
    }
    if (e) {
        jlog("COMPOSITE_ERROR", "\"error_code\":%d", e->error_code);
        free(e);
        return 1;
    }
    return 0;
}

static void settle(xcb_connection_t *c, int ms) {
    struct timespec ts = { ms / 1000, (long)(ms % 1000) * 1000000L };
    xcb_flush(c);
    nanosleep(&ts, NULL);
}

int main(int argc, char **argv) {
    const char *display = NULL, *cell = NULL, *logpath = NULL;
    xcb_connection_t *c;
    xcb_screen_t *s;
    xcb_render_query_pict_formats_reply_t *fmts;
    xcb_render_pictformat_t fmt32, fmt24;
    struct pair p;
    int i, rc;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--display") && i + 1 < argc)
            display = argv[++i];
        else if (!strcmp(argv[i], "--cell") && i + 1 < argc)
            cell = argv[++i];
        else if (!strcmp(argv[i], "--client-log") && i + 1 < argc)
            logpath = argv[++i];
        else if (!strcmp(argv[i], "--help")) {
            printf("p_r9_boundary --display :3 --cell R9-F1|R9-F2 "
                   "[--client-log f]\n");
            return 0;
        }
    }
    if (!cell)
        die("missing --cell");
    if (strcmp(cell, "R9-F1") && strcmp(cell, "R9-F2"))
        die("unknown cell");
    g_cell = cell;
    if (logpath) {
        client_log = fopen(logpath, "w");
        if (!client_log)
            die("client-log");
    }
    if (!display)
        die("missing --display");
    if (strcmp(display, ":3") != 0)
        die("display_not_3"); /* :1 is the Stable lane and is never touched */

    c = xcb_connect(display, NULL);
    if (xcb_connection_has_error(c))
        die("connect");
    jlog("CONNECTED", "\"display\":\"%s\"", display);

    s = xcb_setup_roots_iterator(xcb_get_setup(c)).data;
    if (!s)
        die("no_screen");
    fmts = xcb_render_query_pict_formats_reply(
        c, xcb_render_query_pict_formats(c), NULL);
    if (!fmts)
        die("pict_formats");
    fmt32 = find_fmt(fmts, 32, 1);
    fmt24 = find_fmt(fmts, 24, 0);
    free(fmts);
    if (!fmt32 || !fmt24)
        die("formats");
    jlog("FORMATS", "\"fmt32\":%u,\"fmt24\":%u", (unsigned)fmt32,
         (unsigned)fmt24);

    pair_create(c, s, fmt32, fmt24, &p, PAIR_W, PAIR_H);
    if (xcb_connection_has_error(c))
        die("pair_create_conn_lost");
    jlog("PAIR_CREATED", NULL);

    rc = pair_composite(c, &p, PAIR_W, PAIR_H);

    if (!strcmp(cell, "R9-F1")) {
        /* The armed stale-ready replay makes X halt with x-wrong-generation
         * while it is waiting for READY, so the composite request normally
         * never gets a reply and the connection breaks. Both outcomes are
         * reported, neither is judged here: judge-r9.py requires the EXACT
         * fatal from logcat, and §8.8 forbids concluding PASS from the absence
         * of harm. What the fixture must establish is only that the composite
         * WAS issued, so a missing fatal is INVALID_CONSTRUCTION rather than an
         * unexplained silence. */
        if (rc == 2) {
            jlog("RESULT", "\"outcome\":\"SERVER_CONNECTION_BROKE\","
                           "\"composite_issued\":true");
        } else if (rc == 1) {
            jlog("RESULT", "\"outcome\":\"COMPOSITE_ERROR\","
                           "\"composite_issued\":true");
        } else {
            settle(c, 1500);
            jlog("RESULT", "\"outcome\":\"COMPOSITE_COMPLETED\","
                           "\"composite_issued\":true,"
                           "\"note\":\"no halt observed client-side\"");
        }
        /* Exit 0 in every case: the client reaching the end is not a verdict. */
        if (!xcb_connection_has_error(c))
            xcb_disconnect(c);
        if (client_log)
            fclose(client_log);
        return 0;
    }

    /* R9-F2: a clean session must complete the composite. Anything else is a
     * real client-side failure and the runner should see it. */
    if (rc == 2)
        die("f2_connection_broke");
    if (rc == 1)
        die("f2_composite_error");
    settle(c, 500);
    if (xcb_connection_has_error(c))
        die("f2_conn_lost_after_composite");
    jlog("RESULT", "\"outcome\":\"CLIENT_OK\",\"composite_issued\":true");
    xcb_disconnect(c);
    if (client_log)
        fclose(client_log);
    return 0;
}
