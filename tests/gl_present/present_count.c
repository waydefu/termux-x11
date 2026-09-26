// present_count.so - LD_PRELOAD counter for the ways an X11 client can hand a frame to the X server.
// GL-PRESENT-01 (evidence/session/gl/GL-PRESENT-01-FREEZE.md), mainline #3 step 1.
//
// Counts, per process, every call of:
//   xcb_dri3_pixmap_from_buffer[_checked]    DRI3 1.0 fd import (records w/h/stride/depth/bpp)
//   xcb_dri3_pixmap_from_buffers[_checked]   DRI3 1.2 fd import (records w/h/stride/modifier/num_buffers)
//   xcb_dri3_open                             the client asked the server for a device fd
//   xcb_present_pixmap[_checked]             Present a pixmap (DRI3 path, and kopper)
//   xcb_present_pixmap_synced[_checked]      Present 1.4 explicit-sync variant
//   xcb_shm_put_image[_checked]              MIT-SHM upload (pixels = src_width*src_height)
//   xcb_shm_create_pixmap / xcb_shm_attach[_fd] (+_checked)  SHM pixmaps (Vulkan WSI sw + MIT-SHM)
//   xcb_put_image[_checked]                  core PutImage (bytes = data_len)
//   XPutImage / XShmPutImage                  the same through Xlib (drisw GLX uses these)
// and writes one JSON line to $PRESENT_COUNT_OUT (appended) when the process exits normally.
// Calls are forwarded unchanged (dlsym RTLD_NEXT); counters are atomics because Vulkan WSI
// presents from its own thread. A process that exits through _exit() writes nothing, so a
// missing line means "not measured" (null), never zero.
//
//   cc -O2 -Wall -shared -fPIC -o present_count.so present_count.c -Wl,--no-as-needed (continued)
//      -lxcb-dri3 -lxcb-present -lxcb-shm -lxcb -lXext -lX11 -ldl
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <xcb/xcb.h>
#include <xcb/dri3.h>
#include <xcb/present.h>
#include <xcb/shm.h>
#include <X11/Xlib.h>
#include <X11/extensions/XShm.h>

#define ADD(v, n) __atomic_fetch_add(&(v), (n), __ATOMIC_RELAXED)
#define MAXREC 16

static uint64_t n_pfb, n_pfbs, n_open, n_present, n_present_synced, n_shm, shm_pixels, n_put, put_bytes;
static uint64_t n_xput, xput_pixels, n_xshm, xshm_pixels, n_shm_pixmap, n_shm_attach;
struct rec { uint32_t w, h, stride, depth, bpp, nbuf; uint64_t modifier; int multi; };
static struct rec recs[MAXREC];
static uint32_t nrec;

static void record(int multi, uint32_t w, uint32_t h, uint32_t stride, uint32_t depth, uint32_t bpp,
                   uint32_t nbuf, uint64_t modifier) {
    uint32_t i = __atomic_fetch_add(&nrec, 1, __ATOMIC_RELAXED);
    if (i < MAXREC)
        recs[i] = (struct rec) { w, h, stride, depth, bpp, nbuf, modifier, multi };
}

// The shim is linked against every library it wraps, so they sit in the global scope after it and
// RTLD_NEXT finds them even when the caller (a GL/Vulkan driver) was dlopen'ed RTLD_LOCAL by glvnd or
// the Vulkan loader. Without that link, dlsym returned NULL for XShmPutImage and the client died with
// SIGSEGV on its first swap (host qualification on Xvfb, 2026-09-26). A missing next symbol now aborts
// loudly instead.
static void *next_sym(const char *name) {
    void *p = dlsym(RTLD_NEXT, name);
    if (!p) { fprintf(stderr, "present_count: no next symbol for %s\n", name); abort(); }
    return p;
}
#define NEXT(name) static __typeof__(name) *real; if (!real) real = (__typeof__(name) *) next_sym(#name)

