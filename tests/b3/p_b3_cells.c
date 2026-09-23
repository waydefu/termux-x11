/* p_b3_cells.c - V2-B3 performance fixture. One X connection runs a whole cell list.
 *
 *   p_b3_cells --cells cells.tsv --seed N [--iters 20 --warmup 3 --noise-every 25 --x-pid P]
 *              [--quiet-ms Q]   idle Q ms before CELL_BEGIN and after CELL_END (attribution runs
 *                               only: windows never touch; timing runs leave it 0 = unchanged)
 *
 * Per cell (columns of cells.tsv: id group rw rh sw sh ratio reuse batch residency readback)
 * it prints ONE JSON line: CELL {...}. Timing is client-side CLOCK_MONOTONIC around exactly
 * the work the residency defines (below); nothing inside a batch waits for a reply.
 *
 *   warm      `reuse` live sets, each PutImage'd once; an iteration = `batch` Over composites
 *             on set (iter % reuse) + the readback action
 *   cold      untimed: new set + PutImage + sync; timed: `batch` composites + readback; untimed free
 *             -> the first use of a set (promotion, registration)
 *   recreate  timed: create set, PutImage both, `batch` composites, readback, free, sync
 *   resize    timed: new dst of alternating size (rw,rh)/(rw+16,rh+16) + PutImage + composites
 *             + readback + free of the previous dst
 *   readback  none: one GetInputFocus round trip · immediate: GetImage of the dst rect ·
 *             delayed: round trip, and every 8th iteration GetImage of every live dst
 *
 * Source offset: the composite reads a centred rw x rh window of the sw x sh source, so the
 * ratio factor is real (a 16x source is sampled, not just allocated).
 * Correctness: after the timed iterations, one untimed check on a fresh set of the same
 * geometry: PutImage, `batch` composites, GetImage, compare with the iterated reference.
 * Order: cells shuffled with xorshift64(seed); the N row is re-run first, every
 * `noise_every` cells and last.
 *   cc -O2 -Wall -o /tmp/p_b3_cells p_b3_cells.c -lxcb -lxcb-render
 */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <xcb/render.h>
#include <xcb/xcb.h>

typedef struct {
    char id[16], group[4], ratio[16], residency[16], readback[16];
    int rw, rh, sw, sh, reuse, batch;
} Cell;

typedef struct {
    xcb_pixmap_t spm, dpm;
    xcb_gcontext_t sgc, dgc;
    xcb_render_picture_t sp, dp;
    int dw, dh;
} Set;

static xcb_connection_t *C;
static xcb_screen_t *S;
static xcb_render_pictformat_t F32, F24;
static uint32_t maxreq_bytes;
static uint32_t *SRCBUF, *DSTBUF;
static size_t SRCCAP, DSTCAP;
static int errors;

