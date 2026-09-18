/* Shared LORIE-R8-TEST xcb sender.
 * Authority: installed libxcb 1.17.0
 *   /usr/include/xcb/xcbext.h (vector[-1]/[-2] reserved)
 *   src/xcb_out.c xcb_send_request_with_fds64
 *   src/c_client.py (xcb_parts + 2, .ext, .opcode = minor)
 */
#ifndef R8_XCB_REQUEST_H
#define R8_XCB_REQUEST_H

#include <stddef.h>
#include <stdint.h>
#include <xcb/xcb.h>
#include <xcb/xcbext.h>

#ifdef __cplusplus
extern "C" {
#endif

extern xcb_extension_t r8_ext;

int r8_send_checked(xcb_connection_t *c, uint8_t minor, void *bytes,
                    size_t nbytes, void **reply_out, char *err, size_t errlen);

#ifdef __cplusplus
}
#endif

#endif
