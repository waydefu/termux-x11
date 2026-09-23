/* p_v1_oracle.c - V1 acceptance #6 / #9 pixel oracle for the Gate A DIRECT path.
 *
 * Derived from patches/p_b2_oracle.c (P2-B.2 R3, corrected per-pixmap GC). Three phases,
 * each bracketed by a wall-clock marker line so the judge can attribute GATEA_EVENT
 * event=5 (LEASE_GPU_OWNED) to a phase from logcat:
 *
 *   PHASE persistent  one src/dst pair PER SIZE, reused for every case of that size:
 *                     PutImage src + dst -> Over -> GetImage. After the pair's first
 *                     registration every case is eligible for the direct path, so the
 *                     judge can require event=5 == cases. (p_b2_oracle created a fresh
 *                     pair per case; whether each one went direct was invisible.)
 *   PHASE fresh       the original fresh-pair-per-case loop (registration / unregister
 *                     churn under correctness), direct count reported, not required.
 *   PHASE negative    ops OUTSIDE the Over a8r8g8b8->x8r8g8b8 slice; each must complete,
 *                     produce the exact software result, and take no direct lease.
 *
 * ORACLE_FROZEN_V2 (after oracle-01, which V1 judged FAIL_CORRECTNESS; both causes were
 * construction, see evidence p2-oracle-runtime/ORACLE-01-TRIAGE.md):
 *   - phases (and every negative case) are separated by a QUIET_MS idle gap, so an event's
 *     window is unambiguous (V1's phases were 17 us apart and the judge widened +-5 ms);
 *   - every negative case has its own MARK neg-<kind> BEGIN|END, so a direct lease is named;
 *   - "repeat" composites 8x8 from the 4x4 source: repeat must take effect. In V1 the 4x4
 *     composite of a 4x4 source was a USELESS repeat, which the EXA core strips before the
 *     driver sees the op (exaComposite, exa_render.c:887-891) - an in-slice op, legally direct.
 *
 * Exact RGB is required everywhere; nothing is pre-declared as tolerable.
 *   cc -O2 -Wall -o /tmp/p_v1_oracle p_v1_oracle.c -lxcb -lxcb-render
 *   DISPLAY=:3 /tmp/p_v1_oracle
 * Output lines: MARK <phase> BEGIN|END <epoch_s>, FAIL ..., PHASE_RESULT ..., RESULT ...
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <xcb/render.h>
#include <xcb/xcb.h>
#include <xcb/xproto.h>

static double wall(void) {
    struct timespec t;
    clock_gettime(CLOCK_REALTIME, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static xcb_render_pictformat_t find_fmt(xcb_render_query_pict_formats_reply_t *r, int depth, int alpha) {
    xcb_render_pictforminfo_iterator_t it = xcb_render_query_pict_formats_formats_iterator(r);
    for (; it.rem; xcb_render_pictforminfo_next(&it)) {
        xcb_render_pictforminfo_t *f = it.data;
        if (f->type != XCB_RENDER_PICT_TYPE_DIRECT || f->depth != depth)
            continue;
        if (alpha && f->direct.alpha_mask == 0)
            continue;
        if (!alpha && f->direct.alpha_mask != 0)
            continue;
        return f->id;
    }
    return 0;
}

static int sync_ok(xcb_connection_t *c) {
    xcb_generic_error_t *e = NULL;
    xcb_get_input_focus_reply_t *r = xcb_get_input_focus_reply(c, xcb_get_input_focus(c), &e);
    int ok = r && !e && !xcb_connection_has_error(c);
    free(e);
    free(r);
    return ok;
}

static uint32_t div255(unsigned x) { return (x + 128 + ((x + 128) >> 8)) >> 8; }

/* premultiplied Over onto an X (no-alpha) destination; X byte of the result is 0 */
static uint32_t ref_over_x8(uint32_t s, uint32_t d) {
    unsigned sa = (s >> 24) & 255, ia = 255 - sa;
    unsigned r = ((s >> 16) & 255) + div255(((d >> 16) & 255) * ia);
    unsigned g = ((s >> 8) & 255) + div255(((d >> 8) & 255) * ia);
    unsigned b = (s & 255) + div255((d & 255) * ia);
    if (r > 255) r = 255;
    if (g > 255) g = 255;
    if (b > 255) b = 255;
    return (r << 16) | (g << 8) | b;
}

static uint32_t premul(uint8_t a, uint8_t r, uint8_t g, uint8_t b) {
    return ((uint32_t) a << 24) | (div255((unsigned) r * a) << 16) |
           (div255((unsigned) g * a) << 8) | div255((unsigned) b * a);
}

