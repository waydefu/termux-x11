/* p_r8_lifecycle.c — Gate A P2 R8 lifecycle fixture.
 * DISPLAY=:3 only. One cell per process. Links xcb, xcb-render, xcb-present.
 *
 *   cc -O2 -o p_r8_lifecycle p_r8_lifecycle.c -lxcb -lxcb-render -lxcb-present
 */
#include "r8-test-protocol.h"

#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/uio.h>
#include <xcb/present.h>
#include <xcb/render.h>
#include <xcb/xcb.h>
#include <xcb/xcbext.h>
#include <xcb/xproto.h>

#define PAIR_W 64
#define PAIR_H 64
#define PRESENT_W 1024
#define PRESENT_H 1024

static FILE *client_log;
static const char *g_cell;

static void flog(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    if (client_log) {
        va_list ap2;
        va_copy(ap2, ap);
        vfprintf(client_log, fmt, ap2);
        fputc('\n', client_log);
        fflush(client_log);
        va_end(ap2);
    }
    vprintf(fmt, ap);
    putchar('\n');
    va_end(ap);
}

static void die(const char *m) {
    flog("FAIL %s errno=%d", m, errno);
    exit(1);
}

static uint32_t div255(unsigned x) {
    return (x + 128 + ((x + 128) >> 8)) >> 8;
}

static uint32_t premul(uint8_t a, uint8_t r, uint8_t g, uint8_t b) {
    return ((uint32_t)a << 24) | (div255((unsigned)r * a) << 16) |
           (div255((unsigned)g * a) << 8) | div255((unsigned)b * a);
}

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

struct pair {
    xcb_pixmap_t src_pm, dst_pm;
    xcb_render_picture_t src, dst;
    xcb_gcontext_t gc32, gc24;
};

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

static int pair_composite(xcb_connection_t *c, struct pair *p, uint16_t w,
                          uint16_t h) {
    xcb_generic_error_t *e;
    e = xcb_request_check(c, xcb_render_composite_checked(
                                 c, XCB_RENDER_PICT_OP_SRC, p->src, XCB_NONE,
                                 p->dst, 0, 0, 0, 0, 0, 0, w, h));
    if (e) {
        flog("FAIL composite err=%d", e->error_code);
        free(e);
        return 1;
    }
    return 0;
}

static void pair_free(xcb_connection_t *c, struct pair *p) {
    xcb_render_free_picture(c, p->src);
    xcb_free_pixmap(c, p->src_pm);
    xcb_render_free_picture(c, p->dst);
    xcb_free_pixmap(c, p->dst_pm);
    xcb_flush(c);
}

static xcb_extension_t r8_ext = { .name = LORIE_R8_TEST_NAME, .global_id = 0 };

static const xcb_query_extension_reply_t *r8ext(xcb_connection_t *c) {
    return xcb_get_extension_data(c, &r8_ext);
}

static int r8_send(xcb_connection_t *c, uint8_t minor, void *bytes, size_t nbytes,
                   void **reply_out) {
    const xcb_query_extension_reply_t *ext = r8ext(c);
    struct iovec iov[2];
    uint64_t seq;
    xcb_generic_error_t *e = NULL;
    uint8_t *hdr = bytes;
    if (!ext || !ext->present) {
        flog("FAIL LORIE-R8-TEST missing");
        return 1;
    }
    hdr[0] = ext->major_opcode;
    hdr[1] = minor;
    iov[0].iov_base = bytes;
    iov[0].iov_len = nbytes;
    seq = xcb_send_request64(c, XCB_REQUEST_CHECKED, iov,
                             &(xcb_protocol_request_t){
                                 .count = 1, .ext = NULL, .opcode = 0, .isvoid = 0 });
    xcb_flush(c);
    *reply_out = xcb_wait_for_reply(c, (unsigned)seq, &e);
    if (e || !*reply_out) {
        flog("FAIL r8 request minor=%u", minor);
        free(e);
        free(*reply_out);
        *reply_out = NULL;
        return 1;
    }
    return 0;
}

static int r8_query(xcb_connection_t *c) {
    uint8_t q[4] = { 0, X_LorieR8QueryVersion, 1, 0 };
    void *rep = NULL;
    if (r8_send(c, X_LorieR8QueryVersion, q, 4, &rep))
        return 1;
    flog("QUERY_VERSION ok");
    free(rep);
    return 0;
}

