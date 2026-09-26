// req_count.so - DIAGNOSTIC (not a GL-PRESENT-01 judged tool): histogram of every X request a client
// sends through libxcb, keyed by extension name + minor opcode (core: "core" + major opcode), with bytes.
// Written 2026-09-26 after dry-01-r2: the EGL/Zink client presented 3569 frames without a single call that
// present_count.so hooks. Hooks the four libxcb send entry points that every request function
// (core and extension) goes through, plus xcb_writev (the Xlib-over-xcb path).
// Also parses the byte stream written (writev/sendmsg) to X11 sockets into a per-major-opcode histogram,
// because libxcb's own core request functions reach xcb_send_request without a PLT hop.
// Output: one JSON line to $REQ_COUNT_OUT at normal exit.
//   cc -O2 -Wall -shared -fPIC -o req_count.so req_count.c -Wl,--no-as-needed -lxcb -ldl
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <sys/un.h>
#include <unistd.h>
#include <xcb/xcb.h>
#include <xcb/xcbext.h>

#define MAXK 128
struct ent { char ext[32]; unsigned op; uint64_t n, bytes, fds; };
static struct ent tab[MAXK];
static int ntab;
static uint64_t writev_calls, writev_bytes;
static void *next_sym(const char *name);
// libc-level parse of the byte stream sent to X11 sockets: libxcb's own core request functions call
// xcb_send_request without a PLT hop, so only this level sees core requests (e.g. PutImage, CopyArea).
struct major { uint64_t n, bytes, fds; };
static struct major majors[256];
struct fdst { int known, isx, setup_done; uint64_t left; unsigned char hdr[12]; int hlen; };
static struct fdst fdtab[1024];
static uint64_t sock_fds_passed;

static int is_x_socket(int fd) {
    struct sockaddr_un a; socklen_t l = sizeof(a);
    memset(&a, 0, sizeof(a));
    if (getpeername(fd, (struct sockaddr *) &a, &l) != 0 || a.sun_family != AF_UNIX) return 0;
    return strstr(a.sun_path, ".X11-unix/X") != NULL || strstr(a.sun_path + 1, ".X11-unix/X") != NULL;
}
static void parse(int fd, const unsigned char *p, size_t n, unsigned nfds) {
    if (fd < 0 || fd >= 1024) return;
    struct fdst *st = &fdtab[fd];
    if (!st->known) { st->known = 1; st->isx = is_x_socket(fd); }
    if (!st->isx) return;
    int first = 1;
    while (n) {
        if (st->left) { size_t k = n < st->left ? n : st->left; st->left -= k; p += k; n -= k; continue; }
        int need = st->setup_done ? 4 : 12;
        while (st->hlen < need && n) { st->hdr[st->hlen++] = *p++; n--; }
        if (st->hlen < need) break;
        uint64_t len;
        if (!st->setup_done) {   // connection setup: 12-byte header + pad4(auth name) + pad4(auth data)
            int le = st->hdr[0] == 'l';
            unsigned nl = le ? (st->hdr[6] | st->hdr[7] << 8) : (st->hdr[7] | st->hdr[6] << 8);
            unsigned dl = le ? (st->hdr[8] | st->hdr[9] << 8) : (st->hdr[9] | st->hdr[8] << 8);
            st->setup_done = 1; st->hlen = 0;
            st->left = ((nl + 3) & ~3u) + ((dl + 3) & ~3u);
            continue;
        }
        len = (uint64_t) (st->hdr[2] | st->hdr[3] << 8) * 4;
        if (len == 0) {   // BIG-REQUESTS: 32-bit length follows the 4-byte header
            while (st->hlen < 8 && n) { st->hdr[st->hlen++] = *p++; n--; }
            if (st->hlen < 8) break;
            len = (uint64_t) (st->hdr[4] | st->hdr[5] << 8 | st->hdr[6] << 16 | (uint32_t) st->hdr[7] << 24) * 4;
        }
        unsigned major = st->hdr[0];
        __atomic_fetch_add(&majors[major].n, 1, __ATOMIC_RELAXED);
        __atomic_fetch_add(&majors[major].bytes, len, __ATOMIC_RELAXED);
        if (first && nfds) { __atomic_fetch_add(&majors[major].fds, nfds, __ATOMIC_RELAXED); first = 0; }
        st->left = len > (uint64_t) st->hlen ? len - st->hlen : 0;
        st->hlen = 0;
    }
}
ssize_t writev(int fd, const struct iovec *v, int n) {
    static __typeof__(writev) *real;
    if (!real) real = next_sym("writev");
    ssize_t r = real(fd, v, n);
    if (r > 0) { size_t left = r; for (int i = 0; i < n && left; i++) { size_t k = v[i].iov_len < left ? v[i].iov_len : left; parse(fd, v[i].iov_base, k, 0); left -= k; } }
    return r;
}
ssize_t sendmsg(int fd, const struct msghdr *m, int flags) {
    static __typeof__(sendmsg) *real;
    if (!real) real = next_sym("sendmsg");
    ssize_t r = real(fd, m, flags);
    unsigned nfds = 0;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(m); c; c = CMSG_NXTHDR((struct msghdr *) m, c))
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS) nfds += (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
    if (nfds) __atomic_fetch_add(&sock_fds_passed, nfds, __ATOMIC_RELAXED);
    if (r > 0) { size_t left = r; for (size_t i = 0; i < m->msg_iovlen && left; i++) { size_t k = m->msg_iov[i].iov_len < left ? m->msg_iov[i].iov_len : left; parse(fd, m->msg_iov[i].iov_base, k, nfds); nfds = 0; left -= k; } }
    return r;
}
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static void *next_sym(const char *name) {
    void *p = dlsym(RTLD_NEXT, name);
    if (!p) { fprintf(stderr, "req_count: no next symbol for %s\n", name); abort(); }
    return p;
}