static void put(xcb_connection_t *c, xcb_drawable_t d, xcb_gcontext_t gc, uint8_t depth,
                uint16_t w, uint16_t h, const uint32_t *px) {
    xcb_put_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, d, gc, w, h, 0, 0, 0, depth,
                  (uint32_t) (w * h * 4), (const uint8_t *) px);
}

static int get_px(xcb_connection_t *c, xcb_drawable_t d, uint16_t w, uint16_t h, uint32_t *out) {
    xcb_generic_error_t *e = NULL;
    xcb_get_image_reply_t *r = xcb_get_image_reply(
        c, xcb_get_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, d, 0, 0, w, h, ~0u), &e);
    if (e || !r) {
        free(e);
        free(r);
        return 0;
    }
    uint8_t *data = xcb_get_image_data(r);
    int len = xcb_get_image_data_length(r), stride = h ? len / h : 0;
    for (int i = 0; i < w * h; i++) {
        uint8_t *p = data + (i / w) * stride + (i % w) * 4;
        out[i] = ((uint32_t) p[3] << 24) | ((uint32_t) p[2] << 16) | ((uint32_t) p[1] << 8) | p[0];
    }
    free(r);
    return 1;
}

struct Stats { int cases, fail, max_d, exact_px, xnz_px; };

struct Pair {
    uint16_t sw, sh, dw, dh;
    xcb_pixmap_t spm, dpm;
    xcb_gcontext_t sgc, dgc;
    xcb_render_picture_t sp, dp;
};

static void pair_make(xcb_connection_t *c, xcb_screen_t *s, xcb_render_pictformat_t f32,
                      xcb_render_pictformat_t f24, struct Pair *p) {
    p->spm = xcb_generate_id(c); p->dpm = xcb_generate_id(c);
    p->sgc = xcb_generate_id(c); p->dgc = xcb_generate_id(c);
    p->sp = xcb_generate_id(c); p->dp = xcb_generate_id(c);
    xcb_create_pixmap(c, 32, p->spm, s->root, p->sw, p->sh);
    xcb_create_pixmap(c, 24, p->dpm, s->root, p->dw, p->dh);
    xcb_create_gc(c, p->sgc, p->spm, 0, NULL);      /* per-pixmap GC: the R3 correction */
    xcb_create_gc(c, p->dgc, p->dpm, 0, NULL);
    xcb_render_create_picture(c, p->sp, p->spm, f32, 0, NULL);
    xcb_render_create_picture(c, p->dp, p->dpm, f24, 0, NULL);
    xcb_render_set_picture_filter(c, p->sp, 7, "nearest", 0, NULL);
}

static void pair_free(xcb_connection_t *c, struct Pair *p) {
    xcb_render_free_picture(c, p->sp);
    xcb_render_free_picture(c, p->dp);
    xcb_free_gc(c, p->sgc);
    xcb_free_gc(c, p->dgc);
    xcb_free_pixmap(c, p->spm);
    xcb_free_pixmap(c, p->dpm);
}

/* one Over case on an existing pair; region (dx,dy,cw,ch) from source (sx,sy) */
static int over_case(xcb_connection_t *c, struct Pair *p, int16_t sx, int16_t sy, int16_t dx,
                     int16_t dy, uint16_t cw, uint16_t ch, uint32_t spx, uint32_t dpx,
                     struct Stats *st, const char *name) {
    size_t ns = (size_t) p->sw * p->sh, nd = (size_t) p->dw * p->dh;
    uint32_t *sb = malloc(ns * 4), *db = malloc(nd * 4), *got = malloc(nd * 4);
    int fail = 0, maxd = 0;
    for (size_t i = 0; i < ns; i++) sb[i] = spx;
    for (size_t i = 0; i < nd; i++) db[i] = dpx & 0x00ffffffu;
    put(c, p->spm, p->sgc, 32, p->sw, p->sh, sb);
    put(c, p->dpm, p->dgc, 24, p->dw, p->dh, db);
    xcb_generic_error_t *e = xcb_request_check(c, xcb_render_composite_checked(
        c, XCB_RENDER_PICT_OP_OVER, p->sp, XCB_NONE, p->dp, sx, sy, 0, 0, dx, dy, cw, ch));
    if (e) {
        printf("FAIL %s composite_err=%d\n", name, e->error_code);
        free(e);
        fail = 1;
    } else if (!get_px(c, p->dpm, p->dw, p->dh, got)) {
        printf("FAIL %s getimage\n", name);
        fail = 1;
    } else {
        for (size_t i = 0; i < nd; i++) {
            int x = (int) (i % p->dw), y = (int) (i / p->dw);
            int in = x >= dx && y >= dy && x < dx + cw && y < dy + ch;
            uint32_t exp = in ? ref_over_x8(spx, dpx) : (dpx & 0x00ffffffu), g = got[i];
            int d = 0;
            for (int k = 0; k < 24; k += 8) {
                int a = abs((int) ((g >> k) & 255) - (int) ((exp >> k) & 255));
                if (a > d) d = a;
            }
            if (d > maxd) maxd = d;
            if (d == 0) st->exact_px++;
            if (g >> 24) st->xnz_px++;
            if ((g & 0x00ffffffu) != exp) fail = 1;
        }
        if (fail)
            printf("FAIL %s src=%08x dst=%08x got0=%08x maxd=%d\n", name, spx, dpx, got[0], maxd);
    }
    st->cases++;
    st->fail += fail;
    if (maxd > st->max_d) st->max_d = maxd;
    free(sb); free(db); free(got);
    return fail;
}