static int r8_register(xcb_connection_t *c, uint32_t xid, int *accepted) {
    const xcb_query_extension_reply_t *ext = xcb_get_extension_data(c, &r8_ext);
    struct {
        uint8_t major, minor;
        uint16_t length;
        uint32_t xid;
    } q;
    struct iovec iov[2];
    uint64_t seq;
    xcb_generic_error_t *e = NULL;
    uint8_t *rep;
    if (!ext || !ext->present)
        return 1;
    q.major = ext->major_opcode;
    q.minor = X_LorieR8RegisterBuffer;
    q.length = 2;
    q.xid = xid;
    iov[0].iov_base = &q;
    iov[0].iov_len = 8;
    seq = xcb_send_request64(c, XCB_REQUEST_CHECKED, iov,
                             &(xcb_protocol_request_t){
                                 .count = 1, .ext = NULL, .opcode = 0, .isvoid = 0 });
    xcb_flush(c);
    rep = xcb_wait_for_reply(c, (unsigned)seq, &e);
    if (e || !rep) {
        flog("FAIL REGISTER_BUFFER xid=%u", xid);
        free(e);
        free(rep);
        return 1;
    }
    *accepted = rep[1];
    flog("REGISTER_BUFFER xid=%u accepted=%d", xid, *accepted);
    free(rep);
    return *accepted ? 0 : 1;
}

static int r8_checkpoint(xcb_connection_t *c, uint32_t phase) {
    const xcb_query_extension_reply_t *ext = xcb_get_extension_data(c, &r8_ext);
    struct {
        uint8_t major, minor;
        uint16_t length;
        uint32_t phase;
    } q;
    struct iovec iov[2];
    uint64_t seq;
    xcb_generic_error_t *e = NULL;
    void *rep;
    if (!ext || !ext->present)
        return 1;
    q.major = ext->major_opcode;
    q.minor = X_LorieR8Checkpoint;
    q.length = 2;
    q.phase = phase;
    iov[0].iov_base = &q;
    iov[0].iov_len = 8;
    seq = xcb_send_request64(c, XCB_REQUEST_CHECKED, iov,
                             &(xcb_protocol_request_t){
                                 .count = 1, .ext = NULL, .opcode = 0, .isvoid = 0 });
    xcb_flush(c);
    rep = xcb_wait_for_reply(c, (unsigned)seq, &e);
    if (e || !rep) {
        flog("FAIL CHECKPOINT phase=%u", phase);
        free(e);
        free(rep);
        return 1;
    }
    flog("CHECKPOINT phase=%u", phase);
    free(rep);
    return 0;
}

static int hold_until_hangup(xcb_connection_t *c) {
    flog("HOLD_FOR_TERM");
    for (;;) {
        xcb_generic_event_t *ev = xcb_wait_for_event(c);
        if (!ev) {
            flog("X_HANGUP_AFTER_HOLD");
            return 0;
        }
        free(ev);
        if (xcb_connection_has_error(c)) {
            flog("X_ERROR_AFTER_HOLD");
            return 0;
        }
    }
}

static int cell_c1(xcb_connection_t *c, xcb_screen_t *s,
                   xcb_render_pictformat_t fmt32, xcb_render_pictformat_t fmt24) {
    struct pair a, b;
    pair_create(c, s, fmt32, fmt24, &a, PAIR_W, PAIR_H);
    if (pair_composite(c, &a, PAIR_W, PAIR_H))
        return 1;
    pair_free(c, &a);
    pair_create(c, s, fmt32, fmt24, &b, PAIR_W, PAIR_H);
    if (pair_composite(c, &b, PAIR_W, PAIR_H))
        return 1;
    pair_free(c, &b);
    flog("RESULT p_r8_lifecycle C1 CLIENT_OK");
    return 0;
}

static int cell_c2(xcb_connection_t *c, xcb_screen_t *s,
                   xcb_render_pictformat_t fmt32, xcb_render_pictformat_t fmt24) {
    struct pair a;
    pair_create(c, s, fmt32, fmt24, &a, PAIR_W, PAIR_H);
    if (pair_composite(c, &a, PAIR_W, PAIR_H))
        return 1;
    flog("RESULT p_r8_lifecycle C2 CLIENT_HOLD");
    return hold_until_hangup(c);
}

