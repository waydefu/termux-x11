/* p_r10_ledger.c — Gate A R10 resource-ledger workload.
 *
 * DISPLAY=:3 only. Links xcb and xcb-render. No R8 test extension.
 *
 *   cc -O2 -o p_r10_ledger p_r10_ledger.c -lxcb -lxcb-render
 *
 * WHAT IT IS FOR
 * --------------
 * R10 asks whether Gate A gives back everything it takes. The client's job is to
 * take a MEASURABLE amount and then give it back explicitly, pausing at each point
 * the runner needs to sample. It never terminates X: how the session ends is the
 * runner's decision (V2-R10-DESIGN §2).
 *
 * WHY 1024x1024 AND NOT THE 64x64 R8/R9 USED
 * ------------------------------------------
 * Measured in p2-r10-probe/probe-01: a 64x64 pair is ~32 KB, while the Activity's
 * EGL mtrack swings ~35 MB and its PSS ~1.4 MB between samples with the workload
 * held constant. The old workload sits two to three orders of magnitude under the
 * noise floor of every Activity-side memory metric. Four 1024x1024 pairs is ~32 MB
 * of AHB per iteration - visible - while staying small enough that an allocation
 * failure does not turn a leak test into an OOM test.
 *
 * The format pair is not a choice: lorieCanAccelCompositePictures accepts ONLY
 * PictOpOver with an a8r8g8b8 source and an x8r8g8b8 destination, so anything else
 * silently takes the CPU fallback and measures nothing.
 *
 * SAMPLING HANDSHAKE
 * ------------------
 * A sample must be taken while the client still HOLDS its resources, which no
 * fire-and-forget client can offer. With --state-dir the fixture writes a marker
 * and blocks until the runner answers:
 *
 *   ... composites done ...     write at-A1 ; wait for go-A1
 *   ... explicit teardown ...   write at-A2 ; wait for go-A2 ; exit
 *
 * Without --state-dir it runs straight through, which is what the NOISE run and
 * smoke checks use.
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
#include <xcb/xcbext.h>
#include <xcb/xproto.h>

#include "../r8/r8-test-protocol.h"
#include "../r8/r8_xcb_request.h"

#define PAIR_W 1024
#define PAIR_H 1024
#define N_PAIRS 4
#define N_PASSES 2          /* N_PAIRS * N_PASSES composites per workload unit */
#define HANDSHAKE_TIMEOUT_S 120

static FILE *client_log;
static const char *g_mode = "?";
static const char *g_state_dir;

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
        fprintf(client_log, "{\"v\":1,\"fixture\":\"p_r10_ledger\",\"mode\":\"%s\","
                            "\"phase\":\"%s\"%s%s}\n",
                g_mode, phase, extra[0] ? "," : "", extra);
        fflush(client_log);
    }
    printf("%s %s %s\n", g_mode, phase, extra);
    fflush(stdout);
}

static void die(const char *m) {
    jlog("FAIL", "\"what\":\"%s\",\"errno\":%d", m, errno);
    exit(1);
}

/* Write at-<tag>, then block until go-<tag> appears. The runner samples in
 * between. A timeout is a FAILED ROUND, never a silent continue: continuing would
 * mean the sample was taken against a state the fixture had already left. */
static void handshake(const char *tag) {
    char at[512], go[512];
    FILE *f;
    int waited = 0;
    if (!g_state_dir)
        return;
    snprintf(at, sizeof(at), "%s/at-%s", g_state_dir, tag);
    snprintf(go, sizeof(go), "%s/go-%s", g_state_dir, tag);
    f = fopen(at, "w");
    if (!f)
        die("handshake_marker");
    fprintf(f, "%ld\n", (long)time(NULL));
    fclose(f);
    jlog("HANDSHAKE_WAIT", "\"tag\":\"%s\"", tag);
    while (access(go, F_OK) != 0) {
        struct timespec ts = { 0, 100 * 1000000L };
        nanosleep(&ts, NULL);
        if (++waited > HANDSHAKE_TIMEOUT_S * 10)
            die("handshake_timeout");
    }
    jlog("HANDSHAKE_GO", "\"tag\":\"%s\"", tag);
}

static uint32_t div255(unsigned x) { return (x + 128 + ((x + 128) >> 8)) >> 8; }

static uint32_t premul(uint8_t a, uint8_t r, uint8_t g, uint8_t b) {
    return ((uint32_t)a << 24) | (div255((unsigned)r * a) << 16) |
           (div255((unsigned)g * a) << 8) | div255((unsigned)b * a);
}

struct pair {
    xcb_pixmap_t src_pm, dst_pm;
    xcb_render_picture_t src, dst;
    xcb_gcontext_t gc32, gc24;
};

