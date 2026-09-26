// gl_present - minimal GL client for GL-PRESENT-01 (mainline #3 step 1), built twice:
//   -DAPI_EGL  EGL (X11 platform) + GLES2: the path Chromium/Electron/WebGL take
//   -DAPI_GLX  GLX + desktop GL:           the path Blender and most X11 GL programs take
// Every frame is glClear (colour alternates) + swap with swap interval 0, so the GPU does almost
// nothing and what is left is the cost of handing the frame to the X server.
//   gl_present_{egl,glx} --size WxH --seconds S [--offscreen]
// --offscreen clears a WxH FBO texture and calls glFinish every frame instead of swapping:
// the "nothing is presented" control. The window still exists (same context setup). Both builds
// (GLX offscreen added in GL-PRESENT-01 amendment 1: FBO entry points come from glXGetProcAddress).
// Last stdout line: GL_PRESENT key=value ... ; exit 0 only when at least one frame completed.
//   cc -O2 -Wall -DAPI_EGL -o gl_present_egl gl_present.c -lEGL -lGLESv2 -lX11
//   cc -O2 -Wall -DAPI_GLX -o gl_present_glx gl_present.c -lGL -lX11
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#if defined(API_EGL)
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#define API "egl"
#elif defined(API_GLX)
#include <GL/gl.h>
#include <GL/glx.h>
#define API "glx"
#else
#error "build with -DAPI_EGL or -DAPI_GLX"
#endif

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
#define FAIL(...) do { printf("GL_PRESENT_FAIL " __VA_ARGS__); printf("\n"); exit(3); } while (0)