static uint64_t mono(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000000000ull + t.tv_nsec; }
static double wall(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static uint32_t div255(unsigned x) { return (x + 128 + ((x + 128) >> 8)) >> 8; }
static uint32_t over_x8(uint32_t s, uint32_t d) {
    unsigned ia = 255 - ((s >> 24) & 255), r = ((s >> 16) & 255) + div255(((d >> 16) & 255) * ia);
    unsigned g = ((s >> 8) & 255) + div255(((d >> 8) & 255) * ia), b = (s & 255) + div255((d & 255) * ia);
    return ((r > 255 ? 255 : r) << 16) | ((g > 255 ? 255 : g) << 8) | (b > 255 ? 255 : b);
}
#define SRC_PX 0x80800000u   /* premultiplied a=0x80 red */
#define DST_PX 0x0000ff00u

static void drain_errors(void) {
    xcb_generic_event_t *ev;
    while ((ev = xcb_poll_for_event(C))) {
        if (ev->response_type == 0) errors++;
        free(ev);
    }
}

static void sync_rt(void) {
    free(xcb_get_input_focus_reply(C, xcb_get_input_focus(C), NULL));
}

static void put_rows(xcb_drawable_t d, xcb_gcontext_t gc, int depth, int w, int h, const uint32_t *px) {
    int rows = (int) ((maxreq_bytes - 64) / ((uint32_t) w * 4));
    if (rows < 1) rows = 1;
    for (int y = 0; y < h; y += rows) {
        int n = h - y < rows ? h - y : rows;
        xcb_put_image(C, XCB_IMAGE_FORMAT_Z_PIXMAP, d, gc, (uint16_t) w, (uint16_t) n, 0, (int16_t) y,
                      0, (uint8_t) depth, (uint32_t) (w * n * 4), (const uint8_t *) (px + (size_t) y * w));
    }
}

static void fill_bufs(size_t ns, size_t nd) {
    if (ns > SRCCAP) { SRCBUF = realloc(SRCBUF, ns * 4); SRCCAP = ns; for (size_t i = 0; i < ns; i++) SRCBUF[i] = SRC_PX; }
    if (nd > DSTCAP) { DSTBUF = realloc(DSTBUF, nd * 4); DSTCAP = nd; for (size_t i = 0; i < nd; i++) DSTBUF[i] = DST_PX; }
}

static void set_make(Set *s, const Cell *c, int dw, int dh, int put) {
    s->spm = xcb_generate_id(C); s->dpm = xcb_generate_id(C);
    s->sgc = xcb_generate_id(C); s->dgc = xcb_generate_id(C);
    s->sp = xcb_generate_id(C); s->dp = xcb_generate_id(C);
    s->dw = dw; s->dh = dh;
    xcb_create_pixmap(C, 32, s->spm, S->root, (uint16_t) c->sw, (uint16_t) c->sh);
    xcb_create_pixmap(C, 24, s->dpm, S->root, (uint16_t) dw, (uint16_t) dh);
    xcb_create_gc(C, s->sgc, s->spm, 0, NULL);
    xcb_create_gc(C, s->dgc, s->dpm, 0, NULL);
    xcb_render_create_picture(C, s->sp, s->spm, F32, 0, NULL);
    xcb_render_create_picture(C, s->dp, s->dpm, F24, 0, NULL);
    xcb_render_set_picture_filter(C, s->sp, 7, "nearest", 0, NULL);
    if (put) {
        fill_bufs((size_t) c->sw * c->sh, (size_t) dw * dh);
        put_rows(s->spm, s->sgc, 32, c->sw, c->sh, SRCBUF);
        put_rows(s->dpm, s->dgc, 24, dw, dh, DSTBUF);
    }
}

static void set_put_dst(Set *s) {
    fill_bufs(0, (size_t) s->dw * s->dh);
    put_rows(s->dpm, s->dgc, 24, s->dw, s->dh, DSTBUF);
}

static void set_free(Set *s) {
    xcb_render_free_picture(C, s->sp); xcb_render_free_picture(C, s->dp);
    xcb_free_gc(C, s->sgc); xcb_free_gc(C, s->dgc);
    xcb_free_pixmap(C, s->spm); xcb_free_pixmap(C, s->dpm);
}

static void composites(const Cell *c, Set *s) {
    int16_t sx = (int16_t) ((c->sw - c->rw) / 2), sy = (int16_t) ((c->sh - c->rh) / 2);
    for (int b = 0; b < c->batch; b++)
        xcb_render_composite(C, XCB_RENDER_PICT_OP_OVER, s->sp, XCB_NONE, s->dp, sx, sy, 0, 0, 0, 0,
                             (uint16_t) c->rw, (uint16_t) c->rh);
}

static void getimage(Set *s, int w, int h) {
    xcb_get_image_reply_t *r = xcb_get_image_reply(
        C, xcb_get_image(C, XCB_IMAGE_FORMAT_Z_PIXMAP, s->dpm, 0, 0, (uint16_t) w, (uint16_t) h, ~0u), NULL);
    if (!r) errors++;
    free(r);
}

static void readback(const Cell *c, Set *sets, int nsets, Set *cur, int iter) {
    if (!strcmp(c->readback, "immediate")) {
        getimage(cur, c->rw, c->rh);
    } else {
        sync_rt();
        if (!strcmp(c->readback, "delayed") && iter % 8 == 7)
            for (int k = 0; k < nsets; k++) getimage(&sets[k], sets[k].dw, sets[k].dh);
    }
}

static int check_pixels(const Cell *c) {
    Set s;
    uint32_t exp = DST_PX & 0xffffff;
    int ok = 1;
    set_make(&s, c, c->rw, c->rh, 1);
    composites(c, &s);
    for (int b = 0; b < c->batch; b++) exp = over_x8(SRC_PX, exp);
    xcb_get_image_reply_t *r = xcb_get_image_reply(
        C, xcb_get_image(C, XCB_IMAGE_FORMAT_Z_PIXMAP, s.dpm, 0, 0, (uint16_t) c->rw, (uint16_t) c->rh, ~0u), NULL);
    if (!r) ok = 0;
    else {
        uint8_t *d = xcb_get_image_data(r);
        int stride = xcb_get_image_data_length(r) / c->rh;
        int pts[5][2] = { {0, 0}, {c->rw - 1, 0}, {0, c->rh - 1}, {c->rw - 1, c->rh - 1}, {c->rw / 2, c->rh / 2} };
        for (int i = 0; i < 5; i++) {
            uint8_t *p = d + pts[i][1] * stride + pts[i][0] * 4;
            uint32_t g = ((uint32_t) p[2] << 16) | ((uint32_t) p[1] << 8) | p[0];
            if (g != exp) ok = 0;
        }
        free(r);
    }
    set_free(&s);
    sync_rt();
    return ok;
}

static long x_ticks(int pid) {
    char path[64], buf[1024];
    if (pid <= 0) return -1;
    snprintf(path, sizeof(path), "/proc/%d/stat", pid);
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = 0;
    char *p = strrchr(buf, ')');
    if (!p) return -1;
    long ut = 0, st = 0;
    /* after ") S": fields 3.. ; utime is field 14, stime 15 */
    if (sscanf(p + 2, "%*c %*d %*d %*d %*d %*d %*u %*u %*u %*u %*u %ld %ld", &ut, &st) != 2) return -1;
    return ut + st;
}

static int cmp_u64(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *) a, y = *(const uint64_t *) b;
    return (x > y) - (x < y);
}

