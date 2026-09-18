/* Host proof: LORIE-R8-TEST xcb construction uses extension minor opcodes.
 * Wraps libxcb 1.17.0 xcb_send_request64 / wait_for_reply64.
 */
#include "r8_xcb_request.h"
#include "r8-test-protocol.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/uio.h>
#include <xcb/xcb.h>
#include <xcb/xcbext.h>

static xcb_query_extension_reply_t g_ext;
static xcb_protocol_request_t g_req;
static uint8_t g_hdr[4];
static size_t g_iov0_len;
static int g_reserved_ok;
static int g_send_calls;
static uint8_t g_reply_minor_seen[4];

const xcb_query_extension_reply_t *__wrap_xcb_get_extension_data(
    xcb_connection_t *c, xcb_extension_t *ext) {
    (void)c;
    if (ext != &r8_ext)
        return NULL;
    memset(&g_ext, 0, sizeof(g_ext));
    g_ext.present = 1;
    g_ext.major_opcode = 140;
    return &g_ext;
}

uint64_t __wrap_xcb_send_request64(xcb_connection_t *c, int flags,
                                   struct iovec *vector,
                                   const xcb_protocol_request_t *req) {
    volatile struct iovec reserved0, reserved1;
    (void)c;
    (void)flags;
    reserved0 = vector[-2];
    reserved1 = vector[-1];
    g_reserved_ok = 1;
    (void)reserved0;
    (void)reserved1;
    g_req = *req;
    g_iov0_len = vector[0].iov_len;
    memset(g_hdr, 0, sizeof(g_hdr));
    if (vector[0].iov_base && vector[0].iov_len >= 4) {
        uint8_t *b = vector[0].iov_base;
        if (req->ext) {
            b[0] = 140;
            b[1] = req->opcode;
        } else {
            b[0] = req->opcode;
        }
        memcpy(g_hdr, b, 4);
    }
    g_send_calls++;
    return (uint64_t)g_send_calls;
}

void *__wrap_xcb_wait_for_reply64(xcb_connection_t *c, uint64_t request,
                                  xcb_generic_error_t **e) {
    uint8_t *rep;
    (void)c;
    (void)request;
    if (e)
        *e = NULL;
    if (g_req.opcode < 4)
        g_reply_minor_seen[g_req.opcode] = 1;
    rep = calloc(1, 32);
    if (rep)
        rep[0] = 1; /* X_Reply */
    return rep;
}

int __wrap_xcb_flush(xcb_connection_t *c) {
    (void)c;
    return 1;
}

int __wrap_xcb_connection_has_error(xcb_connection_t *c) {
    (void)c;
    return 0;
}

static void fail(const char *m) {
    fprintf(stderr, "FAIL %s opcode=%u hdr=%u,%u ext=%p\n", m,
            (unsigned)g_req.opcode, g_hdr[0], g_hdr[1], (void *)g_req.ext);
    exit(1);
}

static void send_one(uint8_t minor, size_t nbytes) {
    uint8_t buf[8];
    void *rep = NULL;
    char err[256];
    xcb_connection_t *c = (xcb_connection_t *)(uintptr_t)0x1;
    memset(buf, 0xaa, sizeof(buf));
    if (r8_send_checked(c, minor, buf, nbytes, &rep, err, sizeof(err))) {
        fprintf(stderr, "send fail %s\n", err);
        fail("send");
    }
    if (!rep)
        fail("reply");
    free(rep);
    if (g_req.ext != &r8_ext)
        fail("ext");
    if (g_req.opcode != minor)
        fail("minor");
    if (g_req.isvoid != 0)
        fail("isvoid");
    if (!g_reserved_ok)
        fail("reserved_iovecs");
    if (g_iov0_len < 4)
        fail("hdr_len");
    if (g_hdr[0] != 140)
        fail("major");
    if (g_hdr[1] != minor)
        fail("hdr_minor");
}

static void send_malformed_core_must_not_look_like_ext(void) {
    uint8_t q[4] = { 99, 3, 1, 0 };
    struct iovec parts[4];
    xcb_protocol_request_t req;
    xcb_connection_t *c = (xcb_connection_t *)(uintptr_t)0x1;

    memset(parts, 0, sizeof(parts));
    parts[2].iov_base = q;
    parts[2].iov_len = 4;
    memset(&req, 0, sizeof(req));
    req.count = 1;
    req.ext = NULL;
    req.opcode = 0;
    req.isvoid = 0;
    (void)xcb_send_request64(c, XCB_REQUEST_CHECKED, parts + 2, &req);
    if (g_req.ext != NULL)
        fail("malformed_ext");
    if (g_hdr[0] != 0)
        fail("malformed_core_opcode");
    if (g_hdr[0] == 140)
        fail("malformed_looks_like_ext");
}

int main(void) {
    send_one(X_LorieR8QueryVersion, 4);
    send_one(X_LorieR8RegisterBuffer, 8);
    send_one(X_LorieR8Checkpoint, 8);
    send_one(X_LorieR8Terminate, 4);
    if (!g_reply_minor_seen[0] || !g_reply_minor_seen[1] ||
        !g_reply_minor_seen[2] || !g_reply_minor_seen[3])
        fail("reply_path");
    send_malformed_core_must_not_look_like_ext();
    printf("PASS r8_xcb_request minors=0,1,2,3 malformed_core_fails_ext_identity\n");
    return 0;
}