static const uint8_t ALPHAS[] = { 0x00, 0x01, 0x7f, 0x80, 0xfe, 0xff };
static const uint32_t DSTS[] = { 0x00000000, 0x00ffffff, 0x00ff0000, 0x0000ff00, 0x000000ff, 0x00112233 };
static const struct { uint8_t r, g, b; } SRCS[] = {
    {0, 0, 0}, {255, 255, 255}, {255, 0, 0}, {0, 255, 0}, {0, 0, 255}, {40, 80, 160} };
static const struct { uint16_t w, h; } SIZES[] = {
    {1, 1}, {2, 2}, {5, 24}, {17, 17}, {32, 32}, {64, 64} };
#define N(a) (sizeof(a) / sizeof((a)[0]))

static void phase_result(const char *ph, struct Stats *st) {
    printf("PHASE_RESULT %s cases=%d fail=%d maxd=%d exact_px=%d xnz_px=%d\n",
           ph, st->cases, st->fail, st->max_d, st->exact_px, st->xnz_px);
}

#define QUIET_MS 100
static void quiet(void) { usleep(QUIET_MS * 1000); }

/* --------------------------------------------------------------- negative --- */
/* Every negative uses a UNIFORM 4x4 source, so the correct software result has a
 * closed form even for bilinear / repeat / transform: it is the plain per-pixel op. */