xcb_void_cookie_t xcb_dri3_pixmap_from_buffer(xcb_connection_t *c, xcb_pixmap_t pixmap, xcb_drawable_t drawable,
        uint32_t size, uint16_t width, uint16_t height, uint16_t stride, uint8_t depth, uint8_t bpp, int32_t fd) {
    NEXT(xcb_dri3_pixmap_from_buffer);
    ADD(n_pfb, 1); record(0, width, height, stride, depth, bpp, 1, UINT64_MAX);
    return real(c, pixmap, drawable, size, width, height, stride, depth, bpp, fd);
}
xcb_void_cookie_t xcb_dri3_pixmap_from_buffer_checked(xcb_connection_t *c, xcb_pixmap_t pixmap, xcb_drawable_t drawable,
        uint32_t size, uint16_t width, uint16_t height, uint16_t stride, uint8_t depth, uint8_t bpp, int32_t fd) {
    NEXT(xcb_dri3_pixmap_from_buffer_checked);
    ADD(n_pfb, 1); record(0, width, height, stride, depth, bpp, 1, UINT64_MAX);
    return real(c, pixmap, drawable, size, width, height, stride, depth, bpp, fd);
}
xcb_void_cookie_t xcb_dri3_pixmap_from_buffers(xcb_connection_t *c, xcb_pixmap_t pixmap, xcb_window_t window,
        uint8_t num_buffers, uint16_t width, uint16_t height, uint32_t stride0, uint32_t offset0, uint32_t stride1,
        uint32_t offset1, uint32_t stride2, uint32_t offset2, uint32_t stride3, uint32_t offset3, uint8_t depth,
        uint8_t bpp, uint64_t modifier, const int32_t *buffers) {
    NEXT(xcb_dri3_pixmap_from_buffers);
    ADD(n_pfbs, 1); record(1, width, height, stride0, depth, bpp, num_buffers, modifier);
    return real(c, pixmap, window, num_buffers, width, height, stride0, offset0, stride1, offset1, stride2, offset2,
                stride3, offset3, depth, bpp, modifier, buffers);
}
xcb_void_cookie_t xcb_dri3_pixmap_from_buffers_checked(xcb_connection_t *c, xcb_pixmap_t pixmap, xcb_window_t window,
        uint8_t num_buffers, uint16_t width, uint16_t height, uint32_t stride0, uint32_t offset0, uint32_t stride1,
        uint32_t offset1, uint32_t stride2, uint32_t offset2, uint32_t stride3, uint32_t offset3, uint8_t depth,
        uint8_t bpp, uint64_t modifier, const int32_t *buffers) {
    NEXT(xcb_dri3_pixmap_from_buffers_checked);
    ADD(n_pfbs, 1); record(1, width, height, stride0, depth, bpp, num_buffers, modifier);
    return real(c, pixmap, window, num_buffers, width, height, stride0, offset0, stride1, offset1, stride2, offset2,
                stride3, offset3, depth, bpp, modifier, buffers);
}
xcb_dri3_open_cookie_t xcb_dri3_open(xcb_connection_t *c, xcb_drawable_t drawable, uint32_t provider) {
    NEXT(xcb_dri3_open);
    ADD(n_open, 1);
    return real(c, drawable, provider);
}
xcb_void_cookie_t xcb_present_pixmap(xcb_connection_t *c, xcb_window_t window, xcb_pixmap_t pixmap, uint32_t serial,
        xcb_xfixes_region_t valid, xcb_xfixes_region_t update, int16_t x_off, int16_t y_off, xcb_randr_crtc_t target_crtc,
        xcb_sync_fence_t wait_fence, xcb_sync_fence_t idle_fence, uint32_t options, uint64_t target_msc, uint64_t divisor,
        uint64_t remainder, uint32_t notifies_len, const xcb_present_notify_t *notifies) {
    NEXT(xcb_present_pixmap);
    ADD(n_present, 1);
    return real(c, window, pixmap, serial, valid, update, x_off, y_off, target_crtc, wait_fence, idle_fence, options,
                target_msc, divisor, remainder, notifies_len, notifies);
}
xcb_void_cookie_t xcb_present_pixmap_checked(xcb_connection_t *c, xcb_window_t window, xcb_pixmap_t pixmap, uint32_t serial,
        xcb_xfixes_region_t valid, xcb_xfixes_region_t update, int16_t x_off, int16_t y_off, xcb_randr_crtc_t target_crtc,
        xcb_sync_fence_t wait_fence, xcb_sync_fence_t idle_fence, uint32_t options, uint64_t target_msc, uint64_t divisor,
        uint64_t remainder, uint32_t notifies_len, const xcb_present_notify_t *notifies) {
    NEXT(xcb_present_pixmap_checked);
    ADD(n_present, 1);
    return real(c, window, pixmap, serial, valid, update, x_off, y_off, target_crtc, wait_fence, idle_fence, options,
                target_msc, divisor, remainder, notifies_len, notifies);
}
#ifdef XCB_PRESENT_PIXMAP_SYNCED
xcb_void_cookie_t xcb_present_pixmap_synced(xcb_connection_t *c, xcb_window_t window, xcb_pixmap_t pixmap, uint32_t serial,
        xcb_xfixes_region_t valid, xcb_xfixes_region_t update, int16_t x_off, int16_t y_off, xcb_randr_crtc_t target_crtc,
        xcb_dri3_syncobj_t acquire_syncobj, xcb_dri3_syncobj_t release_syncobj, uint64_t acquire_point,
        uint64_t release_point, uint32_t options, uint64_t target_msc, uint64_t divisor, uint64_t remainder,
        uint32_t notifies_len, const xcb_present_notify_t *notifies) {
    NEXT(xcb_present_pixmap_synced);
    ADD(n_present_synced, 1);
    return real(c, window, pixmap, serial, valid, update, x_off, y_off, target_crtc, acquire_syncobj, release_syncobj,
                acquire_point, release_point, options, target_msc, divisor, remainder, notifies_len, notifies);
}
xcb_void_cookie_t xcb_present_pixmap_synced_checked(xcb_connection_t *c, xcb_window_t window, xcb_pixmap_t pixmap,
        uint32_t serial, xcb_xfixes_region_t valid, xcb_xfixes_region_t update, int16_t x_off, int16_t y_off,
        xcb_randr_crtc_t target_crtc, xcb_dri3_syncobj_t acquire_syncobj, xcb_dri3_syncobj_t release_syncobj,
        uint64_t acquire_point, uint64_t release_point, uint32_t options, uint64_t target_msc, uint64_t divisor,
        uint64_t remainder, uint32_t notifies_len, const xcb_present_notify_t *notifies) {
    NEXT(xcb_present_pixmap_synced_checked);
    ADD(n_present_synced, 1);
    return real(c, window, pixmap, serial, valid, update, x_off, y_off, target_crtc, acquire_syncobj, release_syncobj,
                acquire_point, release_point, options, target_msc, divisor, remainder, notifies_len, notifies);
}
#endif
xcb_void_cookie_t xcb_shm_put_image(xcb_connection_t *c, xcb_drawable_t drawable, xcb_gcontext_t gc, uint16_t total_width,
        uint16_t total_height, uint16_t src_x, uint16_t src_y, uint16_t src_width, uint16_t src_height, int16_t dst_x,
        int16_t dst_y, uint8_t depth, uint8_t format, uint8_t send_event, xcb_shm_seg_t shmseg, uint32_t offset) {
    NEXT(xcb_shm_put_image);
    ADD(n_shm, 1); ADD(shm_pixels, (uint64_t) src_width * src_height);
    return real(c, drawable, gc, total_width, total_height, src_x, src_y, src_width, src_height, dst_x, dst_y, depth,
                format, send_event, shmseg, offset);
}
xcb_void_cookie_t xcb_shm_put_image_checked(xcb_connection_t *c, xcb_drawable_t drawable, xcb_gcontext_t gc,
        uint16_t total_width, uint16_t total_height, uint16_t src_x, uint16_t src_y, uint16_t src_width,
        uint16_t src_height, int16_t dst_x, int16_t dst_y, uint8_t depth, uint8_t format, uint8_t send_event,
        xcb_shm_seg_t shmseg, uint32_t offset) {
    NEXT(xcb_shm_put_image_checked);
    ADD(n_shm, 1); ADD(shm_pixels, (uint64_t) src_width * src_height);
    return real(c, drawable, gc, total_width, total_height, src_x, src_y, src_width, src_height, dst_x, dst_y, depth,
                format, send_event, shmseg, offset);
}
xcb_void_cookie_t xcb_put_image(xcb_connection_t *c, uint8_t format, xcb_drawable_t drawable, xcb_gcontext_t gc,
        uint16_t width, uint16_t height, int16_t dst_x, int16_t dst_y, uint8_t left_pad, uint8_t depth,
        uint32_t data_len, const uint8_t *data) {
    NEXT(xcb_put_image);
    ADD(n_put, 1); ADD(put_bytes, data_len);
    return real(c, format, drawable, gc, width, height, dst_x, dst_y, left_pad, depth, data_len, data);
}
xcb_void_cookie_t xcb_put_image_checked(xcb_connection_t *c, uint8_t format, xcb_drawable_t drawable, xcb_gcontext_t gc,
        uint16_t width, uint16_t height, int16_t dst_x, int16_t dst_y, uint8_t left_pad, uint8_t depth,
        uint32_t data_len, const uint8_t *data) {
    NEXT(xcb_put_image_checked);
    ADD(n_put, 1); ADD(put_bytes, data_len);
    return real(c, format, drawable, gc, width, height, dst_x, dst_y, left_pad, depth, data_len, data);
}
// Vulkan WSI in sw mode with MIT-SHM does not use ShmPutImage: it attaches the image memory
// (xcb_shm_attach / _fd), wraps it in an SHM pixmap and presents that (wsi_common_x11.c:2113-2118).
xcb_void_cookie_t xcb_shm_create_pixmap(xcb_connection_t *c, xcb_pixmap_t pid, xcb_drawable_t drawable, uint16_t width,
        uint16_t height, uint8_t depth, xcb_shm_seg_t shmseg, uint32_t offset) {
    NEXT(xcb_shm_create_pixmap);
    ADD(n_shm_pixmap, 1);
    return real(c, pid, drawable, width, height, depth, shmseg, offset);
}
xcb_void_cookie_t xcb_shm_create_pixmap_checked(xcb_connection_t *c, xcb_pixmap_t pid, xcb_drawable_t drawable,
        uint16_t width, uint16_t height, uint8_t depth, xcb_shm_seg_t shmseg, uint32_t offset) {
    NEXT(xcb_shm_create_pixmap_checked);
    ADD(n_shm_pixmap, 1);
    return real(c, pid, drawable, width, height, depth, shmseg, offset);
}
xcb_void_cookie_t xcb_shm_attach(xcb_connection_t *c, xcb_shm_seg_t shmseg, uint32_t shmid, uint8_t read_only) {
    NEXT(xcb_shm_attach);
    ADD(n_shm_attach, 1);
    return real(c, shmseg, shmid, read_only);
}
xcb_void_cookie_t xcb_shm_attach_checked(xcb_connection_t *c, xcb_shm_seg_t shmseg, uint32_t shmid, uint8_t read_only) {
    NEXT(xcb_shm_attach_checked);
    ADD(n_shm_attach, 1);
    return real(c, shmseg, shmid, read_only);
}
xcb_void_cookie_t xcb_shm_attach_fd(xcb_connection_t *c, xcb_shm_seg_t shmseg, int32_t shm_fd, uint8_t read_only) {
    NEXT(xcb_shm_attach_fd);
    ADD(n_shm_attach, 1);
    return real(c, shmseg, shm_fd, read_only);
}
xcb_void_cookie_t xcb_shm_attach_fd_checked(xcb_connection_t *c, xcb_shm_seg_t shmseg, int32_t shm_fd, uint8_t read_only) {
    NEXT(xcb_shm_attach_fd_checked);
    ADD(n_shm_attach, 1);
    return real(c, shmseg, shm_fd, read_only);
}
int XPutImage(Display *d, Drawable dr, GC gc, XImage *im, int sx, int sy, int dx, int dy, unsigned int w, unsigned int h) {
    NEXT(XPutImage);
    ADD(n_xput, 1); ADD(xput_pixels, (uint64_t) w * h);
    return real(d, dr, gc, im, sx, sy, dx, dy, w, h);
}
Bool XShmPutImage(Display *d, Drawable dr, GC gc, XImage *im, int sx, int sy, int dx, int dy, unsigned int w,
                  unsigned int h, Bool send_event) {
    NEXT(XShmPutImage);
    ADD(n_xshm, 1); ADD(xshm_pixels, (uint64_t) w * h);
    return real(d, dr, gc, im, sx, sy, dx, dy, w, h, send_event);
}