static void note(const xcb_protocol_request_t *req, const struct iovec *v, unsigned nfds) {
    const char *ext = req->ext ? req->ext->name : "core";
    uint64_t b = 0;
    for (size_t i = 0; i < req->count; i++) b += v[i].iov_len;
    pthread_mutex_lock(&mu);
    int i;
    for (i = 0; i < ntab; i++)
        if (tab[i].op == req->opcode && !strncmp(tab[i].ext, ext, sizeof(tab[i].ext) - 1)) break;
    if (i == ntab && ntab < MAXK) { snprintf(tab[i].ext, sizeof(tab[i].ext), "%s", ext); tab[i].op = req->opcode; ntab++; }
    if (i < MAXK) { tab[i].n++; tab[i].bytes += b; tab[i].fds += nfds; }
    pthread_mutex_unlock(&mu);
}

unsigned int xcb_send_request(xcb_connection_t *c, int flags, struct iovec *v, const xcb_protocol_request_t *r) {
    static __typeof__(xcb_send_request) *real;
    if (!real) real = next_sym("xcb_send_request");
    note(r, v, 0);
    return real(c, flags, v, r);
}
uint64_t xcb_send_request64(xcb_connection_t *c, int flags, struct iovec *v, const xcb_protocol_request_t *r) {
    static __typeof__(xcb_send_request64) *real;
    if (!real) real = next_sym("xcb_send_request64");
    note(r, v, 0);
    return real(c, flags, v, r);
}
unsigned int xcb_send_request_with_fds(xcb_connection_t *c, int flags, struct iovec *v,
                                       const xcb_protocol_request_t *r, unsigned int nfd, int *fds) {
    static __typeof__(xcb_send_request_with_fds) *real;
    if (!real) real = next_sym("xcb_send_request_with_fds");
    note(r, v, nfd);
    return real(c, flags, v, r, nfd, fds);
}
uint64_t xcb_send_request_with_fds64(xcb_connection_t *c, int flags, struct iovec *v,
                                     const xcb_protocol_request_t *r, unsigned int nfd, int *fds) {
    static __typeof__(xcb_send_request_with_fds64) *real;
    if (!real) real = next_sym("xcb_send_request_with_fds64");
    note(r, v, nfd);
    return real(c, flags, v, r, nfd, fds);
}
int xcb_writev(xcb_connection_t *c, struct iovec *v, int n, uint64_t requests) {
    static __typeof__(xcb_writev) *real;
    if (!real) real = next_sym("xcb_writev");
    uint64_t b = 0;
    for (int i = 0; i < n; i++) b += v[i].iov_len;
    __atomic_fetch_add(&writev_calls, 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&writev_bytes, b, __ATOMIC_RELAXED);
    return real(c, v, n, requests);
}

__attribute__((destructor)) static void dump(void) {
    const char *out = getenv("REQ_COUNT_OUT");
    FILE *f;
    if (!out || !*out || !(f = fopen(out, "a"))) return;
    fprintf(f, "{\"diag\":\"req_count/1\",\"pid\":%d,\"xlib_writev_calls\":%llu,\"xlib_writev_bytes\":%llu,\"requests\":[",
            (int) getpid(), (unsigned long long) writev_calls, (unsigned long long) writev_bytes);
    for (int i = 0; i < ntab; i++)
        fprintf(f, "%s{\"ext\":\"%s\",\"op\":%u,\"n\":%llu,\"bytes\":%llu,\"fds\":%llu}", i ? "," : "", tab[i].ext,
                tab[i].op, (unsigned long long) tab[i].n, (unsigned long long) tab[i].bytes, (unsigned long long) tab[i].fds);
    fprintf(f, "],\"sock_fds_passed\":%llu,\"majors\":[", (unsigned long long) sock_fds_passed);
    int first = 1;
    for (int m = 0; m < 256; m++)
        if (majors[m].n) { fprintf(f, "%s{\"major\":%d,\"n\":%llu,\"bytes\":%llu,\"fds\":%llu}", first ? "" : ",", m,
                                   (unsigned long long) majors[m].n, (unsigned long long) majors[m].bytes, (unsigned long long) majors[m].fds); first = 0; }
    fprintf(f, "]}\n");
    fclose(f);
}