int main(int argc, char **argv) {
    unsigned W = 600, H = 600;
    double secs = 20;
    int offscreen = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--size") && i + 1 < argc) sscanf(argv[++i], "%ux%u", &W, &H);
        else if (!strcmp(argv[i], "--seconds") && i + 1 < argc) secs = atof(argv[++i]);
        else if (!strcmp(argv[i], "--offscreen")) offscreen = 1;
        else FAIL("bad_arg=%s", argv[i]);
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    Display *dpy = XOpenDisplay(NULL);
    if (!dpy) FAIL("call=XOpenDisplay");
    Window root = DefaultRootWindow(dpy);
    XSetWindowAttributes swa = { .event_mask = StructureNotifyMask | ExposureMask };
    Window win;

#if defined(API_EGL)
    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (PFNEGLGETPLATFORMDISPLAYEXTPROC) eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!gpd) FAIL("call=eglGetPlatformDisplayEXT");
    EGLDisplay ed = gpd(EGL_PLATFORM_X11_EXT, dpy, NULL);
    EGLint maj, min;
    if (ed == EGL_NO_DISPLAY || !eglInitialize(ed, &maj, &min)) FAIL("call=eglInitialize err=0x%x", eglGetError());
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_RED_SIZE, 8,
                    EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_NONE };
    EGLConfig cfg;
    EGLint ncfg = 0;
    if (!eglChooseConfig(ed, ca, &cfg, 1, &ncfg) || ncfg < 1) FAIL("call=eglChooseConfig");
    EGLint vid = 0;
    eglGetConfigAttrib(ed, cfg, EGL_NATIVE_VISUAL_ID, &vid);
    XVisualInfo vt = { .visualid = (VisualID) vid }, *vi;
    int nvi = 0;
    vi = XGetVisualInfo(dpy, VisualIDMask, &vt, &nvi);
    if (!vi) FAIL("call=XGetVisualInfo vid=%d", vid);
    swa.colormap = XCreateColormap(dpy, root, vi->visual, AllocNone);
    win = XCreateWindow(dpy, root, 0, 0, W, H, 0, vi->depth, InputOutput, vi->visual, CWColormap | CWEventMask, &swa);
    EGLint cxa[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext ctx = eglCreateContext(ed, cfg, EGL_NO_CONTEXT, cxa);
    if (ctx == EGL_NO_CONTEXT) FAIL("call=eglCreateContext err=0x%x", eglGetError());
    XMapWindow(dpy, win);
    EGLSurface surf = eglCreateWindowSurface(ed, cfg, (EGLNativeWindowType) win, NULL);
    if (surf == EGL_NO_SURFACE) FAIL("call=eglCreateWindowSurface err=0x%x", eglGetError());
    if (!eglMakeCurrent(ed, surf, surf, ctx)) FAIL("call=eglMakeCurrent err=0x%x", eglGetError());
    eglSwapInterval(ed, 0);
#else
    int va[] = { GLX_RGBA, GLX_DOUBLEBUFFER, GLX_RED_SIZE, 8, GLX_GREEN_SIZE, 8, GLX_BLUE_SIZE, 8, None };
    XVisualInfo *vi = glXChooseVisual(dpy, DefaultScreen(dpy), va);
    if (!vi) FAIL("call=glXChooseVisual");
    swa.colormap = XCreateColormap(dpy, root, vi->visual, AllocNone);
    win = XCreateWindow(dpy, root, 0, 0, W, H, 0, vi->depth, InputOutput, vi->visual, CWColormap | CWEventMask, &swa);
    GLXContext ctx = glXCreateContext(dpy, vi, NULL, True);
    if (!ctx) FAIL("call=glXCreateContext");
    XMapWindow(dpy, win);
    if (!glXMakeCurrent(dpy, win, ctx)) FAIL("call=glXMakeCurrent");
    const char *swapfn = "none";
    void (*sie)(Display *, GLXDrawable, int) = (void (*)(Display *, GLXDrawable, int)) glXGetProcAddress((const GLubyte *) "glXSwapIntervalEXT");
    int (*sim)(unsigned) = (int (*)(unsigned)) glXGetProcAddress((const GLubyte *) "glXSwapIntervalMESA");
    if (sie && strstr(glXQueryExtensionsString(dpy, DefaultScreen(dpy)), "GLX_EXT_swap_control")) { sie(dpy, win, 0); swapfn = "EXT"; }
    else if (sim) { sim(0); swapfn = "MESA"; }
    printf("GLX_SWAP_INTERVAL_FN %s\n", swapfn);
#endif
    for (;;) {   // wait until mapped so the first frames are not dropped by the server
        XEvent e;
        XNextEvent(dpy, &e);
        if (e.type == MapNotify) break;
    }
    printf("GL_RENDERER \"%s\" GL_VERSION \"%s\"\n", (const char *) glGetString(GL_RENDERER), (const char *) glGetString(GL_VERSION));

#if defined(API_GLX)
    // desktop libGL only exports GL 1.x entry points; the FBO ones come from glXGetProcAddress
    typedef void (*genfb_t)(GLsizei, GLuint *);
    typedef void (*bindfb_t)(GLenum, GLuint);
    typedef void (*fbtex_t)(GLenum, GLenum, GLenum, GLuint, GLint);
    typedef GLenum (*fbstat_t)(GLenum);
    genfb_t glGenFramebuffers = (genfb_t) glXGetProcAddress((const GLubyte *) "glGenFramebuffers");
    bindfb_t glBindFramebuffer = (bindfb_t) glXGetProcAddress((const GLubyte *) "glBindFramebuffer");
    fbtex_t glFramebufferTexture2D = (fbtex_t) glXGetProcAddress((const GLubyte *) "glFramebufferTexture2D");
    fbstat_t glCheckFramebufferStatus = (fbstat_t) glXGetProcAddress((const GLubyte *) "glCheckFramebufferStatus");
#ifndef GL_FRAMEBUFFER
#define GL_FRAMEBUFFER 0x8D40
#define GL_COLOR_ATTACHMENT0 0x8CE0
#define GL_FRAMEBUFFER_COMPLETE 0x8CD5
#endif
    if (offscreen && !(glGenFramebuffers && glBindFramebuffer && glFramebufferTexture2D && glCheckFramebufferStatus))
        FAIL("call=glXGetProcAddress_fbo");
#endif
    GLuint tex = 0, fbo = 0;
    if (offscreen) {
        glGenTextures(1, &tex);
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, W, H, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
        glGenFramebuffers(1, &fbo);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) FAIL("call=fbo_incomplete");
    }
    glViewport(0, 0, W, H);
    unsigned long long frames = 0;
    double t0 = now();
    while (now() - t0 < secs) {
        float s = (float) (frames % 2);
        glClearColor(s, 0.5f, 1.0f - s, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        if (offscreen) glFinish();
#if defined(API_EGL)
        else if (!eglSwapBuffers(ed, surf)) FAIL("call=eglSwapBuffers err=0x%x frames=%llu", eglGetError(), frames);
#else
        else glXSwapBuffers(dpy, win);
#endif
        while (XPending(dpy)) { XEvent e; XNextEvent(dpy, &e); }
        frames++;
    }
    glFinish();
    GLenum err = glGetError();
    double el = now() - t0;
    printf("GL_PRESENT api=%s frames=%llu secs=%.3f fps=%.1f size=%ux%u offscreen=%d gl_error=0x%x\n",
           API, frames, el, frames / el, W, H, offscreen, err);
#if defined(API_EGL)
    eglMakeCurrent(ed, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglTerminate(ed);
#else
    glXMakeCurrent(dpy, None, NULL);
    glXDestroyContext(dpy, ctx);
#endif
    XCloseDisplay(dpy);
    return frames > 0 ? 0 : 4;
}