__attribute__((destructor)) static void dump(void) {
    const char *out = getenv("PRESENT_COUNT_OUT");
    char exe[256] = "?";
    ssize_t n;
    FILE *f;
    uint32_t i, k;
    if (!out || !*out)
        return;
    if ((n = readlink("/proc/self/exe", exe, sizeof(exe) - 1)) > 0)
        exe[n] = 0;
    if (!(f = fopen(out, "a")))
        return;
    fprintf(f, "{\"shim\":\"present_count/1\",\"pid\":%d,\"exe\":\"%s\","
               "\"dri3_open\":%llu,\"pixmap_from_buffer\":%llu,\"pixmap_from_buffers\":%llu,"
               "\"present_pixmap\":%llu,\"present_pixmap_synced\":%llu,"
               "\"shm_put_image\":%llu,\"shm_pixels\":%llu,\"put_image\":%llu,\"put_bytes\":%llu,"
               "\"x_put_image\":%llu,\"x_put_pixels\":%llu,\"x_shm_put_image\":%llu,\"x_shm_pixels\":%llu,"
               "\"shm_create_pixmap\":%llu,\"shm_attach\":%llu,"
               "\"imports_total\":%u,\"imports\":[",
            (int) getpid(), exe,
            (unsigned long long) n_open, (unsigned long long) n_pfb, (unsigned long long) n_pfbs,
            (unsigned long long) n_present, (unsigned long long) n_present_synced,
            (unsigned long long) n_shm, (unsigned long long) shm_pixels, (unsigned long long) n_put,
            (unsigned long long) put_bytes, (unsigned long long) n_xput, (unsigned long long) xput_pixels,
            (unsigned long long) n_xshm, (unsigned long long) xshm_pixels,
            (unsigned long long) n_shm_pixmap, (unsigned long long) n_shm_attach, nrec);
    k = nrec < MAXREC ? nrec : MAXREC;
    for (i = 0; i < k; i++) {
        char mod[24] = "null";   // DRI3 1.0 PixmapFromBuffer carries no modifier
        if (recs[i].modifier != UINT64_MAX)
            snprintf(mod, sizeof(mod), "%llu", (unsigned long long) recs[i].modifier);
        fprintf(f, "%s{\"multi\":%d,\"w\":%u,\"h\":%u,\"stride\":%u,\"depth\":%u,\"bpp\":%u,\"nbuf\":%u,"
                   "\"modifier\":%s}",
                i ? "," : "", recs[i].multi, recs[i].w, recs[i].h, recs[i].stride, recs[i].depth, recs[i].bpp,
                recs[i].nbuf, mod);
    }
    fprintf(f, "]}\n");
    fclose(f);
}