static int neg_case(xcb_connection_t *c, xcb_screen_t *s, xcb_render_pictformat_t f32,
                    xcb_render_pictformat_t f24, xcb_render_pictformat_t fa8, const char *kind,
                    struct Stats *st) {
    const uint16_t sw = 4, sh = 4;
    /* repeat: the composite (and dst) is 8x8, twice the source, so the repeat is used */
    const uint16_t w = strcmp(kind, "repeat") ? 4 : 8, h = w;
    uint32_t spx = premul(0x80, 255, 0, 0), dpx = 0x000000ff, sb[16], db[64], got[64], exp;
    uint8_t op = XCB_RENDER_PICT_OP_OVER, ddepth = 24;
    xcb_render_pictformat_t dfmt = f24;
    xcb_pixmap_t spm = xcb_generate_id(c), dpm = xcb_generate_id(c), mpm = 0;
    xcb_gcontext_t sgc = xcb_generate_id(c), dgc = xcb_generate_id(c), mgc = 0;
    xcb_render_picture_t sp = xcb_generate_id(c), dp = xcb_generate_id(c), mp = 0;
    int fail = 0;

    if (!strcmp(kind, "src-op")) op = XCB_RENDER_PICT_OP_SRC;
    if (!strcmp(kind, "dst-argb")) { ddepth = 32; dfmt = f32; }
    for (int i = 0; i < 16; i++) sb[i] = spx;
    for (int i = 0; i < w * h; i++) db[i] = ddepth == 32 ? (0xff000000u | dpx) : dpx;
    xcb_create_pixmap(c, 32, spm, s->root, sw, sh);
    xcb_create_pixmap(c, ddepth, dpm, s->root, w, h);
    xcb_create_gc(c, sgc, spm, 0, NULL);
    xcb_create_gc(c, dgc, dpm, 0, NULL);
    put(c, spm, sgc, 32, sw, sh, sb);
    put(c, dpm, dgc, ddepth, w, h, db);
    xcb_render_create_picture(c, sp, spm, f32, 0, NULL);
    xcb_render_create_picture(c, dp, dpm, dfmt, 0, NULL);
    if (!strcmp(kind, "mask-a8") || !strcmp(kind, "component-alpha")) {
        int ca = !strcmp(kind, "component-alpha");
        uint8_t m8[16];
        uint32_t m32[16];
        mpm = xcb_generate_id(c); mp = xcb_generate_id(c); mgc = xcb_generate_id(c);
        memset(m8, 255, sizeof(m8));
        for (int i = 0; i < 16; i++) m32[i] = 0xffffffffu;
        xcb_create_pixmap(c, ca ? 32 : 8, mpm, s->root, w, h);
        xcb_create_gc(c, mgc, mpm, 0, NULL);
        if (ca) put(c, mpm, mgc, 32, w, h, m32);
        else xcb_put_image(c, XCB_IMAGE_FORMAT_Z_PIXMAP, mpm, mgc, w, h, 0, 0, 0, 8, 16, m8);
        xcb_render_create_picture(c, mp, mpm, ca ? f32 : fa8, 0, NULL);
        if (ca) {
            uint32_t v = 1;
            xcb_render_change_picture(c, mp, XCB_RENDER_CP_COMPONENT_ALPHA, &v);
        }
    }
    if (!strcmp(kind, "bilinear"))
        xcb_render_set_picture_filter(c, sp, 8, "bilinear", 0, NULL);
    if (!strcmp(kind, "repeat")) {
        uint32_t v = XCB_RENDER_REPEAT_NORMAL;
        xcb_render_change_picture(c, sp, XCB_RENDER_CP_REPEAT, &v);
    }
    if (!strcmp(kind, "transform")) {
        /* 2x downscale: inside a uniform source it samples the same colour */
        xcb_render_transform_t t = { 0x20000, 0, 0, 0, 0x20000, 0, 0, 0, 0x10000 };
        uint32_t v = XCB_RENDER_REPEAT_PAD;
        xcb_render_change_picture(c, sp, XCB_RENDER_CP_REPEAT, &v);
        xcb_render_set_picture_transform(c, sp, t);
    }
    xcb_generic_error_t *e = xcb_request_check(c, xcb_render_composite_checked(
        c, op, sp, mp ? mp : XCB_NONE, dp, 0, 0, 0, 0, 0, 0, w, h));
    if (e) {
        printf("FAIL neg %s composite_err=%d\n", kind, e->error_code);
        free(e);
        fail = 1;
    } else if (!get_px(c, dpm, w, h, got)) {
        printf("FAIL neg %s getimage\n", kind);
        fail = 1;
    } else {
        exp = op == XCB_RENDER_PICT_OP_SRC ? (spx & 0x00ffffffu) : ref_over_x8(spx, dpx);
        for (int i = 0; i < w * h; i++)
            if ((got[i] & 0x00ffffffu) != exp) {
                printf("FAIL neg %s px%d got=%08x exp=%06x\n", kind, i, got[i], exp);
                fail = 1;
                break;
            }
    }
    xcb_render_free_picture(c, sp);
    xcb_render_free_picture(c, dp);
    if (mp) xcb_render_free_picture(c, mp);
    xcb_free_pixmap(c, spm);
    xcb_free_pixmap(c, dpm);
    if (mpm) xcb_free_pixmap(c, mpm);
    xcb_free_gc(c, sgc);
    xcb_free_gc(c, dgc);
    if (mgc) xcb_free_gc(c, mgc);
    if (!sync_ok(c)) {
        printf("FAIL neg %s conn\n", kind);
        fail = 1;
    }
    st->cases++;
    st->fail += fail;
    if (!fail) printf("NEG %s OK\n", kind);
    return fail;
}