static int quiet_ms = 0;

static void run_cell(const Cell *c, int iters, int warmup, int xpid, int seq) {
    uint64_t *t = calloc((size_t) iters, sizeof(uint64_t));
    int nsets = !strcmp(c->residency, "warm") ? c->reuse : 1;
    Set *sets = calloc((size_t) nsets, sizeof(Set)), prev = {0};
    int have_prev = 0;
    errors = 0;
    if (quiet_ms > 0) usleep((useconds_t) quiet_ms * 1000);
    double w0 = wall();
    long x0 = x_ticks(xpid);
    printf("MARK CELL_BEGIN %s %.6f\n", c->id, w0);
    if (!strcmp(c->residency, "warm") || !strcmp(c->residency, "resize")) {
        for (int k = 0; k < nsets; k++) set_make(&sets[k], c, c->rw, c->rh, 1);
        sync_rt();
    }
    for (int it = -warmup; it < iters; it++) {
        uint64_t a = 0, b = 0;
        if (!strcmp(c->residency, "warm")) {
            Set *s = &sets[((it % nsets) + nsets) % nsets];
            a = mono(); composites(c, s); readback(c, sets, nsets, s, it); b = mono();
        } else if (!strcmp(c->residency, "cold")) {
            Set s; set_make(&s, c, c->rw, c->rh, 1); sync_rt();
            a = mono(); composites(c, &s); readback(c, &s, 1, &s, it); b = mono();
            set_free(&s); sync_rt();
        } else if (!strcmp(c->residency, "recreate")) {
            Set s;
            a = mono(); set_make(&s, c, c->rw, c->rh, 1); composites(c, &s); readback(c, &s, 1, &s, it);
            set_free(&s); sync_rt(); b = mono();
        } else {                                       /* resize: dst alternates size */
            int big = (it & 1);
            Set s = sets[0];
            a = mono();
            s.dpm = xcb_generate_id(C); s.dgc = xcb_generate_id(C); s.dp = xcb_generate_id(C);
            s.dw = c->rw + (big ? 16 : 0); s.dh = c->rh + (big ? 16 : 0);
            xcb_create_pixmap(C, 24, s.dpm, S->root, (uint16_t) s.dw, (uint16_t) s.dh);
            xcb_create_gc(C, s.dgc, s.dpm, 0, NULL);
            xcb_render_create_picture(C, s.dp, s.dpm, F24, 0, NULL);
            set_put_dst(&s);
            composites(c, &s); readback(c, &s, 1, &s, it);
            if (have_prev) {
                xcb_render_free_picture(C, prev.dp); xcb_free_gc(C, prev.dgc); xcb_free_pixmap(C, prev.dpm);
            }
            prev = s; have_prev = 1;
            b = mono();
        }
        if (it >= 0) t[it] = b - a;
        drain_errors();
    }
    if (have_prev) { xcb_render_free_picture(C, prev.dp); xcb_free_gc(C, prev.dgc); xcb_free_pixmap(C, prev.dpm); }
    if (!strcmp(c->residency, "warm") || !strcmp(c->residency, "resize"))
        for (int k = 0; k < nsets; k++) set_free(&sets[k]);
    sync_rt();
    long x1 = x_ticks(xpid);
    int pix = check_pixels(c);
    drain_errors();
    double w1 = wall();
    printf("MARK CELL_END %s %.6f\n", c->id, w1);
    if (quiet_ms > 0) usleep((useconds_t) quiet_ms * 1000);
    uint64_t *s2 = malloc((size_t) iters * sizeof(uint64_t));
    memcpy(s2, t, (size_t) iters * sizeof(uint64_t));
    qsort(s2, (size_t) iters, sizeof(uint64_t), cmp_u64);
    printf("CELL {\"seq\":%d,\"id\":\"%s\",\"group\":\"%s\",\"rw\":%d,\"rh\":%d,\"sw\":%d,\"sh\":%d,"
           "\"ratio\":\"%s\",\"reuse\":%d,\"batch\":%d,\"residency\":\"%s\",\"readback\":\"%s\","
           "\"iters\":%d,\"iter_ns_p50\":%" PRIu64 ",\"iter_ns_p95\":%" PRIu64 ",\"per_op_ns_p50\":%" PRIu64 ","
           "\"x_cpu_ticks\":%ld,\"errors\":%d,\"pixel_ok\":%s,\"wall\":[%.6f,%.6f],\"iter_ns\":[",
           seq, c->id, c->group, c->rw, c->rh, c->sw, c->sh, c->ratio, c->reuse, c->batch, c->residency,
           c->readback, iters, s2[iters / 2], s2[(iters * 95) / 100 < iters ? (iters * 95) / 100 : iters - 1],
           s2[iters / 2] / (uint64_t) c->batch, (x0 >= 0 && x1 >= 0) ? x1 - x0 : -1L, errors,
           pix ? "true" : "false", w0, w1);
    for (int i = 0; i < iters; i++) printf("%s%" PRIu64, i ? "," : "", t[i]);
    printf("]}\n");
    fflush(stdout);
    free(t); free(s2); free(sets);
}

