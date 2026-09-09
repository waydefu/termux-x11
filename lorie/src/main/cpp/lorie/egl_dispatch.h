#pragma once

/* S3-E1 typed EGL/GL dispatch table.
 *
 * Why this file exists: the NDK link stub does not export KHR/OES extension
 * entrypoints (Khronos: portable clients must not rely on static export of
 * extension functions). All KHR/OES calls therefore go through these typed
 * PFN globals, resolved once via eglGetProcAddress after EGL init (EGL side)
 * or after a context is current (GL side). A NULL entry is never dereferenced:
 * every consumer either runs behind a capability gate (fence path) or has an
 * explicit fail-closed fallback (image/OES paths).
 *
 * Per-process state (plain globals): the X server and renderer processes each
 * initialize their own table on their own EGL setup. Uninitialized entries are
 * NULL by construction.
 */

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

extern PFNEGLCREATEIMAGEKHRPROC lorieEglCreateImageKHR;
extern PFNEGLDESTROYIMAGEKHRPROC lorieEglDestroyImageKHR;
extern PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC lorieEglGetNativeClientBufferANDROID;
extern PFNEGLCREATESYNCKHRPROC lorieEglCreateSyncKHR;
extern PFNEGLDESTROYSYNCKHRPROC lorieEglDestroySyncKHR;
extern PFNEGLCLIENTWAITSYNCKHRPROC lorieEglClientWaitSyncKHR;
extern PFNGLEGLIMAGETARGETTEXTURE2DOESPROC lorieGlEGLImageTargetTexture2DOES;

/* Resolve the EGL-side entries for `dpy`. Requires an initialized display.
 * Safe to call repeatedly (second and later calls are no-ops). Entries stay
 * NULL when the display is unusable, the extension token is absent, or the
 * proc cannot be resolved. */
void lorieEglDispatchInit(EGLDisplay dpy);

/* Resolve the GL-side OES entry. Requires a current GL context in this thread.
 * Same no-op-on-repeat / NULL-on-failure contract. */
void lorieGlesDispatchInit(void);

/* Capability queries for the testCapabilities-style gates. */
bool lorieEglHasImage(void);   /* create+destroy Image procs + image capability */
bool lorieEglHasNativeClientBuffer(void); /* EGL_ANDROID_get_native_client_buffer */
bool lorieEglHasFence(void);   /* all three fence procs + EGL_KHR_fence_sync */
bool lorieGlesHasEglImage(void); /* OES proc + GL_OES_EGL_image capability */

#ifdef __cplusplus
}
#endif