static int present_copy(xcb_connection_t *c, xcb_screen_t *s, uint32_t serial,
                        int disconnect, int destroy_win) {
    xcb_window_t win = xcb_generate_id(c);
    xcb_pixmap_t pm = xcb_generate_id(c);
    xcb_gcontext_t gc = xcb_generate_id(c);
    uint32_t mask = XCB_CW_EVENT_MASK;
    uint32_t val = XCB_EVENT_MASK_NO_EVENT;
    uint32_t *buf;
    unsigned i;
    xcb_void_cookie_t ck;
    xcb_generic_error_t *e;

    xcb_create_window(c, 24, win, s->root, 0, 0, PRESENT_W, PRESENT_H, 0,
                      XCB_WINDOW_CLASS_INPUT_OUTPUT, s->root_visual, mask, &val);
    xcb_map_window(c, win);
    xcb_create_pixmap(c, 24, pm, win, PRESENT_W, PRESENT_H);
    xcb_create_gc(c, gc, pm, 0, NULL);
    buf = calloc((size_t)PRESENT_W * PRESENT_H, sizeof(uint32_t));
    if (!buf)
        return 1;
    for (i = 0; i < (unsigned)PRESENT_W * PRESENT_H; i++)
        buf[i] = 0x00804000u;
    xcb_put_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, pm, gc, PRESENT_W, PRESENT_H, 0,
                  0, 0, 24, PRESENT_W * PRESENT_H * 4, (const uint8_t *)buf);
    free(buf);
    ck = xcb_present_pixmap_checked(
        c, win, pm, serial, XCB_NONE, XCB_NONE, 0, 0, XCB_NONE, XCB_NONE,
        XCB_NONE, XCB_PRESENT_OPTION_ASYNC | XCB_PRESENT_OPTION_COPY, 0, 0, 0,
        0, NULL);
    xcb_flush(c);
    e = xcb_request_check(c, ck);
    if (e) {
        flog("FAIL PresentPixmap err=%d", e->error_code);
        free(e);
        return 1;
    }
    if (destroy_win) {
        xcb_destroy_window(c, win);
        xcb_flush(c);
    }
    if (disconnect)
        return 0;
    return 0;
}

static int cell_c3_window(xcb_connection_t *c, xcb_screen_t *s,
                          xcb_render_pictformat_t fmt32,
                          xcb_render_pictformat_t fmt24) {
    struct pair b;
    if (present_copy(c, s, 1, 0, 1))
        return 1;
    pair_create(c, s, fmt32, fmt24, &b, PAIR_W, PAIR_H);
    if (pair_composite(c, &b, PAIR_W, PAIR_H))
        return 1;
    pair_free(c, &b);
    flog("RESULT p_r8_lifecycle C3-window CLIENT_OK");
    return 0;
}

static int cell_c4(xcb_connection_t *c, xcb_screen_t *s,
                   xcb_render_pictformat_t fmt32, xcb_render_pictformat_t fmt24) {
    struct pair a;
    int acc;
    pair_create(c, s, fmt32, fmt24, &a, PAIR_W, PAIR_H);
    if (r8_query(c))
        return 1;
    if (r8_checkpoint(c, LORIE_R8_PHASE_BEGIN))
        return 1;
    if (r8_register(c, a.src_pm, &acc) || r8_register(c, a.dst_pm, &acc))
        return 1;
    if (r8_checkpoint(c, LORIE_R8_PHASE_PRE_FREE))
        return 1;
    pair_free(c, &a);
    if (r8_checkpoint(c, LORIE_R8_PHASE_POST_FREE))
        return 1;
    flog("RESULT p_r8_lifecycle C4 CLIENT_OK");
    return 0;
}

static int cell_c5_full(xcb_connection_t *c, xcb_screen_t *s,
                        xcb_render_pictformat_t fmt32, xcb_render_pictformat_t fmt24) {
    xcb_pixmap_t pms[16];
    xcb_gcontext_t gc = xcb_generate_id(c);
    int i, acc, n = 0;
    uint32_t px = 0x00804000u;
    (void)fmt32;
    (void)fmt24;
    if (r8_query(c))
        return 1;
    xcb_create_gc(c, gc, s->root, 0, NULL);
    for (i = 0; i < 16; i++) {
        pms[i] = xcb_generate_id(c);
        xcb_create_pixmap(c, 24, pms[i], s->root, PAIR_W, PAIR_H);
        xcb_put_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, pms[i], gc, PAIR_W, PAIR_H,
                      0, 0, 0, 24, PAIR_W * PAIR_H * 4, (const uint8_t *)&px);
        if (r8_register(c, pms[i], &acc)) {
            flog("C5 register stop at %d", i);
            break;
        }
        n++;
    }
    flog("C5_FULL registered=%d", n);
    if (r8_checkpoint(c, LORIE_R8_PHASE_PRE_TERM))
        return 1;
    flog("RESULT p_r8_lifecycle C5-full CLIENT_HOLD");
    return hold_until_hangup(c);
}