int main(void) {
    xcb_connection_t *c = xcb_connect(NULL, NULL);
    if (xcb_connection_has_error(c)) { printf("FAIL connect\n"); return 1; }
    setvbuf(stdout, NULL, _IOLBF, 0);
    xcb_screen_t *s = xcb_setup_roots_iterator(xcb_get_setup(c)).data;
    const xcb_query_extension_reply_t *ext = xcb_get_extension_data(c, &xcb_render_id);
    if (!ext || !ext->present) { printf("FAIL RENDER\n"); return 1; }
    xcb_render_query_pict_formats_reply_t *fr =
        xcb_render_query_pict_formats_reply(c, xcb_render_query_pict_formats(c), NULL);
    xcb_render_pictformat_t f24 = find_fmt(fr, 24, 0), f32 = find_fmt(fr, 32, 1), fa8 = find_fmt(fr, 8, 1);
    free(fr);
    if (!f24 || !f32 || !fa8) { printf("FAIL formats\n"); return 1; }
    int rc = 0;
    char name[96];

    /* ---- persistent: one pair per size, every case on it */
    struct Stats sp = {0};
    printf("MARK persistent BEGIN %.6f\n", wall());
    for (unsigned z = 0; z < N(SIZES); z++) {
        struct Pair p = { SIZES[z].w, SIZES[z].h, SIZES[z].w, SIZES[z].h, 0, 0, 0, 0, 0, 0 };
        pair_make(c, s, f32, f24, &p);
        for (unsigned a = 0; a < N(ALPHAS); a++)
            for (unsigned si = 0; si < N(SRCS); si++)
                for (unsigned di = 0; di < N(DSTS); di++) {
                    snprintf(name, sizeof(name), "persistent %ux%u a=%02x s=%u d=%u",
                             p.sw, p.sh, ALPHAS[a], si, di);
                    rc |= over_case(c, &p, 0, 0, 0, 0, p.sw, p.sh,
                                    premul(ALPHAS[a], SRCS[si].r, SRCS[si].g, SRCS[si].b),
                                    DSTS[di], &sp, name);
                }
        pair_free(c, &p);
        if (!sync_ok(c)) { printf("FAIL conn persistent\n"); return 1; }
    }
    {   /* offset and clip on their own pairs */
        struct Pair p = { 32, 32, 32, 32, 0, 0, 0, 0, 0, 0 };
        pair_make(c, s, f32, f24, &p);
        rc |= over_case(c, &p, 3, 5, 1, 2, 8, 8, premul(0x80, 255, 0, 0), 0x0000ff00, &sp,
                        "persistent offset 8x8 from 3,5 to 1,2");
        pair_free(c, &p);
        struct Pair q = { 16, 16, 32, 32, 0, 0, 0, 0, 0, 0 };
        pair_make(c, s, f32, f24, &q);
        rc |= over_case(c, &q, 0, 0, 8, 8, 16, 16, premul(0xfe, 0, 0, 255), 0x00ffffff, &sp,
                        "persistent clip 16x16 at 8,8");
        pair_free(c, &q);
    }
    sync_ok(c);
    printf("MARK persistent END %.6f\n", wall());
    phase_result("persistent", &sp);
    quiet();

    /* ---- fresh: a new pair per case (registration churn under correctness) */
    struct Stats sf = {0};
    printf("MARK fresh BEGIN %.6f\n", wall());
    for (unsigned z = 0; z < N(SIZES); z++)
        for (unsigned a = 0; a < N(ALPHAS); a++) {
            struct Pair p = { SIZES[z].w, SIZES[z].h, SIZES[z].w, SIZES[z].h, 0, 0, 0, 0, 0, 0 };
            pair_make(c, s, f32, f24, &p);
            snprintf(name, sizeof(name), "fresh %ux%u a=%02x", p.sw, p.sh, ALPHAS[a]);
            rc |= over_case(c, &p, 0, 0, 0, 0, p.sw, p.sh,
                            premul(ALPHAS[a], SRCS[5].r, SRCS[5].g, SRCS[5].b), DSTS[5], &sf, name);
            pair_free(c, &p);
        }
    sync_ok(c);
    printf("MARK fresh END %.6f\n", wall());
    phase_result("fresh", &sf);
    quiet();

    /* ---- negative: outside the slice */
    struct Stats sn = {0};
    static const char *NEG[] = { "src-op", "mask-a8", "dst-argb", "bilinear", "repeat",
                                 "transform", "component-alpha" };
    printf("MARK negative BEGIN %.6f\n", wall());
    for (unsigned i = 0; i < N(NEG); i++) {
        quiet();
        printf("MARK neg-%s BEGIN %.6f\n", NEG[i], wall());
        rc |= neg_case(c, s, f32, f24, fa8, NEG[i], &sn);
        printf("MARK neg-%s END %.6f\n", NEG[i], wall());
    }
    quiet();
    printf("MARK negative END %.6f\n", wall());
    phase_result("negative", &sn);

    if (!sync_ok(c)) { printf("FAIL X dead\n"); return 1; }
    printf("RESULT p_v1_oracle %s\n", rc ? "FAIL" : "PASS");
    xcb_disconnect(c);
    return rc ? 1 : 0;
}
