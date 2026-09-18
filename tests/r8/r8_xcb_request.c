#include "r8_xcb_request.h"
#include "r8-test-protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/uio.h>

xcb_extension_t r8_ext = { .name = LORIE_R8_TEST_NAME, .global_id = 0 };

int r8_send_checked(xcb_connection_t *c, uint8_t minor, void *bytes,
                    size_t nbytes, void **reply_out, char *err, size_t errlen) {
    const xcb_query_extension_reply_t *ext;
    struct iovec parts[4];
    xcb_protocol_request_t req;
    uint64_t seq;
    xcb_generic_error_t *e = NULL;
    uint8_t *hdr = bytes;

    if (err && errlen)
        err[0] = 0;
    if (reply_out)
        *reply_out = NULL;
    if (!c || !bytes || !reply_out) {
        snprintf(err, errlen, "FAIL r8 request minor=%u null_args", minor);
        return 1;
    }
    if (nbytes < 4 || (nbytes & 3u) != 0) {
        snprintf(err, errlen, "FAIL r8 request minor=%u bad_nbytes=%zu",
                 minor, nbytes);
        return 1;
    }

    ext = xcb_get_extension_data(c, &r8_ext);
    if (!ext || !ext->present) {
        snprintf(err, errlen, "FAIL LORIE-R8-TEST missing");
        return 1;
    }

    /* libxcb overwrites major (ext), minor (req.opcode), and length. */
    hdr[0] = 0;
    hdr[1] = 0;
    hdr[2] = 0;
    hdr[3] = 0;

    memset(parts, 0, sizeof(parts));
    parts[2].iov_base = bytes;
    parts[2].iov_len = nbytes;

    req.count = 1;
    req.ext = &r8_ext;
    req.opcode = minor;
    req.isvoid = 0;

    seq = xcb_send_request64(c, XCB_REQUEST_CHECKED, parts + 2, &req);
    if (seq == 0) {
        snprintf(err, errlen, "FAIL r8 request minor=%u send_seq=0", minor);
        return 1;
    }
    xcb_flush(c);
    *reply_out = xcb_wait_for_reply64(c, seq, &e);
    if (e) {
        snprintf(err, errlen,
                 "FAIL r8 request minor=%u error_code=%u sequence=%u minor_code=%u",
                 minor, (unsigned)e->error_code, (unsigned)e->sequence,
                 (unsigned)e->minor_code);
        free(e);
        free(*reply_out);
        *reply_out = NULL;
        return 1;
    }
    if (!*reply_out) {
        snprintf(err, errlen, "FAIL r8 request minor=%u no_reply seq=%llu",
                 minor, (unsigned long long)seq);
        return 1;
    }
    return 0;
}