static int cell_c5_overflow(xcb_connection_t *c, xcb_screen_t *s,
                            xcb_render_pictformat_t fmt32,
                            xcb_render_pictformat_t fmt24) {
    xcb_pixmap_t pms[16];
    struct pair rec;
    int i, acc, n = 0;
    uint32_t px = 0x00804000u;
    xcb_gcontext_t gc = xcb_generate_id(c);
    if (r8_query(c))
        return 1;
    xcb_create_gc(c, gc, s->root, 0, NULL);
    pms[0] = xcb_generate_id(c);
    xcb_create_pixmap(c, 32, pms[0], s->root, PAIR_W, PAIR_H);
    if (r8_register(c, pms[0], &acc))
        return 1;
    n = 1;
    for (i = 1; i < 16; i++) {
        pms[i] = xcb_generate_id(c);
        xcb_create_pixmap(c, 24, pms[i], s->root, PAIR_W, PAIR_H);
        xcb_put_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, pms[i], gc, PAIR_W, PAIR_H,
                      0, 0, 0, 24, 4, (const uint8_t *)&px);
        if (r8_register(c, pms[i], &acc))
            break;
        n++;
    }
    flog("C5_OVERFLOW filled=%d", n);
    pair_create(c, s, fmt32, fmt24, &rec, PAIR_W, PAIR_H);
    /* New dest while full: composite should fallback, not lease. */
    (void)pair_composite(c, &rec, PAIR_W, PAIR_H);
    xcb_free_pixmap(c, pms[n > 1 ? n - 1 : 0]);
    xcb_flush(c);
    if (pair_composite(c, &rec, PAIR_W, PAIR_H))
        return 1;
    pair_free(c, &rec);
    flog("RESULT p_r8_lifecycle C5-overflow CLIENT_OK");
    return 0;
}

static int cell_d(xcb_connection_t *c, xcb_screen_t *s,
                  xcb_render_pictformat_t fmt32, xcb_render_pictformat_t fmt24) {
    xcb_connection_t *pc;
    struct pair g;
    int i;
    xcb_window_t win;
    xcb_pixmap_t pms[8];
    xcb_gcontext_t gc;
    uint32_t mask = XCB_CW_EVENT_MASK, val = 0;
    pc = xcb_connect(getenv("DISPLAY"), NULL);
    if (xcb_connection_has_error(pc))
        return 1;
    pair_create(c, s, fmt32, fmt24, &g, PAIR_W, PAIR_H);
    if (pair_composite(c, &g, PAIR_W, PAIR_H))
        return 1;
    win = xcb_generate_id(pc);
    xcb_create_window(pc, 24, win, s->root, 0, 0, PRESENT_W, PRESENT_H, 0,
                      XCB_WINDOW_CLASS_INPUT_OUTPUT, s->root_visual, mask, &val);
    xcb_map_window(pc, win);
    gc = xcb_generate_id(pc);
    xcb_create_gc(pc, gc, win, 0, NULL);
    for (i = 0; i < 8; i++) {
        pms[i] = xcb_generate_id(pc);
        xcb_create_pixmap(pc, 24, pms[i], win, PRESENT_W, PRESENT_H);
        xcb_present_pixmap(pc, win, pms[i], (uint32_t)(i + 1), XCB_NONE,
                           XCB_NONE, 0, 0, XCB_NONE, XCB_NONE, XCB_NONE,
                           XCB_PRESENT_OPTION_ASYNC | XCB_PRESENT_OPTION_COPY,
                           0, 0, 0, 0, NULL);
        if (i == 3) {
            xcb_flush(pc);
            xcb_render_free_picture(c, g.src);
            xcb_free_pixmap(c, g.src_pm);
            xcb_flush(c);
        }
    }
    xcb_flush(pc);
    xcb_render_free_picture(c, g.dst);
    xcb_free_pixmap(c, g.dst_pm);
    xcb_flush(c);
    for (i = 0; i < 8; i++) {
        xcb_generic_event_t *ev = xcb_wait_for_event(pc);
        free(ev);
    }
    xcb_disconnect(pc);
    flog("RESULT p_r8_lifecycle D CLIENT_OK");
    return 0;
}