static xcb_render_pictformat_t find_fmt(xcb_render_query_pict_formats_reply_t *r,
                                        int depth, int alpha) {
    xcb_render_pictforminfo_iterator_t it =
        xcb_render_query_pict_formats_formats_iterator(r);
    for (; it.rem; xcb_render_pictforminfo_next(&it)) {
        xcb_render_pictforminfo_t *f = it.data;
        if (f->type != XCB_RENDER_PICT_TYPE_DIRECT || f->depth != depth)
            continue;
        if (alpha && f->direct.alpha_mask == 0) continue;
        if (!alpha && f->direct.alpha_mask != 0) continue;
        return f->id;
    }
    return 0;
}

/* PutImage has a request-size ceiling, so a 1024x1024 surface is filled row-band by
 * row-band. The bands are an implementation detail of getting the bytes there; the
 * pixmap the composite sees is one whole surface. */
static void fill_pixmap(xcb_connection_t *c, xcb_pixmap_t pm, xcb_gcontext_t gc,
                        uint8_t depth, uint32_t px, uint16_t w, uint16_t h) {
    enum { BAND = 64 };
    uint32_t *buf = calloc((size_t)w * BAND, sizeof(uint32_t));
    uint16_t y;
    unsigned i;
    if (!buf)
        die("calloc");
    for (i = 0; i < (unsigned)w * BAND; i++)
        buf[i] = px;
    for (y = 0; y < h; y += BAND) {
        uint16_t rows = (uint16_t)(h - y < BAND ? h - y : BAND);
        xcb_put_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, pm, gc, w, rows, 0, (int16_t)y,
                      0, depth, (uint32_t)((uint32_t)w * rows * 4),
                      (const uint8_t *)buf);
    }
    free(buf);
}

static void pair_create(xcb_connection_t *c, xcb_screen_t *s,
                        xcb_render_pictformat_t f32, xcb_render_pictformat_t f24,
                        struct pair *p) {
    p->src_pm = xcb_generate_id(c); p->dst_pm = xcb_generate_id(c);
    p->src = xcb_generate_id(c);    p->dst = xcb_generate_id(c);
    p->gc32 = xcb_generate_id(c);   p->gc24 = xcb_generate_id(c);
    xcb_create_pixmap(c, 32, p->src_pm, s->root, PAIR_W, PAIR_H);
    xcb_create_pixmap(c, 24, p->dst_pm, s->root, PAIR_W, PAIR_H);
    xcb_create_gc(c, p->gc32, p->src_pm, 0, NULL);
    xcb_create_gc(c, p->gc24, p->dst_pm, 0, NULL);
    xcb_render_create_picture(c, p->src, p->src_pm, f32, 0, NULL);
    xcb_render_create_picture(c, p->dst, p->dst_pm, f24, 0, NULL);
    fill_pixmap(c, p->src_pm, p->gc32, 32, premul(0x80, 0x00, 0x80, 0x00),
                PAIR_W, PAIR_H);
    fill_pixmap(c, p->dst_pm, p->gc24, 24, 0x00804000u, PAIR_W, PAIR_H);
    xcb_flush(c);
}

static void pair_destroy(xcb_connection_t *c, struct pair *p) {
    xcb_render_free_picture(c, p->src);
    xcb_render_free_picture(c, p->dst);
    xcb_free_gc(c, p->gc32);
    xcb_free_gc(c, p->gc24);
    xcb_free_pixmap(c, p->src_pm);
    xcb_free_pixmap(c, p->dst_pm);
}

static int pair_composite(xcb_connection_t *c, struct pair *p) {
    xcb_generic_error_t *e = xcb_request_check(c, xcb_render_composite_checked(
        c, XCB_RENDER_PICT_OP_OVER, p->src, XCB_NONE, p->dst,
        0, 0, 0, 0, 0, 0, PAIR_W, PAIR_H));
    if (xcb_connection_has_error(c)) { free(e); return 2; }
    if (e) { jlog("COMPOSITE_ERROR", "\"error_code\":%d", e->error_code); free(e); return 1; }
    return 0;
}