int main(int argc, char **argv) {
    const char *cells_path = NULL;
    uint64_t seed = 1;
    int iters = 20, warmup = 3, every = 25, xpid = -1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--cells") && i + 1 < argc) cells_path = argv[++i];
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) seed = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--iters") && i + 1 < argc) iters = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--warmup") && i + 1 < argc) warmup = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--noise-every") && i + 1 < argc) every = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--x-pid") && i + 1 < argc) xpid = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--quiet-ms") && i + 1 < argc) quiet_ms = atoi(argv[++i]);
    }
    if (!cells_path || iters < 1) { fprintf(stderr, "usage\n"); return 64; }
    setvbuf(stdout, NULL, _IOLBF, 0);
    FILE *f = fopen(cells_path, "r");
    if (!f) { printf("FAIL cells\n"); return 1; }
    Cell *cells = NULL, noise = {0};
    int n = 0, have_noise = 0;
    char line[512];
    if (!fgets(line, sizeof(line), f)) return 1;       /* header */
    while (fgets(line, sizeof(line), f)) {
        Cell c = {0};
        if (sscanf(line, "%15s %3s %d %d %d %d %15s %d %d %15s %15s", c.id, c.group, &c.rw, &c.rh,
                   &c.sw, &c.sh, c.ratio, &c.reuse, &c.batch, c.residency, c.readback) != 11) continue;
        if (!strcmp(c.group, "N")) { noise = c; have_noise = 1; continue; }
        cells = realloc(cells, (size_t) (n + 1) * sizeof(Cell));
        cells[n++] = c;
    }
    fclose(f);
    for (int i = n - 1; i > 0; i--) {                   /* xorshift64 Fisher-Yates */
        seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
        int j = (int) (seed % (uint64_t) (i + 1));
        Cell tmp = cells[i]; cells[i] = cells[j]; cells[j] = tmp;
    }
    C = xcb_connect(NULL, NULL);
    if (xcb_connection_has_error(C)) { printf("FAIL connect\n"); return 1; }
    S = xcb_setup_roots_iterator(xcb_get_setup(C)).data;
    maxreq_bytes = xcb_get_maximum_request_length(C) * 4;
    xcb_render_query_pict_formats_reply_t *fr =
        xcb_render_query_pict_formats_reply(C, xcb_render_query_pict_formats(C), NULL);
    for (xcb_render_pictforminfo_iterator_t it = xcb_render_query_pict_formats_formats_iterator(fr);
         it.rem; xcb_render_pictforminfo_next(&it)) {
        xcb_render_pictforminfo_t *pf = it.data;
        if (pf->type != XCB_RENDER_PICT_TYPE_DIRECT) continue;
        if (!F24 && pf->depth == 24 && !pf->direct.alpha_mask) F24 = pf->id;
        if (!F32 && pf->depth == 32 && pf->direct.alpha_mask) F32 = pf->id;
    }
    free(fr);
    if (!F24 || !F32) { printf("FAIL formats\n"); return 1; }
    printf("RUN_BEGIN cells=%d noise=%d iters=%d warmup=%d every=%d %.6f\n", n, have_noise, iters, warmup, every, wall());
    int seq = 0;
    if (have_noise) run_cell(&noise, iters, warmup, xpid, seq++);
    for (int i = 0; i < n; i++) {
        run_cell(&cells[i], iters, warmup, xpid, seq++);
        if (have_noise && every > 0 && (i + 1) % every == 0) run_cell(&noise, iters, warmup, xpid, seq++);
        if (xcb_connection_has_error(C)) { printf("FAIL connection_lost after %s\n", cells[i].id); return 1; }
    }
    if (have_noise) run_cell(&noise, iters, warmup, xpid, seq++);
    printf("RUN_END %.6f\n", wall());
    xcb_disconnect(C);
    return 0;
}