static int cell_p(xcb_connection_t *c, xcb_screen_t *s,
                  xcb_render_pictformat_t fmt32, xcb_render_pictformat_t fmt24) {
    struct pair a;
    pair_create(c, s, fmt32, fmt24, &a, PAIR_W, PAIR_H);
    (void)pair_composite(c, &a, PAIR_W, PAIR_H);
    flog("RESULT p_r8_lifecycle P CLIENT_DONE");
    return 0;
}

int main(int argc, char **argv) {
    const char *display = getenv("DISPLAY");
    const char *cell = NULL;
    const char *logpath = NULL;
    xcb_connection_t *c;
    xcb_screen_t *s;
    xcb_render_query_pict_formats_reply_t *fmts;
    xcb_render_pictformat_t fmt32, fmt24;
    int i, rc = 1;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--display") && i + 1 < argc)
            display = argv[++i];
        else if (!strcmp(argv[i], "--cell") && i + 1 < argc)
            cell = argv[++i];
        else if (!strcmp(argv[i], "--client-log") && i + 1 < argc)
            logpath = argv[++i];
        else if (!strcmp(argv[i], "--spec") && i + 1 < argc)
            i++;
        else if (!strcmp(argv[i], "--help")) {
            printf("p_r8_lifecycle --display :3 --cell R8-C1 [--client-log f]\n");
            return 0;
        }
    }
    if (!cell)
        die("missing --cell");
    g_cell = cell;
    if (logpath) {
        client_log = fopen(logpath, "w");
        if (!client_log)
            die("client-log");
    }
    if (!display || strcmp(display, ":3") != 0) {
        /* allow override only if DISPLAY already :3 */
        if (!display)
            die("DISPLAY");
    }
    c = xcb_connect(display, NULL);
    if (xcb_connection_has_error(c))
        die("connect");
    s = xcb_setup_roots_iterator(xcb_get_setup(c)).data;
    fmts = xcb_render_query_pict_formats_reply(
        c, xcb_render_query_pict_formats(c), NULL);
    if (!fmts)
        die("pict_formats");
    fmt32 = find_fmt(fmts, 32, 1);
    fmt24 = find_fmt(fmts, 24, 0);
    free(fmts);
    if (!fmt32 || !fmt24)
        die("formats");
    if (!strcmp(cell, "R8-C1"))
        rc = cell_c1(c, s, fmt32, fmt24);
    else if (!strcmp(cell, "R8-C2"))
        rc = cell_c2(c, s, fmt32, fmt24);
    else if (!strcmp(cell, "R8-C3-window"))
        rc = cell_c3_window(c, s, fmt32, fmt24);
    else if (!strcmp(cell, "R8-C3-disconnect")) {
        xcb_connection_t *pc = xcb_connect(display, NULL);
        if (present_copy(pc, s, 1, 1, 0))
            rc = 1;
        else {
            xcb_disconnect(pc);
            rc = cell_c1(c, s, fmt32, fmt24);
            if (!rc)
                flog("RESULT p_r8_lifecycle C3-disconnect CLIENT_OK");
        }
    } else if (!strcmp(cell, "R8-C4"))
        rc = cell_c4(c, s, fmt32, fmt24);
    else if (!strcmp(cell, "R8-C5-full"))
        rc = cell_c5_full(c, s, fmt32, fmt24);
    else if (!strcmp(cell, "R8-C5-overflow"))
        rc = cell_c5_overflow(c, s, fmt32, fmt24);
    else if (!strcmp(cell, "R8-D"))
        rc = cell_d(c, s, fmt32, fmt24);
    else if (!strcmp(cell, "R8-P1") || !strcmp(cell, "R8-P2"))
        rc = cell_p(c, s, fmt32, fmt24);
    else
        die("unknown cell");
    xcb_disconnect(c);
    if (client_log)
        fclose(client_log);
    return rc;
}