int main(int argc, char **argv) {
    const char *display = NULL, *mode = "workload", *logpath = NULL;
    int hold_ms = 0, i, pass, rc = 0;
    xcb_connection_t *c;
    xcb_screen_t *s;
    xcb_render_query_pict_formats_reply_t *fmts;
    xcb_render_pictformat_t f32, f24;
    struct pair pairs[N_PAIRS];

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--display") && i + 1 < argc) display = argv[++i];
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc) mode = argv[++i];
        else if (!strcmp(argv[i], "--state-dir") && i + 1 < argc) g_state_dir = argv[++i];
        else if (!strcmp(argv[i], "--client-log") && i + 1 < argc) logpath = argv[++i];
        else if (!strcmp(argv[i], "--hold-ms") && i + 1 < argc) hold_ms = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--help")) {
            printf("p_r10_ledger --display :3 --mode workload|idle|terminate "
                   "[--state-dir d] [--client-log f] [--hold-ms n]\n");
            return 0;
        }
    }
    if (strcmp(mode, "workload") && strcmp(mode, "idle")
        && strcmp(mode, "terminate")) die("unknown mode");
    g_mode = mode;
    if (logpath && !(client_log = fopen(logpath, "w"))) die("client-log");
    if (!display) die("missing --display");
    /* :1 is the Stable lane and is never touched. */
    if (strcmp(display, ":3") != 0) die("display_not_3");

    c = xcb_connect(display, NULL);
    if (xcb_connection_has_error(c)) die("connect");
    s = xcb_setup_roots_iterator(xcb_get_setup(c)).data;
    if (!s) die("no_screen");
    jlog("CONNECTED", "\"display\":\"%s\"", display);

    if (!strcmp(mode, "terminate")) {
        /* End the session the ONLY clean way the product offers: the R8
         * test-control terminate, which calls the upstream GiveUp(0) and so runs
         * CloseScreen -> gateACloseGeneration() -> UNREGISTER every buffer ->
         * GENERATION_CLOSE -> renderer unbind -> dump. It allocates nothing of its
         * own, unlike p_r8_lifecycle --cell R8-C1, whose two pairs would land in the
         * very counters R10 judges with zero tolerance. */
        uint8_t q[4] = { 0 };
        void *rep_ = NULL;
        char err[256];
        jlog("TERMINATE_SENT", NULL);
        if (r8_send_checked(c, X_LorieR8Terminate, q, sizeof(q), &rep_,
                            err, sizeof(err))) {
            if (xcb_connection_has_error(c)) {
                jlog("RESULT", "\"outcome\":\"X_HANGUP_AFTER_TERMINATE\"");
                if (client_log) fclose(client_log);
                return 0;   /* the hangup IS the terminate landing */
            }
            jlog("FAIL", "\"what\":\"terminate\",\"err\":\"%s\"", err);
            return 1;
        }
        free(rep_);
        for (;;) {
            xcb_generic_event_t *ev;
            if (xcb_connection_has_error(c)) break;
            ev = xcb_wait_for_event(c);
            if (!ev) break;
            free(ev);
        }
        jlog("RESULT", "\"outcome\":\"X_HANGUP_AFTER_TERMINATE\"");
        if (client_log) fclose(client_log);
        return 0;
    }

    if (!strcmp(mode, "idle")) {
        /* V2-R10-DESIGN §6: the noise run must cost the same wall time and the same
         * connect/disconnect as the real workload, and allocate nothing at all. */
        struct timespec ts = { hold_ms / 1000, (long)(hold_ms % 1000) * 1000000L };
        handshake("A1");
        nanosleep(&ts, NULL);
        handshake("A2");
        jlog("RESULT", "\"outcome\":\"IDLE_OK\",\"pairs\":0,\"composites\":0");
        xcb_disconnect(c);
        if (client_log) fclose(client_log);
        return 0;
    }

    fmts = xcb_render_query_pict_formats_reply(
        c, xcb_render_query_pict_formats(c), NULL);
    if (!fmts) die("pict_formats");
    f32 = find_fmt(fmts, 32, 1);
    f24 = find_fmt(fmts, 24, 0);
    free(fmts);
    if (!f32 || !f24) die("formats");

    for (i = 0; i < N_PAIRS; i++) pair_create(c, s, f32, f24, &pairs[i]);
    if (xcb_connection_has_error(c)) die("pair_create_conn_lost");
    jlog("PAIRS_CREATED", "\"pairs\":%d,\"w\":%d,\"h\":%d", N_PAIRS, PAIR_W, PAIR_H);

    for (pass = 0; pass < N_PASSES && rc == 0; pass++)
        for (i = 0; i < N_PAIRS && rc == 0; i++)
            rc = pair_composite(c, &pairs[i]);
    if (rc == 2) die("composite_conn_lost");
    if (rc == 1) die("composite_error");
    jlog("COMPOSITES_DONE", "\"count\":%d", N_PAIRS * N_PASSES);

    handshake("A1");                       /* sampled while the client still holds */

    for (i = 0; i < N_PAIRS; i++) pair_destroy(c, &pairs[i]);
    xcb_flush(c);
    /* A round-trip so the frees are known to have been processed, not just sent. */
    free(xcb_get_input_focus_reply(c, xcb_get_input_focus(c), NULL));
    if (xcb_connection_has_error(c)) die("teardown_conn_lost");
    jlog("TEARDOWN_DONE", NULL);

    handshake("A2");                       /* sampled after X has unregistered */

    jlog("RESULT", "\"outcome\":\"CLIENT_OK\",\"pairs\":%d,\"composites\":%d",
         N_PAIRS, N_PAIRS * N_PASSES);
    xcb_disconnect(c);
    if (client_log) fclose(client_log);
    return 0;
}
