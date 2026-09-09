#include "egl_dispatch.h"

#include <string.h>

PFNEGLCREATEIMAGEKHRPROC lorieEglCreateImageKHR = NULL;
PFNEGLDESTROYIMAGEKHRPROC lorieEglDestroyImageKHR = NULL;
PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC lorieEglGetNativeClientBufferANDROID = NULL;
PFNEGLCREATESYNCKHRPROC lorieEglCreateSyncKHR = NULL;
PFNEGLDESTROYSYNCKHRPROC lorieEglDestroySyncKHR = NULL;
PFNEGLCLIENTWAITSYNCKHRPROC lorieEglClientWaitSyncKHR = NULL;
PFNGLEGLIMAGETARGETTEXTURE2DOESPROC lorieGlEGLImageTargetTexture2DOES = NULL;

static bool eglInitDone = false;
static bool glesInitDone = false;
static bool hasImage = false;
static bool hasNativeClientBuffer = false;
static bool hasFence = false;
static bool hasOesImage = false;

/* Exact token match inside a space-separated extension string. A substring
 * test would accept e.g. "EGL_KHR_fence_sync2" — wrong. */
static bool hasExtToken(const char *exts, const char *token) {
    size_t len;

    if (!exts || !token || !token[0])
        return false;
    len = strlen(token);
    while (*exts) {
        while (*exts == ' ')
            exts++;
        if (!*exts)
            break;
        if (strncmp(exts, token, len) == 0 && (exts[len] == ' ' || exts[len] == '\0'))
            return true;
        while (*exts && *exts != ' ')
            exts++;
    }
    return false;
}

void lorieEglDispatchInit(EGLDisplay dpy) {
    const char *exts;

    if (eglInitDone)
        return;
    if (dpy == EGL_NO_DISPLAY)
        return; /* too early: retry on the next call, do not latch */

    exts = eglQueryString(dpy, EGL_EXTENSIONS);
    if (!exts)
        return; /* display not usable yet: retry later, do not latch */

    lorieEglCreateImageKHR =
        (PFNEGLCREATEIMAGEKHRPROC) eglGetProcAddress("eglCreateImageKHR");
    lorieEglDestroyImageKHR =
        (PFNEGLDESTROYIMAGEKHRPROC) eglGetProcAddress("eglDestroyImageKHR");
    if (hasExtToken(exts, "EGL_ANDROID_get_native_client_buffer"))
        lorieEglGetNativeClientBufferANDROID =
            (PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC) eglGetProcAddress(
                "eglGetNativeClientBufferANDROID");
    lorieEglCreateSyncKHR =
        (PFNEGLCREATESYNCKHRPROC) eglGetProcAddress("eglCreateSyncKHR");
    lorieEglDestroySyncKHR =
        (PFNEGLDESTROYSYNCKHRPROC) eglGetProcAddress("eglDestroySyncKHR");
    lorieEglClientWaitSyncKHR =
        (PFNEGLCLIENTWAITSYNCKHRPROC) eglGetProcAddress("eglClientWaitSyncKHR");

    hasImage = lorieEglCreateImageKHR && lorieEglDestroyImageKHR &&
               hasExtToken(exts, "EGL_KHR_image_base");
    hasNativeClientBuffer = lorieEglGetNativeClientBufferANDROID != NULL;
    hasFence = lorieEglCreateSyncKHR && lorieEglDestroySyncKHR &&
               lorieEglClientWaitSyncKHR &&
               hasExtToken(exts, "EGL_KHR_fence_sync");
    eglInitDone = true;
}

void lorieGlesDispatchInit(void) {
    const char *exts;

    if (glesInitDone)
        return;
    if (eglGetCurrentContext() == EGL_NO_CONTEXT)
        return; /* no current context yet: retry later, do not latch */

    lorieGlEGLImageTargetTexture2DOES =
        (PFNGLEGLIMAGETARGETTEXTURE2DOESPROC) eglGetProcAddress(
            "glEGLImageTargetTexture2DOES");
    exts = (const char *) glGetString(GL_EXTENSIONS);
    hasOesImage = lorieGlEGLImageTargetTexture2DOES &&
                  hasExtToken(exts, "GL_OES_EGL_image");
    glesInitDone = true;
}

bool lorieEglHasImage(void) {
    return hasImage;
}

bool lorieEglHasNativeClientBuffer(void) {
    return hasNativeClientBuffer;
}

bool lorieEglHasFence(void) {
    return hasFence;
}

bool lorieGlesHasEglImage(void) {
    return hasOesImage;
}
