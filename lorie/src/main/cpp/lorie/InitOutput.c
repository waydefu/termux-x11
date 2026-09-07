#pragma clang diagnostic ignored "-Wunknown-pragmas"
#pragma clang diagnostic ignored "-Wstrict-prototypes"
#pragma ide diagnostic ignored "cppcoreguidelines-narrowing-conversions"
#pragma ide diagnostic ignored "cert-err34-c"
#pragma ide diagnostic ignored "ConstantConditionsOC"
#pragma ide diagnostic ignored "ConstantFunctionResult"
#pragma ide diagnostic ignored "bugprone-integer-division"
#pragma clang diagnostic ignored "-Wmissing-noreturn"
#pragma clang diagnostic ignored "-Wformat-nonliteral"

#ifdef HAVE_DIX_CONFIG_H
#include <dix-config.h>
#endif

#include <sys/eventfd.h>
#include <sys/errno.h>
#include <sys/socket.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <libxcvt/libxcvt.h>
#include <X11/X.h>
#include <X11/Xmd.h>
#include <sys/wait.h>
#include <present.h>
#include <sys/mman.h>
#include <dri3.h>
#include <sys/stat.h>
#include <dlfcn.h>
#include <signal.h>
#include <sys/syscall.h>
#include <fcntl.h>
#include <ucontext.h>
#include "fb.h"
#include "mipointer.h"
#include "micmap.h"
#include "miline.h"
#include "shmint.h"
#include "present_priv.h"
#include "misyncshm.h"
#include "glxserver.h"
#include "glxutil.h"
#include "fbconfigs.h"
#include "inpututils.h"
#include "exa.h"
#include "picturestr.h"
#include "drm_fourcc.h"
#include <pixman.h>

#include "lorie.h"

#define DRM_FORMAT_MOD_LINEAR 0

extern void android_shmem_sysv_shm_force(uint8_t enable);

#define unused __attribute__((unused))
#define log(prio, ...) __android_log_print(ANDROID_LOG_ ## prio, "LorieNative", __VA_ARGS__)

extern DeviceIntPtr lorieMouse, lorieKeyboard;

#define CREATE_PIXMAP_USAGE_LORIEBUFFER_BACKED 5

struct vblank {
    struct xorg_list link;
    uint64_t id, msc;
};

static struct present_screen_info loriePresentInfo;
static dri3_screen_info_rec lorieDri3Info;
static ExaDriverRec lorieExa;

typedef struct {
    DamagePtr damage;
    OsTimerPtr fpsTimer;

    SetWindowPixmapProcPtr SetWindowPixmap;
    CloseScreenProcPtr CloseScreen;

    int eventFd, stateFd;

    struct lorie_shared_server_state* state;
    struct {
        Bool legacyDrawing;
        uint32_t width, height;
        char name[1024];
        uint32_t framerate;
    } root;

    Bool dri3;
    Bool gpuPresentDisabled;
    Bool gpuExaDisabled;

    uint64_t vblank_interval;
    struct xorg_list vblank_queue;
    uint64_t current_msc;

    uint64_t gpuCopySerialCounter;
    uint64_t rootGpuCopyPending;

    Bool xrenderProbeInstalled;
    CompositeProcPtr xrenderSavedComposite;
} lorieScreenInfo;

ScreenPtr pScreenPtr;
static lorieScreenInfo lorieScreen = {
        .stateFd = -1,
        .root.width = 1280,
        .root.height = 1024,
        .root.framerate = 30,
        .root.name = "screen",
        .dri3 = TRUE,
        .vblank_queue = { &lorieScreen.vblank_queue, &lorieScreen.vblank_queue },
}, *pvfb = &lorieScreen;
static char *xstartup = NULL;
static char **xstartupArgv = NULL;

// Owned by the activity process, handed to us over the connection socket. Points at a placeholder until
// the first connection so callers don't need a NULL check.
static pthread_cond_t rendererCondPlaceholder = PTHREAD_COND_INITIALIZER;
static pthread_cond_t* volatile rendererCond = &rendererCondPlaceholder;

typedef struct {
    LorieBuffer *buffer;
    bool flipped, wasLocked, imported;
    void *locked;
    void *mem;
} LoriePixmapPriv;

#define LORIE_PIXMAP_PRIV_FROM_PIXMAP(pixmap) (pixmap ? ((LoriePixmapPriv*) exaGetPixmapDriverPrivate(pixmap)) : NULL)
#define LORIE_BUFFER_FROM_PIXMAP(pixmap) (pixmap ? ((LoriePixmapPriv*) exaGetPixmapDriverPrivate(pixmap))->buffer : NULL)

#ifdef __ANDROID__
static uint32_t lorieP2b2HashRect(const uint8_t *base, int width, int height, int stride,
                                   int x, int y, int rectWidth, int rectHeight) {
    uint32_t hash = 2166136261u;
    int row, col;

    if (!base || width <= 0 || height <= 0 || stride < width ||
        x < 0 || y < 0 || x >= width || y >= height || rectWidth <= 0 || rectHeight <= 0)
        return 0;
    if (rectWidth > width - x)
        rectWidth = width - x;
    if (rectHeight > height - y)
        rectHeight = height - y;
    for (row = 0; row < rectHeight; row++) {
        const uint8_t *p = base + (size_t) (y + row) * (size_t) stride * 4 + (size_t) x * 4;
        for (col = 0; col < rectWidth * 4; col++) {
            hash ^= p[col];
            hash *= 16777619u;
        }
    }
    return hash;
}

static void lorieP2b2Stamp(const char *stage, PixmapPtr pixmap, LoriePixmapPriv *priv,
                           const LorieBuffer_Desc *desc, const void *base,
                           int requestedType, int srcX, int srcY, int width, int height) {
    const uint8_t *pixel = NULL;
    uint8_t first[8] = {0};
    uint32_t px = 0;
    uint32_t hash;
    int i;
    char msg[640];

    if (base && desc && srcX >= 0 && srcY >= 0 &&
        srcX < desc->width && srcY < desc->height) {
        pixel = (const uint8_t *) base +
                (size_t) srcY * (size_t) desc->stride * 4 + (size_t) srcX * 4;
        memcpy(&px, pixel, sizeof(px));
        memcpy(first, pixel, sizeof(first));
    }
    hash = lorieP2b2HashRect((const uint8_t *) base,
                             desc ? desc->width : 0, desc ? desc->height : 0,
                             desc ? desc->stride : 0, srcX, srcY, width, height);
    snprintf(msg, sizeof(msg),
             "R3 %s pix=%p priv=%p buf=%p type=%u reqType=%d w=%d h=%d stride=%d "
             "depth=%d bpp=%d devKind=%d devptr=%p data=%p locked=%p mem=%p ahb=%p "
             "req=%d,%d rect=%dx%d addr=%p px=%08x first=%02x%02x%02x%02x%02x%02x%02x%02x "
             "hash=%08x",
             stage, (void *) pixmap, (void *) priv, priv ? (void *) priv->buffer : NULL,
             desc ? desc->type : 0, requestedType,
             desc ? desc->width : 0, desc ? desc->height : 0, desc ? desc->stride : 0,
             pixmap ? pixmap->drawable.depth : 0,
             pixmap ? pixmap->drawable.bitsPerPixel : 0,
             pixmap ? pixmap->devKind : 0,
             pixmap ? pixmap->devPrivate.ptr : NULL,
             desc ? desc->data : NULL, priv ? priv->locked : NULL,
             priv ? priv->mem : NULL, desc ? (void *) desc->buffer : NULL,
             srcX, srcY, width, height, (void *) pixel, px,
             first[0], first[1], first[2], first[3], first[4], first[5], first[6], first[7],
             hash);
    p2a2_emit(msg);
}
#endif

static LorieBuffer *lorieEnsureGpuSampleable(PixmapPtr pixmap, int8_t type) {
    LoriePixmapPriv *priv = LORIE_PIXMAP_PRIV_FROM_PIXMAP(pixmap);
    const LorieBuffer_Desc *desc;
    if (!priv || !priv->buffer || priv->mem)
        return NULL;

    desc = LorieBuffer_description(priv->buffer);
    if (desc->type == LORIEBUFFER_REGULAR) {
        int8_t format = pixmap->drawable.depth >= 32
            ? AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM
            : AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM;
#ifdef __ANDROID__
        lorieP2b2Stamp("S0_REGULAR", pixmap, priv, desc, desc->data, type, 0, 0, 1, 1);
#endif
        LorieBuffer_convert(priv->buffer, type, format);
        if (desc->type != LORIEBUFFER_REGULAR) {
            // LorieBuffer_convert does not report status but it does not let the type change in the case of error.
            pScreenPtr->ModifyPixmapHeader(pixmap, 0, 0, 0, 0, desc->stride * 4, NULL);
            LorieBuffer_lock(priv->buffer, &priv->locked);
#ifdef __ANDROID__
            lorieP2b2Stamp("S2_AHB", pixmap, priv, desc, priv->locked, type, 0, 0, 1, 1);
#endif
        }
    }

    return desc->type == type ? priv->buffer : NULL;
}

static Bool lorieServerDebugEnabled = FALSE;

extern void xorg_backtrace(void);

static char p2a2AltStack[64 * 1024];
static int p2a3SnapFd = -1;

/* Async-signal-safe helpers: no malloc, snprintf, or logcat. */
static char *p2a3PutStr(char *p, char *e, const char *s) {
    while (s && *s && p < e)
        *p++ = *s++;
    return p;
}

static char *p2a3PutDec(char *p, char *e, long v) {
    char tmp[24];
    int n = 0;
    unsigned long u;

    if (v < 0) {
        if (p < e)
            *p++ = '-';
        u = (unsigned long) (-v);
    } else {
        u = (unsigned long) v;
    }
    if (u == 0)
        tmp[n++] = '0';
    while (u && n < (int) sizeof(tmp)) {
        tmp[n++] = (char) ('0' + (u % 10));
        u /= 10;
    }
    while (n && p < e)
        *p++ = tmp[--n];
    return p;
}

static char *p2a3PutHex(char *p, char *e, unsigned long v) {
    static const char H[] = "0123456789abcdef";
    int i;

    p = p2a3PutStr(p, e, "0x");
    for (i = (int) (sizeof(unsigned long) * 2) - 1; i >= 0 && p < e; i--)
        *p++ = H[(v >> (i * 4)) & 0xf];
    return p;
}

static void p2a3WriteLine(const char *buf, size_t n) {
    if (!buf || n == 0)
        return;
    (void) write(2, buf, n);
    (void) write(2, "\n", 1);
    if (p2a3SnapFd >= 0) {
        (void) write(p2a3SnapFd, buf, n);
        (void) write(p2a3SnapFd, "\n", 1);
        (void) fsync(p2a3SnapFd);
    }
}

static void p2a3CrashHandler(int signo, siginfo_t *si, void *uctx) {
    char buf[768];
    char *p = buf;
    char *e = buf + sizeof(buf) - 1;
    ucontext_t *uc = (ucontext_t *) uctx;
    unsigned long pc = 0, lr = 0, sp = 0, fp = 0;
    unsigned long x[9];
    int i;

    memset(x, 0, sizeof(x));
#if defined(__aarch64__)
    if (uc) {
        pc = (unsigned long) uc->uc_mcontext.pc;
        sp = (unsigned long) uc->uc_mcontext.sp;
        lr = (unsigned long) uc->uc_mcontext.regs[30];
        fp = (unsigned long) uc->uc_mcontext.regs[29];
        for (i = 0; i < 9; i++)
            x[i] = (unsigned long) uc->uc_mcontext.regs[i];
    }
#endif
    p = p2a3PutStr(p, e, "Uctx signo=");
    p = p2a3PutDec(p, e, signo);
    p = p2a3PutStr(p, e, " si_code=");
    p = p2a3PutDec(p, e, si ? si->si_code : -1);
    p = p2a3PutStr(p, e, " si_addr=");
    p = p2a3PutHex(p, e, (unsigned long) (si ? si->si_addr : 0));
    p = p2a3PutStr(p, e, " PC=");
    p = p2a3PutHex(p, e, pc);
    p = p2a3PutStr(p, e, " LR=");
    p = p2a3PutHex(p, e, lr);
    p = p2a3PutStr(p, e, " SP=");
    p = p2a3PutHex(p, e, sp);
    p = p2a3PutStr(p, e, " FP=");
    p = p2a3PutHex(p, e, fp);
    p = p2a3PutStr(p, e, " x0=");
    p = p2a3PutHex(p, e, x[0]);
    p = p2a3PutStr(p, e, " x1=");
    p = p2a3PutHex(p, e, x[1]);
    p = p2a3PutStr(p, e, " x2=");
    p = p2a3PutHex(p, e, x[2]);
    p = p2a3PutStr(p, e, " x3=");
    p = p2a3PutHex(p, e, x[3]);
    p = p2a3PutStr(p, e, " x4=");
    p = p2a3PutHex(p, e, x[4]);
    p = p2a3PutStr(p, e, " x5=");
    p = p2a3PutHex(p, e, x[5]);
    p = p2a3PutStr(p, e, " x6=");
    p = p2a3PutHex(p, e, x[6]);
    p = p2a3PutStr(p, e, " x7=");
    p = p2a3PutHex(p, e, x[7]);
    p = p2a3PutStr(p, e, " x8=");
    p = p2a3PutHex(p, e, x[8]);
    *p = 0;
    p2a3WriteLine(buf, (size_t) (p - buf));

    p = buf;
    p = p2a3PutStr(p, e, "Ssig signo=");
    p = p2a3PutDec(p, e, signo);
    p = p2a3PutStr(p, e, " code=");
    p = p2a3PutDec(p, e, si ? si->si_code : -1);
    p = p2a3PutStr(p, e, " addr=");
    p = p2a3PutHex(p, e, (unsigned long) (si ? si->si_addr : 0));
    p = p2a3PutStr(p, e, " (rt_sigaction+ucontext)");
    *p = 0;
    p2a3WriteLine(buf, (size_t) (p - buf));

    /* Auxiliary: not async-signal-safe. Uctx line above is the primary evidence. */
    xorg_backtrace();
    _exit(128 + signo);
}

static void p2a2InstallCrashProbe(void) {
    static int installed;
    stack_t ss;

    if (installed)
        return;
    installed = 1;

    p2a3SnapFd = open("/tmp/x11gpu-p2a3.snap", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0666);

    memset(&ss, 0, sizeof(ss));
    ss.ss_sp = p2a2AltStack;
    ss.ss_size = sizeof(p2a2AltStack);
    if (sigaltstack(&ss, NULL) != 0)
        log(ERROR, "P2-A.3 sigaltstack failed: %s", strerror(errno));

#if defined(__aarch64__) || defined(__x86_64__)
    {
        /* Kernel aarch64/x86_64 sigaction layout (not bionic's). */
        struct kernel_sigaction {
            void (*handler)(int, siginfo_t *, void *);
            unsigned long flags;
            void (*restorer)(void);
            unsigned long mask;
        } kact;

        memset(&kact, 0, sizeof(kact));
        kact.handler = p2a3CrashHandler;
        kact.flags = SA_SIGINFO | SA_ONSTACK;
        /* Kernel rt_sigaction bypasses ART libsigchain so Uctx/Ssig reach
         * stderr+snap before debuggerd wins the ~110ms attach race. */
        if (syscall(SYS_rt_sigaction, SIGSEGV, &kact, NULL, 8) != 0)
            log(ERROR, "P2-A.3 rt_sigaction SIGSEGV failed: %s", strerror(errno));
        if (syscall(SYS_rt_sigaction, SIGBUS, &kact, NULL, 8) != 0)
            log(ERROR, "P2-A.3 rt_sigaction SIGBUS failed: %s", strerror(errno));
    }
#else
    {
        struct sigaction act;

        memset(&act, 0, sizeof(act));
        act.sa_sigaction = p2a3CrashHandler;
        act.sa_flags = SA_SIGINFO | SA_ONSTACK;
        sigemptyset(&act.sa_mask);
        if (sigaction(SIGSEGV, &act, NULL) != 0)
            log(ERROR, "P2-A.3 sigaction SIGSEGV failed: %s", strerror(errno));
        if (sigaction(SIGBUS, &act, NULL) != 0)
            log(ERROR, "P2-A.3 sigaction SIGBUS failed: %s", strerror(errno));
    }
#endif
    log(INFO, "P2-A.3 diagnostic: D0-D3/E0/E1/Uctx; observe-only; GWP-ASan miss does not exclude heap overflow");
}

void OsVendorInit(void) {
    pthread_mutexattr_t mutex_attr;

    p2a2InstallCrashProbe();

    if (lorieScreen.stateFd != -1) // already initialized
        return;

    lorieServerDebugEnabled = getenv("TERMUX_X11_DEBUG") != NULL;

    if (-1 == (lorieScreen.stateFd = LorieBuffer_createRegion("xserver", sizeof(*lorieScreen.state)))) {
        dprintf(2, "FATAL: Failed to allocate server state.\n");
        _exit(1);
    }

    if (!(lorieScreen.state = mmap(NULL, sizeof(*lorieScreen.state), PROT_READ|PROT_WRITE, MAP_SHARED, lorieScreen.stateFd, 0))) {
        dprintf(2, "FATAL: Failed to map server state.\n");
        _exit(1);
    }

    pthread_mutexattr_init(&mutex_attr);
    pthread_mutexattr_setpshared(&mutex_attr, PTHREAD_PROCESS_SHARED);
    pthread_mutexattr_settype(&mutex_attr, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&lorieScreen.state->lock, &mutex_attr);
    pthread_mutex_init(&lorieScreen.state->cursor.lock, &mutex_attr);
}

// Queued from handleLorieEvents (input thread) to run on the main thread, i.e. the same thread that
// signals rendererCond from lorieRedraw/lorieMoveCursor - so the swap and the unmap below can never race
// a signal.
static Bool lorieSetRendererWakeupCondWorkProc(__unused ClientPtr client, void* closure) {
    int fd = (int) (intptr_t) closure;
    pthread_cond_t* newCond = mmap(NULL, sizeof(pthread_cond_t), PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd); // mmap already keeps the region alive.
    if (newCond == MAP_FAILED) {
        log(ERROR, "Failed to map renderer wakeup cond var, keeping the old one");
        return TRUE;
    }

    pthread_cond_t* old = rendererCond;
    rendererCond = newCond;
    pthread_cond_signal(newCond); // in case a signal was sent to `old` right before this swap

    if (old != &rendererCondPlaceholder)
        munmap(old, sizeof(pthread_cond_t));

    return TRUE;
}

void lorieSetRendererWakeupCond(int fd) {
    QueueWorkProc(lorieSetRendererWakeupCondWorkProc, NULL, (void*) (intptr_t) fd);
    lorieWakeServer();
}

void lorieActivityConnected(void) {
    pvfb->state->drawRequested = pvfb->state->cursor.updated = true;
    lorieSendSharedServerState(pvfb->stateFd);
    lorieRegisterBuffer(LORIE_BUFFER_FROM_PIXMAP(pScreenPtr->devPrivate));
}

static LoriePixmapPriv* lorieRootWindowPixmapPriv(void) {
    void* devPriv = pScreenPtr ? pScreenPtr->devPrivate : NULL;
    return devPriv ? exaGetPixmapDriverPrivate(devPriv) : NULL;
}

static Bool TrueNoop() { return TRUE; }
static Bool FalseNoop() { return FALSE; }
static void VoidNoop() {}

void ddxGiveUp(unused enum ExitCode error) {
    lorieDumpFlightRecorder("ddxGiveUp");
    log(ERROR, "Server stopped (%d)", error);
    CloseWellKnownConnections();
    UnlockServer();
    exit(error);
}

static void* ddxReadyThread(unused void* cookie) {
    if ((xstartup || xstartupArgv) && serverGeneration == 1) {
        pid_t pid = fork();

        if (!pid) {
            char DISPLAY[16] = "";
            sprintf(DISPLAY, ":%s", display);
            setenv("DISPLAY", DISPLAY, 1);

#define INHERIT_VAR(v) char *v = getenv("XSTARTUP_" #v); if (v && strlen(v)) setenv(#v, v, 1); unsetenv("XSTARTUP_" #v);
            INHERIT_VAR(CLASSPATH)
            INHERIT_VAR(LD_LIBRARY_PATH)
            INHERIT_VAR(LD_PRELOAD)
#undef INHERIT_VAR

            if (xstartupArgv) {
                execvp(xstartupArgv[0], xstartupArgv);
                dprintf(2, "Failed to start command `%s`: %s\n", xstartupArgv[0], strerror(errno));
                abort();
            }

            execlp(xstartup, xstartup, NULL);
            execlp("sh", "sh", "-c", xstartup, NULL);
            dprintf(2, "Failed to start command `sh -c \"%s\"`: %s\n", xstartup, strerror(errno));
            abort();
        } else {
            int status;
            do {
                pid_t w = waitpid(pid, &status, 0);
                if (w == -1) {
                    perror("waitpid");
                    GiveUp(SIGKILL);
                }

                if (WIFEXITED(status)) {
                    printf("%d exited, status=%d\n", w, WEXITSTATUS(status));
                } else if (WIFSIGNALED(status)) {
                    printf("%d killed by signal %d\n", w, WTERMSIG(status));
                } else if (WIFSTOPPED(status)) {
                    printf("%d stopped by signal %d\n", w, WSTOPSIG(status));
                } else if (WIFCONTINUED(status)) {
                    printf("%d continued\n", w);
                }
            } while (!WIFEXITED(status) && !WIFSIGNALED(status));
            GiveUp(SIGINT);
        }
    }

    return NULL;
}

void drawSquare(int x, int y, int l, uint32_t color, uint32_t stride, uint32_t* pixels) {
    for (int i=0; i<l; i++) for (int j=0; j<l; j++)
        pixels[(j+y)*stride + x + i] = color;
}

Bool drawSquares() {
    LoriePixmapPriv* priv = lorieRootWindowPixmapPriv();
    uint32_t* pixels = !priv ? NULL : priv->locked;
    if (pixels) {
        const LorieBuffer_Desc *d = LorieBuffer_description(priv->buffer);
        int l = min(d->width, d->height) / 4, x = (d->width - l)/2, y = (d->height - l)/2;

        drawSquare(x - l/3, y - l/3, l, 0x00FF0000, d->stride, pixels);
        drawSquare(x, y, l, 0x0000FF00, d->stride, pixels);
        drawSquare(x + l/3, y + l/3, l, 0x000000FF, d->stride, pixels);
    }

    return FALSE;
}

void ddxReady(void) {
    lorieInstallFlightRecorder();
    CursorVisible = TRUE;
    pScreenPtr->DisplayCursor(lorieMouse, pScreenPtr, rootCursor);
    if (NoListenAll)
        return;
    if (!xstartupArgv) {
        if (xstartup && !strlen(xstartup)) // allow overriding $TERMUX_X11_XSTARTUP with empty xstartup arg
            return;
        if (!xstartup || !strlen(xstartup))
            xstartup = getenv("TERMUX_X11_XSTARTUP");
        if (!xstartup || !strlen(xstartup))
            return;
    }

    pthread_t t;
    pthread_create(&t, NULL, ddxReadyThread, NULL);
}

void OsVendorFatalError(unused const char *f, unused va_list args) {
    lorieDumpFlightRecorder("OsVendorFatalError");
    log(ERROR, f, args);
}

#if defined(DDXBEFORERESET)
void ddxBeforeReset(void) {}
#endif

#if INPUTTHREAD
/** This function is called in Xserver/os/inputthread.c when starting
    the input thread. */
void ddxInputThreadInit(void) {}
#endif

void ddxUseMsg(void) {
    ErrorF("-xstartup \"command\"\n");
    ErrorF("-- command args...     start `command` after server startup\n");
    ErrorF("-legacy-drawing        use legacy drawing, without using AHardwareBuffers\n");
    ErrorF("-force-bgra            force flipping colours (RGBA->BGRA)\n");
    ErrorF("-disable-dri3          disabling DRI3 support (to let lavapipe work)\n");
    ErrorF("-force-sysvshm         force using SysV shm syscalls\n");
    ErrorF("-check-drawing         run server only able to draw some test image (for testing if rendering root window works or not),\n");
    ErrorF("-disable-gpu-present   disable offloading Present copies to the GPU, always use the CPU path\n");
    ErrorF("-disable-gpu-exa       disable EXA GPU Copy/Solid/Composite, always use software fallback\n");
}

int ddxProcessArgument(unused int argc, unused char *argv[], unused int i) {
    if (strcmp(argv[i], "-xstartup") == 0) {  /* -xstartup "command" */
        CHECK_FOR_REQUIRED_ARGUMENTS(1);
        if (xstartupArgv) {
            UseMsg();
            FatalError("-xstartup and -- are mutually exclusive\n");
        }
        xstartup = argv[++i];
        return 2;
    }

    if (strcmp(argv[i], "--") == 0) {  /* -- command args...: everything after goes verbatim into xstartupArgv */
        CHECK_FOR_REQUIRED_ARGUMENTS(1);
        if (xstartup) {
            UseMsg();
            FatalError("-xstartup and -- are mutually exclusive\n");
        }
        int n = argc - i - 1;
        xstartupArgv = calloc(n + 1, sizeof(char*)); /* argv passed to us isn't guaranteed NULL-terminated past argc */
        memcpy(xstartupArgv, &argv[i + 1], n * sizeof(char*));
        return argc - i;
    }

    if (strcmp(argv[i], "-legacy-drawing") == 0) {
        pvfb->root.legacyDrawing = TRUE;
        return 1;
    }

    if (strcmp(argv[i], "-force-bgra") == 0)
        return 1;

    if (strcmp(argv[i], "-disable-dri3") == 0) {
        pvfb->dri3 = FALSE;
        return 1;
    }

    if (strcmp(argv[i], "-force-sysvshm") == 0) {
        android_shmem_sysv_shm_force(1);
        return 1;
    }

    if (strcmp(argv[i], "-check-drawing") == 0) {
        NoListenAll = TRUE;
        QueueWorkProc(drawSquares, NULL, NULL);
        return 1;
    }

    if (strcmp(argv[i], "-disable-gpu-present") == 0) {
        pvfb->gpuPresentDisabled = TRUE;
        return 1;
    }

    if (strcmp(argv[i], "-disable-gpu-exa") == 0) {
        pvfb->gpuExaDisabled = TRUE;
        return 1;
    }

    return 0;
}

static RRModePtr lorieCvt(int width, int height, int framerate) {
    struct libxcvt_mode_info *info;
    char name[128];
    xRRModeInfo modeinfo = {0};
    RRModePtr mode;

    info = libxcvt_gen_mode_info(width, height, framerate, 0, 0);

    snprintf(name, sizeof name, "%dx%d", info->hdisplay, info->vdisplay);
    modeinfo.nameLength = strlen(name);
    modeinfo.width      = info->hdisplay;
    modeinfo.height     = info->vdisplay;
    modeinfo.dotClock   = info->dot_clock * 1000.0;
    modeinfo.hSyncStart = info->hsync_start;
    modeinfo.hSyncEnd   = info->hsync_end;
    modeinfo.hTotal     = info->htotal;
    modeinfo.vSyncStart = info->vsync_start;
    modeinfo.vSyncEnd   = info->vsync_end;
    modeinfo.vTotal     = info->vtotal;
    modeinfo.modeFlags  = info->mode_flags;

    mode = RRModeGet(&modeinfo, name);
    free(info);
    return mode;
}

static void lorieMoveCursor(unused DeviceIntPtr pDev, unused ScreenPtr pScr, int x, int y) {
    pvfb->state->cursor.x = x;
    pvfb->state->cursor.y = y;
    pvfb->state->cursor.moved = TRUE;
    // No need to explicitly lock the mutex, it will cause waiting for rendering to be finished.
    // We are simply signaling the renderer in the case if it sleeps.
    pthread_cond_signal(rendererCond);
}

static void lorieConvertCursor(CursorPtr pCurs, uint32_t *data) {
    CursorBitsPtr bits = pCurs->bits;
    if (bits->argb) {
        for (int i = 0; i < bits->width * bits->height; i++) {
            /* Convert bgra to rgba */
            CARD32 p = bits->argb[i];
            data[i] = (p & 0xFF000000) | ((p & 0x00FF0000) >> 16) | (p & 0x0000FF00) | ((p & 0x000000FF) << 16);
        }
    } else {
        uint32_t d, fg, bg, *p;
        int x, y, stride, i, bit;

        p = data;
        fg = ((pCurs->foreBlue & 0xff00) << 8) | (pCurs->foreGreen & 0xff00) | (pCurs->foreRed >> 8);
        bg = ((pCurs->backBlue & 0xff00) << 8) | (pCurs->backGreen & 0xff00) | (pCurs->backRed >> 8);
        stride = BitmapBytePad(bits->width);
        for (y = 0; y < bits->height; y++)
            for (x = 0; x < bits->width; x++) {
                i = y * stride + x / 8;
                bit = 1 << (x & 7);
                d = (bits->source[i] & bit) ? fg : bg;
                d = (bits->mask[i] & bit) ? d | 0xff000000 : 0x00000000;
                *p++ = d;
            }
    }
}

static void lorieSetCursor(unused DeviceIntPtr pDev, unused ScreenPtr pScr, CursorPtr pCurs, int x0, int y0) {
    CursorBitsPtr bits = pCurs ? pCurs->bits : NULL;
    if (pCurs && (pCurs->bits->width >= 512 || pCurs->bits->height >= 512))
        // We do not have enough memory allocated for such a big cursor, let's display default "X" cursor
        pCurs = rootCursor;

    lorie_mutex_lock(&pvfb->state->cursor.lock, &pvfb->state->cursor.lockingPid);
    if (pCurs && bits) {
        pvfb->state->cursor.xhot = bits->xhot;
        pvfb->state->cursor.yhot = bits->yhot;
        pvfb->state->cursor.width = bits->width;
        pvfb->state->cursor.height = bits->height;
        lorieConvertCursor(pCurs, pvfb->state->cursor.bits);
    } else {
        pvfb->state->cursor.xhot = pvfb->state->cursor.yhot = 0;
        pvfb->state->cursor.width = pvfb->state->cursor.height = 0;
    }
    pvfb->state->cursor.updated = true;
    lorie_mutex_unlock(&pvfb->state->cursor.lock, &pvfb->state->cursor.lockingPid);

    lorieMoveCursor(NULL, NULL, x0, y0);
}

static miPointerSpriteFuncRec loriePointerSpriteFuncs = {
    .RealizeCursor = TrueNoop,
    .UnrealizeCursor = TrueNoop,
    .SetCursor = lorieSetCursor,
    .MoveCursor = lorieMoveCursor,
    .DeviceCursorInitialize = TrueNoop,
    .DeviceCursorCleanup = VoidNoop
};

static miPointerScreenFuncRec loriePointerCursorFuncs = {
    .CursorOffScreen = FalseNoop,
    .CrossScreen = VoidNoop,
    .WarpCursor = miPointerWarpCursor
};

static void loriePerformVblanks(void);

static Bool lorieRedraw(__unused ClientPtr pClient, __unused void *closure) {
    int status, nonEmpty;
    LoriePixmapPriv* priv;
    PixmapPtr root = pScreenPtr && pScreenPtr->root ? pScreenPtr->GetWindowPixmap(pScreenPtr->root) : NULL;

    pvfb->current_msc++;
    loriePerformVblanks();

    pvfb->state->waitForNextFrame = false;

    if (!lorieConnectionAlive() || !pvfb->state->surfaceAvailable)
        return TRUE;

    nonEmpty = RegionNotEmpty(DamageRegion(pvfb->damage));
    priv = root ? exaGetPixmapDriverPrivate(root) : NULL;

    if (!priv)
        // Impossible situation, but let's skip this step
        return TRUE;

    if (nonEmpty && priv->buffer) {
        // We should unlock and lock buffer in order to update texture content on some devices
        // In most cases AHardwareBuffer uses DMA memory which is shared between CPU and GPU
        // and this is not needed. But according to docs we should do it for any case.
        // Also according to AHardwareBuffer docs simultaneous reading in rendering thread and
        // locking for writing in other thread is fine.
        if (priv->locked) {
            LorieBuffer_unlock(priv->buffer);
            status = LorieBuffer_lock(priv->buffer, &priv->locked);
            if (status)
                FatalError("Failed to lock the surface: %d\n", status);
        }

        DamageEmpty(pvfb->damage);
        pvfb->state->drawRequested = TRUE;
    }

    if (pvfb->state->drawRequested || pvfb->state->cursor.moved || pvfb->state->cursor.updated) {
        pvfb->state->rootWindowTextureID = LorieBuffer_description(priv->buffer)->id;

        // Sending signal about pending root window changes to renderer thread.
        // We do not explicitly lock the pvfb->state->lock here because we do not want to wait
        // for all drawing operations to be finished.
        // Renderer thread will check the `drawRequested` flag right before going to sleep.
        pthread_cond_signal(rendererCond);
    }

    return TRUE;
}

static uint64_t gpuCopyAttempts = 0, gpuCopyOffloads = 0;
static uint64_t exaCopyAttempts = 0, exaCopyOffloads = 0, exaCopyFallbackRects = 0;
static uint64_t exaSolidPrepare = 0, exaSolidGpu = 0, exaSolidFallback = 0, exaSolidRects = 0, exaSolidCpuRects = 0;
static uint64_t exaCompCheckTrue = 0, exaCompCheckFalse = 0, exaCompPrepareTrue = 0, exaCompPrepareFalse = 0;
static uint64_t exaCompGpuRects = 0, exaCompCpuRects = 0, exaCompDone = 0;

#define XRENDER_HIST_SLOTS 64
typedef struct {
    uint8_t op, flags, filter, wbucket;
    uint32_t srcFmt, maskFmt, dstFmt;
    uint32_t count;
    uint16_t lastW, lastH;
} XRenderHistEnt;
static XRenderHistEnt xrenderHist[XRENDER_HIST_SLOTS];
static uint64_t xrenderOps = 0;
static int p2a4ProbeDepth;
static int p2a4ProbeMaxDepth;
static uint64_t p2a4ProbeEnter;
static uint64_t p2a4ProbeReturn;

static uint8_t xrenderSizeBucket(int w, int h) {
    int m = w > h ? w : h;
    if (m <= 1) return 0;
    if (m <= 16) return 1;
    if (m <= 64) return 2;
    if (m <= 256) return 3;
    if (m <= 1024) return 4;
    return 5;
}

static void xrenderHistRecord(int op, PicturePtr src, PicturePtr mask, PicturePtr dst, int w, int h) {
    XRenderHistEnt key;
    int i, freeSlot = -1;
    memset(&key, 0, sizeof(key));
    key.op = (uint8_t) op;
    key.srcFmt = src ? src->format : 0;
    key.dstFmt = dst ? dst->format : 0;
    key.maskFmt = mask ? mask->format : 0;
    key.filter = src ? (uint8_t) src->filter : 0;
    if (mask) key.flags |= 1;
    if (src && src->transform) key.flags |= 2;
    if (src && src->componentAlpha) key.flags |= 4;
    if (mask && mask->componentAlpha) key.flags |= 8;
    if (src && src->repeat) key.flags |= 16;
    if (mask && mask->repeat) key.flags |= 32;
    key.wbucket = xrenderSizeBucket(w, h);
    xrenderOps++;
    for (i = 0; i < XRENDER_HIST_SLOTS; i++) {
        if (xrenderHist[i].count == 0) {
            if (freeSlot < 0) freeSlot = i;
            continue;
        }
        if (xrenderHist[i].op == key.op && xrenderHist[i].flags == key.flags &&
            xrenderHist[i].filter == key.filter && xrenderHist[i].wbucket == key.wbucket &&
            xrenderHist[i].srcFmt == key.srcFmt && xrenderHist[i].maskFmt == key.maskFmt &&
            xrenderHist[i].dstFmt == key.dstFmt) {
            xrenderHist[i].count++;
            xrenderHist[i].lastW = (uint16_t) w;
            xrenderHist[i].lastH = (uint16_t) h;
            return;
        }
    }
    if (freeSlot >= 0) {
        key.count = 1;
        key.lastW = (uint16_t) w;
        key.lastH = (uint16_t) h;
        xrenderHist[freeSlot] = key;
    }
}

static void lorieCompositeProbe(CARD8 op, PicturePtr pSrc, PicturePtr pMask, PicturePtr pDst,
                                INT16 xSrc, INT16 ySrc, INT16 xMask, INT16 yMask,
                                INT16 xDst, INT16 yDst, CARD16 width, CARD16 height) {
    CompositeProcPtr saved = pvfb->xrenderSavedComposite;
#ifdef __ANDROID__
    {
        char msg[256];

        p2a4ProbeEnter++;
        p2a4ProbeDepth++;
        if (p2a4ProbeDepth > p2a4ProbeMaxDepth)
            p2a4ProbeMaxDepth = p2a4ProbeDepth;
        snprintf(msg, sizeof(msg),
                 "Probe ENTER depth=%d max=%d enter=%llu return=%llu saved=%p",
                 p2a4ProbeDepth, p2a4ProbeMaxDepth,
                 (unsigned long long) p2a4ProbeEnter,
                 (unsigned long long) p2a4ProbeReturn, (void *) saved);
        p2a2_emit(msg);
    }
#endif
    xrenderHistRecord(op, pSrc, pMask, pDst, width, height);
    if (saved)
        saved(op, pSrc, pMask, pDst, xSrc, ySrc, xMask, yMask, xDst, yDst, width, height);
#ifdef __ANDROID__
    {
        char msg[256];

        snprintf(msg, sizeof(msg),
                 "Probe RETURN depth=%d enter=%llu return=%llu",
                 p2a4ProbeDepth,
                 (unsigned long long) p2a4ProbeEnter,
                 (unsigned long long) (p2a4ProbeReturn + 1));
        p2a2_emit(msg);
        p2a4ProbeReturn++;
        if (p2a4ProbeDepth > 0)
            p2a4ProbeDepth--;
    }
#endif
}

static void lorieInstallXRenderProbe(ScreenPtr pScreen) {
    PictureScreenPtr ps = GetPictureScreenIfSet(pScreen);
#ifdef __ANDROID__
    char msg[256];
#endif

    if (!ps) {
        log(ERROR, "XRender probe: PictureScreen missing");
        return;
    }
    /* Idempotent on per-screen state, not ps->Composite == probe.
     * Damage wrap changes ps->Composite after the first install; comparing
     * the live hook would re-enter and create a probe↔damage cycle. */
    if (pvfb->xrenderProbeInstalled) {
#ifdef __ANDROID__
        snprintf(msg, sizeof(msg),
                 "InstallProbe installed=1 SKIP saved=%p current=%p",
                 (void *) pvfb->xrenderSavedComposite, (void *) ps->Composite);
        p2a2_emit(msg);
#endif
        log(INFO, "XRender histogram probe already installed; skip");
        return;
    }
#ifdef __ANDROID__
    snprintf(msg, sizeof(msg),
             "InstallProbe installed=0 saved=%p new=lorieCompositeProbe current=%p",
             (void *) ps->Composite, (void *) ps->Composite);
    p2a2_emit(msg);
#endif
    pvfb->xrenderSavedComposite = ps->Composite;
    ps->Composite = lorieCompositeProbe;
    pvfb->xrenderProbeInstalled = TRUE;
    log(INFO, "XRender histogram probe installed");
}

static CARD32 lorieFramecounter(unused OsTimerPtr timer, unused CARD32 time, unused void *arg) {
    if (pvfb->state->renderedFrames || gpuCopyAttempts || exaCopyAttempts || exaSolidPrepare || xrenderOps || exaCompCheckTrue || exaCompCheckFalse)
        log(INFO, "%d frames in 5.0 seconds = %.1f FPS, %llu/%llu present copies offloaded to GPU, %llu/%llu EXA copies offloaded (%llu CPU rects), exa_solid_prepare=%llu exa_solid_gpu=%llu exa_solid_fallback=%llu solid_rects=%llu cpu_solid_rects=%llu renderer_solid_submits=%llu renderer_solid_complete=%llu xrender_ops=%llu exa_comp_check=%llu/%llu prepare=%llu/%llu gpu_rects=%llu cpu_rects=%llu done=%llu",
            pvfb->state->renderedFrames, ((float) pvfb->state->renderedFrames) / 5,
            (unsigned long long) gpuCopyOffloads, (unsigned long long) gpuCopyAttempts,
            (unsigned long long) exaCopyOffloads, (unsigned long long) exaCopyAttempts,
            (unsigned long long) exaCopyFallbackRects,
            (unsigned long long) exaSolidPrepare, (unsigned long long) exaSolidGpu,
            (unsigned long long) exaSolidFallback, (unsigned long long) exaSolidRects,
            (unsigned long long) exaSolidCpuRects,
            (unsigned long long) pvfb->state->rendererSolidSubmits,
            (unsigned long long) pvfb->state->rendererSolidComplete,
            (unsigned long long) xrenderOps,
            (unsigned long long) exaCompCheckTrue, (unsigned long long) exaCompCheckFalse,
            (unsigned long long) exaCompPrepareTrue, (unsigned long long) exaCompPrepareFalse,
            (unsigned long long) exaCompGpuRects, (unsigned long long) exaCompCpuRects,
            (unsigned long long) exaCompDone);
    if (xrenderOps) {
        int i, n;
        XRenderHistEnt top[8];
        memset(top, 0, sizeof(top));
        for (i = 0; i < XRENDER_HIST_SLOTS; i++) {
            if (!xrenderHist[i].count)
                continue;
            for (n = 0; n < 8; n++) {
                if (xrenderHist[i].count > top[n].count) {
                    memmove(&top[n + 1], &top[n], sizeof(XRenderHistEnt) * (7 - n));
                    top[n] = xrenderHist[i];
                    break;
                }
            }
        }
        for (n = 0; n < 8 && top[n].count; n++)
            log(INFO, "xrender hist[%d] count=%u op=%u src=%08x mask=%08x dst=%08x flags=0x%x filter=%u wbucket=%u last=%ux%u",
                n, top[n].count, top[n].op, top[n].srcFmt, top[n].maskFmt, top[n].dstFmt,
                top[n].flags, top[n].filter, top[n].wbucket, top[n].lastW, top[n].lastH);
    }
    pvfb->state->renderedFrames = 0;
    gpuCopyAttempts = gpuCopyOffloads = 0;
    exaCopyAttempts = exaCopyOffloads = exaCopyFallbackRects = 0;
    exaSolidPrepare = exaSolidGpu = exaSolidFallback = exaSolidRects = exaSolidCpuRects = 0;
    xrenderOps = 0;
    memset(xrenderHist, 0, sizeof(xrenderHist));
    exaCompCheckTrue = exaCompCheckFalse = exaCompPrepareTrue = exaCompPrepareFalse = 0;
    exaCompGpuRects = exaCompCpuRects = exaCompDone = 0;
    return 5000;
}

static Bool lorieCreateScreenResources(ScreenPtr pScreen) {
    pScreen->devPrivate = pScreen->CreatePixmap(pScreen, pScreen->width, pScreen->height, pScreen->rootDepth, CREATE_PIXMAP_USAGE_LORIEBUFFER_BACKED);

    pvfb->damage = DamageCreate(NULL, NULL, DamageReportNone, TRUE, pScreen, NULL);
    if (!pvfb->damage)
        FatalError("Couldn't setup damage\n");

    DamageRegister(&(*pScreen->GetScreenPixmap)(pScreen)->drawable, pvfb->damage);
    pvfb->fpsTimer = TimerSet(NULL, 0, 5000, lorieFramecounter, pScreen);

    lorieRegisterBuffer(LORIE_BUFFER_FROM_PIXMAP(pScreenPtr->devPrivate));

    return TRUE;
}

static Bool lorieCloseScreen(ScreenPtr pScreen) {
    PictureScreenPtr ps = GetPictureScreenIfSet(pScreen);

    if (pvfb->xrenderProbeInstalled && ps &&
        ps->Composite == lorieCompositeProbe && pvfb->xrenderSavedComposite)
        ps->Composite = pvfb->xrenderSavedComposite;
    pvfb->xrenderProbeInstalled = FALSE;
    pvfb->xrenderSavedComposite = NULL;

    pScreenPtr = NULL;
    pScreen->DestroyPixmap(pScreen->devPrivate);
    pScreen->devPrivate = NULL;
    pScreen->CloseScreen = pvfb->CloseScreen;
    return pScreen->CloseScreen(pScreen);
}

void lorieSetWindowPixmap(WindowPtr pWindow, PixmapPtr newPixmap) {
    bool isRoot = pWindow == pScreenPtr->root;
    PixmapPtr oldPixmap = isRoot ? pScreenPtr->GetWindowPixmap(pWindow) : NULL;
    LoriePixmapPriv *old, *new;
    if (isRoot) {
        old = LORIE_PIXMAP_PRIV_FROM_PIXMAP(oldPixmap);
        new = LORIE_PIXMAP_PRIV_FROM_PIXMAP(newPixmap);
        if (old && old->buffer && old->locked) {
            LorieBuffer_unlock(old->buffer);
            old->locked = NULL;
            old->wasLocked = false;
        }
        if (new && new->buffer && !new->locked) {
            LorieBuffer_lock(new->buffer, &new->locked);
            new->wasLocked = false;
        }
    }

    pScreenPtr->SetWindowPixmap = pvfb->SetWindowPixmap;
    (*pScreenPtr->SetWindowPixmap) (pWindow, newPixmap);
    pvfb->SetWindowPixmap = pScreenPtr->SetWindowPixmap;
    pScreenPtr->SetWindowPixmap = lorieSetWindowPixmap;
}

static int lorieSetPixmapVisitWindow(WindowPtr window, void *data) {
    ScreenPtr screen = window->drawable.pScreen;

    if (screen->GetWindowPixmap(window) == data) {
        screen->SetWindowPixmap(window, screen->GetScreenPixmap(screen));
        return WT_WALKCHILDREN;
    }

    return WT_DONTWALKCHILDREN;
}

static Bool lorieRRScreenSetSize(ScreenPtr pScreen, CARD16 width, CARD16 height, unused CARD32 mmWidth, unused CARD32 mmHeight) {
    PixmapPtr oldPixmap, newPixmap;
    BoxRec box = { 0, 0, width, height };

    // Drain all pending vblanks.
    loriePerformVblanks();

    // Restore root window pixmap.
    present_restore_screen_pixmap(pScreenPtr);

    SetRootClip(pScreen, ROOT_CLIP_NONE);

    pScreen->root->drawable.width = pvfb->root.width = pScreen->width = width;
    pScreen->root->drawable.height = pvfb->root.height = pScreen->height = height;
    pScreen->mmWidth = ((double) (width)) * 25.4 / monitorResolution;
    pScreen->mmHeight = ((double) (height)) * 25.4 / monitorResolution;

    oldPixmap = pScreen->GetScreenPixmap(pScreen);
    newPixmap = pScreen->CreatePixmap(pScreen, width, height, pScreen->rootDepth, CREATE_PIXMAP_USAGE_LORIEBUFFER_BACKED);
    pScreen->SetScreenPixmap(newPixmap);
    if (pvfb->damage) {
        DamageUnregister(pvfb->damage);
        DamageDestroy(pvfb->damage);
    }

    pvfb->damage = DamageCreate(NULL, NULL, DamageReportNone, TRUE, pScreen, NULL);
    if (!pvfb->damage)
        FatalError("Couldn't setup damage\n");

    DamageRegister(&newPixmap->drawable, pvfb->damage);

    if (oldPixmap) {
        GCPtr gc = GetScratchGC(newPixmap->drawable.depth, pScreen);
        if (gc) {
            ValidateGC(&newPixmap->drawable, gc);
            gc->ops->CopyArea(&oldPixmap->drawable, &newPixmap->drawable, gc, 0, 0, min(oldPixmap->drawable.width, newPixmap->drawable.width), min(oldPixmap->drawable.height, newPixmap->drawable.height), 0, 0);
            FreeScratchGC(gc);
        }
        TraverseTree(pScreen->root, lorieSetPixmapVisitWindow, oldPixmap);
        pScreen->DestroyPixmap(oldPixmap);
    }

    lorieRegisterBuffer(LORIE_BUFFER_FROM_PIXMAP(pScreenPtr->devPrivate));

    pScreen->ResizeWindow(pScreen->root, 0, 0, width, height, NULL);
    RegionReset(&pScreen->root->winSize, &box);

    SetRootClip(pScreen, ROOT_CLIP_FULL);

    RRScreenSizeNotify(pScreen);
    update_desktop_dimensions();
    pvfb->state->cursor.moved = TRUE;

    return TRUE;
}

static Bool lorieRRCrtcSet(unused ScreenPtr pScreen, RRCrtcPtr crtc, RRModePtr mode, int x, int y,
               Rotation rotation, int numOutput, RROutputPtr *outputs) {
    return (crtc && mode) ? RRCrtcNotify(crtc, mode, x, y, rotation, NULL, numOutput, outputs) : FALSE;
}

static Bool lorieRRGetInfo(unused ScreenPtr pScreen, Rotation *rotations) {
    *rotations = RR_Rotate_0;
    return TRUE;
}

static Bool lorieRandRInit(ScreenPtr pScreen) {
    rrScrPrivPtr pScrPriv;
    RROutputPtr output;
    RRCrtcPtr crtc;
    RRModePtr mode;

    if (!RRScreenInit(pScreen))
       return FALSE;

    pScrPriv = rrGetScrPriv(pScreen);
    pScrPriv->rrGetInfo = lorieRRGetInfo;
    pScrPriv->rrCrtcSet = lorieRRCrtcSet;
    pScrPriv->rrScreenSetSize = lorieRRScreenSetSize;

    RRScreenSetSizeRange(pScreen, 1, 1, 32767, 32767);

    if (FALSE
        || !(mode = lorieCvt(pScreen->width, pScreen->height, pvfb->root.framerate))
        || !(crtc = RRCrtcCreate(pScreen, NULL))
        || !RRCrtcGammaSetSize(crtc, 256)
        || !(output = RROutputCreate(pScreen, pvfb->root.name, sizeof(pvfb->root.name), NULL))
        || (output->nameLength = strlen(output->name), FalseNoop())
        || !RROutputSetClones(output, NULL, 0)
        || !RROutputSetModes(output, &mode, 1, 0)
        || !RROutputSetCrtcs(output, &crtc, 1)
        || !RROutputSetConnection(output, RR_Connected)
        || !RRCrtcNotify(crtc, mode, 0, 0, RR_Rotate_0, NULL, 1, &output))
        return FALSE;
    return TRUE;
}

void lorieWakeServer(void) {
    // Wake the server if it sleeps.
    eventfd_write(pvfb->eventFd, 1);
}

static void lorieWorkingQueueCallback(int fd, int __unused ready, void __unused *data) {
    // Nothing to do here. It is needed to interrupt ospoll_wait.
    eventfd_t dummy;
    eventfd_read(fd, &dummy);
}

void lorieChoreographerFrameCallback(__unused long t, AChoreographer* d) {
    AChoreographer_postFrameCallback(d, (AChoreographer_frameCallback) lorieChoreographerFrameCallback, d);
    if (pScreenPtr) {
        QueueWorkProc(lorieRedraw, NULL, NULL);
        lorieWakeServer();
    }
}

static Bool lorieScreenInit(ScreenPtr pScreen, unused int argc, unused char **argv) {
    static int eventFd = -1;
    pScreenPtr = pScreen;

    if (eventFd == -1)
        eventFd = eventfd(0, EFD_CLOEXEC);

    pvfb->eventFd = eventFd;
    SetNotifyFd(eventFd, lorieWorkingQueueCallback, X_NOTIFY_READ, NULL);

    miSetZeroLineBias(pScreen, 0);
    pScreen->blackPixel = 0;
    pScreen->whitePixel = 1;

    pvfb->vblank_interval = 1000000 / pvfb->root.framerate;

    if (FALSE
          || !miSetVisualTypesAndMasks(24, ((1 << TrueColor) | (1 << DirectColor)), 8, TrueColor, 0xFF0000, 0x00FF00, 0x0000FF)
          || !miSetPixmapDepths()
          || !fbScreenInit(pScreen, NULL, pvfb->root.width, pvfb->root.height, monitorResolution, monitorResolution, 0, 32)
          || !(pScreen->CreateScreenResources = lorieCreateScreenResources) // Simply replace unneeded function
          || !(!pvfb->dri3 || dri3_screen_init(pScreen, &lorieDri3Info))
          || !fbPictureInit(pScreen, 0, 0)
          || !exaDriverInit(pScreen, &lorieExa)
          || !lorieRandRInit(pScreen)
          || !miPointerInitialize(pScreen, &loriePointerSpriteFuncs, &loriePointerCursorFuncs, TRUE)
          || !fbCreateDefColormap(pScreen)
          || !present_screen_init(pScreen, &loriePresentInfo))
        return FALSE;

    lorieInstallXRenderProbe(pScreen);

    pvfb->CloseScreen = pScreen->CloseScreen;
    pvfb->SetWindowPixmap = pScreenPtr->SetWindowPixmap;
    pScreen->CloseScreen = lorieCloseScreen;
    pScreen->SetWindowPixmap = lorieSetWindowPixmap;

    ShmRegisterFbFuncs(pScreen);
    miSyncShmScreenInit(pScreen);

    return TRUE;
}                               /* end lorieScreenInit */

void lorieConfigureNotify(int width, int height, int framerate, size_t name_size, char* name) {
    ScreenPtr pScreen = pScreenPtr;
    RROutputPtr output = RRFirstOutput(pScreen);
    framerate = framerate ? framerate : 30;

    if (output && name) {
        // We should save this name in pvfb to make sure the name will be restored in the case if the server is being reset.
        memset(pvfb->root.name, 0, 1024);
        memset(output->name, 0, 1024);
        strncpy(pvfb->root.name, name, name_size < 1024 ? name_size : 1024);
        strncpy(output->name, name, name_size < 1024 ? name_size : 1024);
        output->name[1023] = '\0';
        output->nameLength = strlen(output->name);
    }

    if (output && width && height && (pScreen->width != width || pScreen->height != height || pvfb->root.framerate != framerate)) {
        CARD32 mmWidth, mmHeight;
        RRModePtr mode = lorieCvt(width, height, framerate);
        mmWidth = ((double) (mode->mode.width)) * 25.4 / monitorResolution;
        mmHeight = ((double) (mode->mode.width)) * 25.4 / monitorResolution;
        RROutputSetModes(output, &mode, 1, 0);
        RRCrtcNotify(RRFirstEnabledCrtc(pScreen), mode, 0, 0, RR_Rotate_0, NULL, 1, &output);
        RRScreenSizeSet(pScreen, mode->mode.width, mode->mode.height, mmWidth, mmHeight);

        log(VERBOSE, "New reported framerate is %d", framerate);
        pvfb->root.framerate = framerate;
        pvfb->vblank_interval = 1000000 / pvfb->root.framerate;
    }
}

void InitOutput(ScreenInfo * screen_info, int argc, char **argv) {
    int depths[] = { 1, 4, 8, 15, 16, 24, 32 };
    int bpp[] =    { 1, 8, 8, 16, 16, 32, 32 };
    int i;

    if (monitorResolution == 0)
        monitorResolution = 96;

    for(i = 0; i < ARRAY_SIZE(depths); i++) {
        screen_info->formats[i].depth = depths[i];
        screen_info->formats[i].bitsPerPixel = bpp[i];
        screen_info->formats[i].scanlinePad = BITMAP_SCANLINE_PAD;
    }

    screen_info->imageByteOrder = IMAGE_BYTE_ORDER;
    screen_info->bitmapScanlineUnit = BITMAP_SCANLINE_UNIT;
    screen_info->bitmapScanlinePad = BITMAP_SCANLINE_PAD;
    screen_info->bitmapBitOrder = BITMAP_BIT_ORDER;
    screen_info->numPixmapFormats = ARRAY_SIZE(depths);

    rendererTestCapabilities(&pvfb->root.legacyDrawing, &pvfb->gpuPresentDisabled);
    xorgGlxCreateVendor();
    lorieInitClipboard();

    if (-1 == AddScreen(lorieScreenInit, argc, argv)) {
        FatalError("Couldn't add screen\n");
    }
}

// This Present implementation mostly copies the one from `present/present_fake.c`
// The only difference is performing vblanks right before redrawing root window (in lorieRedraw) instead of using timers.
static RRCrtcPtr loriePresentGetCrtc(WindowPtr w) {
    return RRFirstEnabledCrtc(w->drawable.pScreen);
}

static int loriePresentGetUstMsc(__unused RRCrtcPtr crtc, uint64_t *ust, uint64_t *msc) {
    *ust = GetTimeInMicros();
    *msc = pvfb->current_msc;
    return Success;
}

static Bool loriePresentQueueVblank(__unused RRCrtcPtr crtc, uint64_t event_id, uint64_t msc) {
#pragma clang diagnostic push
#pragma ide diagnostic ignored "MemoryLeak" // it is not leaked, it is destroyed in lorieRedraw
    struct vblank* vblank = calloc (1, sizeof (*vblank));
    if (!vblank)
        return BadAlloc;

    *vblank = (struct vblank) { .id = event_id, .msc = msc };
    xorg_list_add(&vblank->link, &pvfb->vblank_queue);

    return Success;
#pragma clang diagnostic pop
}

static void loriePresentAbortVblank(__unused RRCrtcPtr crtc, uint64_t id, __unused uint64_t msc) {
    struct vblank *vblank, *tmp;

    xorg_list_for_each_entry_safe(vblank, tmp, &pvfb->vblank_queue, link) {
        if (vblank->id == id) {
            xorg_list_del(&vblank->link);
            free (vblank);
            break;
        }
    }
}

static void loriePerformVblanks(void) {
    struct vblank *vblank, *tmp;
    xorg_list_for_each_entry_safe(vblank, tmp, &pvfb->vblank_queue, link) {
        if (vblank->msc <= pvfb->current_msc) {
            present_event_notify(vblank->id, GetTimeInMicros(), pvfb->current_msc);
            xorg_list_del(&vblank->link);
            free (vblank);
        }
    }
}

// Whether the renderer currently has a surface to draw into (e.g. false while the activity is
// backgrounded). Unlike lorieConnectionAlive(), this can go false without the socket connection
// itself dropping - the renderer process/thread stays up, it just has nothing to render into.
bool lorieRendererAvailable(void) {
    return pvfb->state->surfaceAvailable;
}

static Bool lorieTryScheduleGpuBlit(PixmapPtr pixmap, PixmapPtr dst, RegionPtr update, int16_t x_off, int16_t y_off,
                                    uint8_t gpuOp, uint64_t *out_serial, void **out_dst_buffer);

// Tries to offload a Present "copy" operation (present_execute_copy) to the renderer's GPU
// context instead of doing a CPU CopyArea here. dst is whatever GetWindowPixmap(window) is - root
// for a plain window, or a Composite-redirected window's own backing pixmap. Returns FALSE
// (caller falls back to the regular CPU present_copy_region) whenever either buffer isn't
// GPU-sampleable, or the deferred copy queue is currently full.
Bool lorieTryScheduleGpuCopy(PixmapPtr pixmap, PixmapPtr dst, RegionPtr update, int16_t x_off, int16_t y_off,
                              uint64_t *out_serial, void **out_dst_buffer) {
    return lorieTryScheduleGpuBlit(pixmap, dst, update, x_off, y_off, LORIE_GPU_OP_COPY,
                                   out_serial, out_dst_buffer);
}

/* FD snapshot of a locked BGRA AHB src so the renderer can glTexSubImage2D as GLES
 * RGBA. Renderer-side AHardwareBuffer_lock of the live AHB fails while X holds it. */
static LorieBuffer *exaCompSrcUpload;

static LorieBuffer *lorieCloneBgraAhbToFd(PixmapPtr pixmap) {
    LoriePixmapPriv *priv = LORIE_PIXMAP_PRIV_FROM_PIXMAP(pixmap);
    const LorieBuffer_Desc *d, *dd;
    LorieBuffer *fd;
    uint8_t *src, *dst;
    int32_t y, w, h, ss, ds;

    if (!priv || !priv->buffer || !priv->locked)
        return NULL;
    d = LorieBuffer_description(priv->buffer);
    if (d->type != LORIEBUFFER_AHARDWAREBUFFER ||
        d->format != AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM)
        return NULL;
    fd = LorieBuffer_allocate(d->width, d->height, AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM, LORIEBUFFER_FD);
    if (!fd)
        return NULL;
    dd = LorieBuffer_description(fd);
    src = priv->locked;
    dst = dd->data;
    if (!dst) {
        LorieBuffer_release(fd);
        return NULL;
    }
    w = d->width;
    h = d->height;
    ss = d->stride;
    ds = dd->stride;
    if (w <= 0 || h <= 0 || ss < w || ds < w) {
        LorieBuffer_release(fd);
        return NULL;
    }
    for (y = 0; y < h; y++)
        memcpy(dst + (size_t) y * ds * 4, src + (size_t) y * ss * 4, (size_t) w * 4);
    return fd;
}

static Bool lorieTryScheduleGpuBlit(PixmapPtr pixmap, PixmapPtr dst, RegionPtr update, int16_t x_off, int16_t y_off,
                                    uint8_t gpuOp, uint64_t *out_serial, void **out_dst_buffer) {
    LorieBuffer *srcBuffer, *dstBuffer;
    LoriePixmapPriv *priv;
    const LorieBuffer_Desc *desc, *dstDesc;
    LorieGpuCopyEntry *entry;
    BoxRec fullBox;
    BoxPtr box;
    int numRects, i;
    uint32_t writeIndex, readIndex;

    if (pvfb->root.legacyDrawing || (gpuOp == LORIE_GPU_OP_COPY && pvfb->gpuPresentDisabled)) {
        if (gpuOp == LORIE_GPU_OP_COPY)
            if (gpuOp == LORIE_GPU_OP_COPY)
            gpuCopyAttempts++;
        return FALSE;
    }

    if (!lorieConnectionAlive() || !lorieRendererAvailable()) {
        // No renderer to drain the queue, so fall back to CPU copy.
        if (gpuOp == LORIE_GPU_OP_COPY)
            gpuCopyAttempts++;
        return FALSE;
    }

    if (!(srcBuffer = lorieEnsureGpuSampleable(pixmap, LORIEBUFFER_AHARDWAREBUFFER)) ||
        !(dstBuffer = lorieEnsureGpuSampleable(dst, LORIEBUFFER_AHARDWAREBUFFER))) {
        if (gpuOp == LORIE_GPU_OP_COPY)
            gpuCopyAttempts++;
        return FALSE;
    }
    priv = LORIE_PIXMAP_PRIV_FROM_PIXMAP(pixmap);
    desc = LorieBuffer_description(srcBuffer);
    dstDesc = LorieBuffer_description(dstBuffer);
    if (gpuOp == LORIE_GPU_OP_COMPOSITE && exaCompSrcUpload) {
        srcBuffer = exaCompSrcUpload;
        desc = LorieBuffer_description(srcBuffer);
    }

    if (update) {
        numRects = RegionNumRects(update);
        box = RegionRects(update);
    } else {
        fullBox = (BoxRec) { 0, 0, (short) pixmap->drawable.width, (short) pixmap->drawable.height };
        numRects = 1;
        box = &fullBox;
    }

    if (numRects <= 0 || numRects > LORIE_GPU_COPY_MAX_RECTS) {
        if (gpuOp == LORIE_GPU_OP_COPY)
            gpuCopyAttempts++;
        return FALSE;
    }

    writeIndex = pvfb->state->gpuCopyQueue.writeIndex;
    readIndex = pvfb->state->gpuCopyQueue.readIndex;
    if (writeIndex - readIndex >= LORIE_GPU_COPY_QUEUE_CAPACITY) {
        if (gpuOp == LORIE_GPU_OP_COPY)
            gpuCopyAttempts++;
        return FALSE;
    }

    // Make sure the renderer has (or will have) this texture. Idempotent if already registered.
    lorieRegisterBuffer(srcBuffer);
    // Extra reference: keeps the LorieBuffer struct alive on this side until lorieGpuCopyAck()
    // releases it, independently from the X pixmap's own lifetime.
    LorieBuffer_acquire(srcBuffer);
    // Read of the client's own pixmap (e.g. another client re-drawing into a buffer it already
    // handed to Present) could otherwise race this copy's GPU read of it.
    LorieBuffer_gpuCopyPendingInc(srcBuffer);
    // Root already has its own lifecycle (recreated on resize, kept alive by pScreenPtr->devPrivate)
    // - an extra reference here would outlive a resize and let the renderer keep finding a stale,
    // already-destroyed root buffer. Redirected-window destinations have no such guarantee, so they
    // still need registering and an extra reference.
    Bool dstIsRoot = dst == pScreenPtr->devPrivate;
    if (!dstIsRoot) {
        lorieRegisterBuffer(dstBuffer);
        LorieBuffer_acquire(dstBuffer);
        // Tracked so CPU reads of this window's pixmap (e.g. a compositor reading it back to
        // paint) only pay for the GPU lock (see lorieNeedsGpuLock) while a GPU write into it can
        // actually be in flight, instead of on every AHardwareBuffer-backed pixmap access.
        LorieBuffer_gpuCopyPendingInc(dstBuffer);
    } else {
        // Tracked so CPU reads of the screen pixmap only pay for the GPU lock (see
        // lorieNeedsGpuLock) while a GPU write into it can actually be in flight.
        pvfb->rootGpuCopyPending++;
    }
    *out_dst_buffer = dstIsRoot ? NULL : dstBuffer;

    entry = &pvfb->state->gpuCopyQueue.entries[writeIndex % LORIE_GPU_COPY_QUEUE_CAPACITY];
    entry->serial = ++pvfb->gpuCopySerialCounter;
    entry->srcBufferId = desc->id;
    entry->dstBufferId = dstDesc->id;
    entry->xOff = x_off;
    entry->yOff = y_off;
    entry->numRects = (uint16_t) numRects;
    entry->op = gpuOp;
    entry->dstIsRgba = 0;
    entry->color = 0;
    for (i = 0; i < numRects; i++)
        entry->rects[i] = (LorieGpuCopyRect) { box[i].x1, box[i].y1, box[i].x2, box[i].y2 };

    __sync_synchronize(); // publish entry contents before the renderer can see the new writeIndex
    pvfb->state->gpuCopyQueue.writeIndex = writeIndex + 1;
    pthread_cond_signal(rendererCond);

    *out_serial = entry->serial;
    if (gpuOp == LORIE_GPU_OP_COPY) {
        gpuCopyAttempts++;
        gpuCopyOffloads++;
    }
    return TRUE;
}

Bool lorieGpuCopyIsDone(uint64_t serial) {
    return pvfb->state->gpuCopyQueue.completedSerial >= serial;
}

void lorieGpuCopyAck(PixmapPtr pixmap, void *dst_buffer) {
    LoriePixmapPriv *priv = LORIE_PIXMAP_PRIV_FROM_PIXMAP(pixmap);
    if (priv && priv->buffer)
        LorieBuffer_gpuCopyPendingDec(priv->buffer);
    if (dst_buffer)
        LorieBuffer_gpuCopyPendingDec((LorieBuffer *) dst_buffer);
    else
        pvfb->rootGpuCopyPending--;

    if (priv && priv->buffer)
        LorieBuffer_release(priv->buffer);
    if (dst_buffer)
        LorieBuffer_release((LorieBuffer *) dst_buffer);
}

static Bool lorieGpuCopyWait(uint64_t serial, int timeout_ms) {
    struct timespec t0, now;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    while (!lorieGpuCopyIsDone(serial)) {
        if (!lorieConnectionAlive() || !lorieRendererAvailable())
            return FALSE;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed = (now.tv_sec - t0.tv_sec) * 1000L + (now.tv_nsec - t0.tv_nsec) / 1000000L;
        if (elapsed > timeout_ms)
            return FALSE;
        usleep(200);
    }
    return TRUE;
}

static struct {
    PixmapPtr src;
    PixmapPtr dst;
    uint64_t lastSerial;
    int scheduled;
    void *dstBuf;
} exaGpuCopy;

static struct {
    PixmapPtr dst;
    Pixel fg;
    uint64_t lastSerial;
    int scheduled;
    void *dstBuf;
    int nrepair;
    BoxRec repair[32];
    BoxRec repairUnion;
} exaGpuSolid;

Bool loriePrepareAccess(PixmapPtr pPix, int index);
void lorieFinishAccess(PixmapPtr pPix, int index);

static Bool lorieGpuExaDisabled(void) {
    const char *e;
    if (pvfb->gpuExaDisabled)
        return TRUE;
    e = getenv("TERMUX_X11_DISABLE_EXA_GPU");
    return e && e[0] && e[0] != '0';
}

static Bool lorieTryScheduleGpuSolid(PixmapPtr dst, int x1, int y1, int x2, int y2, Pixel fg,
                                     uint64_t *out_serial, void **out_dst_buffer) {
    LorieBuffer *dstBuffer;
    const LorieBuffer_Desc *dstDesc;
    LorieGpuCopyEntry *entry;
    uint32_t writeIndex, readIndex;
    Bool dstIsRoot;

    if (x2 <= x1 || y2 <= y1)
        return FALSE;
    if (pvfb->root.legacyDrawing || !lorieConnectionAlive() || !lorieRendererAvailable())
        return FALSE;
    if (!(dstBuffer = lorieEnsureGpuSampleable(dst, LORIEBUFFER_AHARDWAREBUFFER)))
        return FALSE;

    writeIndex = pvfb->state->gpuCopyQueue.writeIndex;
    readIndex = pvfb->state->gpuCopyQueue.readIndex;
    if (writeIndex - readIndex >= LORIE_GPU_COPY_QUEUE_CAPACITY)
        return FALSE;

    dstDesc = LorieBuffer_description(dstBuffer);
    dstIsRoot = dst == pScreenPtr->devPrivate;
    if (!dstIsRoot) {
        lorieRegisterBuffer(dstBuffer);
        LorieBuffer_acquire(dstBuffer);
        LorieBuffer_gpuCopyPendingInc(dstBuffer);
    } else {
        pvfb->rootGpuCopyPending++;
    }
    *out_dst_buffer = dstIsRoot ? NULL : dstBuffer;

    entry = &pvfb->state->gpuCopyQueue.entries[writeIndex % LORIE_GPU_COPY_QUEUE_CAPACITY];
    entry->serial = ++pvfb->gpuCopySerialCounter;
    entry->srcBufferId = 0;
    entry->dstBufferId = dstDesc->id;
    entry->xOff = 0;
    entry->yOff = 0;
    entry->numRects = 1;
    entry->op = LORIE_GPU_OP_SOLID;
    entry->dstIsRgba = LorieBuffer_isRgba(dstBuffer) ? 1 : 0;
    entry->color = (uint32_t) fg;
    entry->rects[0] = (LorieGpuCopyRect) { (int16_t) x1, (int16_t) y1, (int16_t) x2, (int16_t) y2 };

    __sync_synchronize();
    pvfb->state->gpuCopyQueue.writeIndex = writeIndex + 1;
    pthread_cond_signal(rendererCond);

    *out_serial = entry->serial;
    return TRUE;
}

static void lorieExaCpuSolidRect(PixmapPtr dst, int x1, int y1, int x2, int y2, Pixel fg) {
    int bpp, dstride, y, w;
    char *d;

    if (x2 <= x1 || y2 <= y1)
        return;
    if (!loriePrepareAccess(dst, EXA_PREPARE_DEST))
        return;
    d = dst->devPrivate.ptr;
    dstride = dst->devKind;
    bpp = dst->drawable.bitsPerPixel / 8;
    w = x2 - x1;
    if (d && bpp == 4) {
        for (y = y1; y < y2; y++) {
            uint32_t *row = (uint32_t *) (d + y * dstride + x1 * 4);
            int x;
            for (x = 0; x < w; x++)
                row[x] = (uint32_t) fg;
        }
    }
    lorieFinishAccess(dst, EXA_PREPARE_DEST);
}

static void lorieExaRepairSolidXByte(PixmapPtr dst, BoxPtr boxes, int nbox, Pixel fg) {
    int b, y, x, x1, y1, x2, y2, dstride;
    uint8_t xbyte, *d;

    if (!dst || nbox <= 0 || dst->drawable.bitsPerPixel != 32)
        return;
    xbyte = (uint8_t) ((fg >> 24) & 0xff);
    if (!loriePrepareAccess(dst, EXA_PREPARE_DEST))
        return;
    d = dst->devPrivate.ptr;
    dstride = dst->devKind;
    if (d) {
        for (b = 0; b < nbox; b++) {
            x1 = boxes[b].x1; y1 = boxes[b].y1; x2 = boxes[b].x2; y2 = boxes[b].y2;
            if (x1 < 0) x1 = 0;
            if (y1 < 0) y1 = 0;
            if (x2 > dst->drawable.width) x2 = dst->drawable.width;
            if (y2 > dst->drawable.height) y2 = dst->drawable.height;
            for (y = y1; y < y2; y++) {
                uint8_t *row = d + y * dstride + x1 * 4;
                for (x = 0; x < x2 - x1; x++)
                    row[x * 4 + 3] = xbyte;
            }
        }
    }
    lorieFinishAccess(dst, EXA_PREPARE_DEST);
}

static Bool lorieExaPrepareSolid(PixmapPtr dst, int alu, Pixel planemask, Pixel fg) {
    Pixel fullmask;

    memset(&exaGpuSolid, 0, sizeof(exaGpuSolid));
    exaSolidPrepare++;
    if (lorieGpuExaDisabled() || pvfb->root.legacyDrawing || !lorieConnectionAlive() || !lorieRendererAvailable()) {
        exaSolidFallback++;
        return FALSE;
    }
    if (alu != GXcopy) {
        exaSolidFallback++;
        return FALSE;
    }
    if (dst->drawable.bitsPerPixel != 32) {
        exaSolidFallback++;
        return FALSE;
    }
    fullmask = dst->drawable.depth >= 32 ? ~((Pixel) 0) : ((((Pixel) 1) << dst->drawable.depth) - 1);
    if (planemask != fullmask && planemask != ~((Pixel) 0)) {
        exaSolidFallback++;
        return FALSE;
    }
    if (!lorieEnsureGpuSampleable(dst, LORIEBUFFER_AHARDWAREBUFFER)) {
        exaSolidFallback++;
        return FALSE;
    }
    exaGpuSolid.dst = dst;
    exaGpuSolid.fg = fg;
    exaSolidGpu++;
    return TRUE;
}

static void lorieExaSolid(PixmapPtr dst, int x1, int y1, int x2, int y2) {
    uint64_t serial = 0;
    void *dstBuf = NULL;
    Bool scheduled = FALSE;

    if (x2 <= x1 || y2 <= y1 || !exaGpuSolid.dst)
        return;
    scheduled = lorieTryScheduleGpuSolid(dst, x1, y1, x2, y2, exaGpuSolid.fg, &serial, &dstBuf);
    if (!scheduled && exaGpuSolid.scheduled) {
        lorieGpuCopyWait(exaGpuSolid.lastSerial, 2000);
        scheduled = lorieTryScheduleGpuSolid(dst, x1, y1, x2, y2, exaGpuSolid.fg, &serial, &dstBuf);
    }
    if (scheduled) {
        BoxRec box = { (short) x1, (short) y1, (short) x2, (short) y2 };
        exaGpuSolid.lastSerial = serial;
        exaGpuSolid.scheduled++;
        exaGpuSolid.dstBuf = dstBuf;
        if (exaGpuSolid.nrepair == 0)
            exaGpuSolid.repairUnion = box;
        else {
            if (box.x1 < exaGpuSolid.repairUnion.x1) exaGpuSolid.repairUnion.x1 = box.x1;
            if (box.y1 < exaGpuSolid.repairUnion.y1) exaGpuSolid.repairUnion.y1 = box.y1;
            if (box.x2 > exaGpuSolid.repairUnion.x2) exaGpuSolid.repairUnion.x2 = box.x2;
            if (box.y2 > exaGpuSolid.repairUnion.y2) exaGpuSolid.repairUnion.y2 = box.y2;
        }
        if (exaGpuSolid.nrepair < 32)
            exaGpuSolid.repair[exaGpuSolid.nrepair++] = box;
        exaSolidRects++;
        return;
    }
    if (exaGpuSolid.scheduled)
        lorieGpuCopyWait(exaGpuSolid.lastSerial, 2000);
    lorieExaCpuSolidRect(dst, x1, y1, x2, y2, exaGpuSolid.fg);
    exaSolidCpuRects++;
}

static void lorieExaDoneSolid(unused PixmapPtr dst) {
    int i;
    if (exaGpuSolid.scheduled) {
        if (!lorieGpuCopyWait(exaGpuSolid.lastSerial, 2000))
            log(ERROR, "EXA GPU solid wait timeout serial=%llu scheduled=%d",
                (unsigned long long) exaGpuSolid.lastSerial, exaGpuSolid.scheduled);
        /* R8G8B8X8 FBO writes leave the unused byte 0xFF. 24bpp X11 pixels store 0 there. */
        if (dst && dst->drawable.depth < 32) {
            if (exaGpuSolid.nrepair > 0 && exaGpuSolid.nrepair < 32)
                lorieExaRepairSolidXByte(dst, exaGpuSolid.repair, exaGpuSolid.nrepair, exaGpuSolid.fg);
            else
                lorieExaRepairSolidXByte(dst, &exaGpuSolid.repairUnion, 1, exaGpuSolid.fg);
        }
        for (i = 0; i < exaGpuSolid.scheduled; i++)
            lorieGpuCopyAck(NULL, exaGpuSolid.dstBuf);
    }
    memset(&exaGpuSolid, 0, sizeof(exaGpuSolid));
}

static void lorieExaCpuCopyRect(PixmapPtr src, PixmapPtr dst, int srcX, int srcY, int dstX, int dstY, int w, int h) {
    int bpp, sstride, dstride, i;
    char *s, *d;

    if (w <= 0 || h <= 0)
        return;
    if (!loriePrepareAccess(src, EXA_PREPARE_SRC))
        return;
    if (!loriePrepareAccess(dst, EXA_PREPARE_DEST)) {
        lorieFinishAccess(src, EXA_PREPARE_SRC);
        return;
    }
    s = src->devPrivate.ptr;
    d = dst->devPrivate.ptr;
    sstride = src->devKind;
    dstride = dst->devKind;
    bpp = src->drawable.bitsPerPixel / 8;
    if (s && d && bpp > 0) {
        for (i = 0; i < h; i++)
            memcpy(d + (dstY + i) * dstride + dstX * bpp,
                   s + (srcY + i) * sstride + srcX * bpp,
                   (size_t) w * (size_t) bpp);
    }
    lorieFinishAccess(dst, EXA_PREPARE_DEST);
    lorieFinishAccess(src, EXA_PREPARE_SRC);
}

static void lorieUnlockBgraAhb(PixmapPtr pPix) {
    LoriePixmapPriv *sp = LORIE_PIXMAP_PRIV_FROM_PIXMAP(pPix);
    if (sp && sp->buffer && sp->locked &&
        LorieBuffer_description(sp->buffer)->format == AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM) {
        LorieBuffer_unlock(sp->buffer);
        sp->locked = NULL;
        sp->wasLocked = FALSE;
    }
}

static Bool lorieExaPrepareCopy(PixmapPtr src, PixmapPtr dst, unused int dx, unused int dy, int alu, Pixel planemask) {
    Pixel fullmask;

    memset(&exaGpuCopy, 0, sizeof(exaGpuCopy));
    if (lorieGpuExaDisabled() || pvfb->root.legacyDrawing || !lorieConnectionAlive() || !lorieRendererAvailable())
        return FALSE;
    if (alu != GXcopy || src == dst)
        return FALSE;
    if (src->drawable.depth != dst->drawable.depth || src->drawable.bitsPerPixel != dst->drawable.bitsPerPixel)
        return FALSE;
    fullmask = src->drawable.depth >= 32 ? ~((Pixel) 0) : ((((Pixel) 1) << src->drawable.depth) - 1);
    if (planemask != fullmask && planemask != ~((Pixel) 0))
        return FALSE;
    if (!lorieEnsureGpuSampleable(src, LORIEBUFFER_AHARDWAREBUFFER) ||
        !lorieEnsureGpuSampleable(dst, LORIEBUFFER_AHARDWAREBUFFER))
        return FALSE;
    exaGpuCopy.src = src;
    exaGpuCopy.dst = dst;
    lorieUnlockBgraAhb(src);
    return TRUE;
}

static void lorieExaCopy(PixmapPtr dst, int srcX, int srcY, int dstX, int dstY, int width, int height) {
    BoxRec box;
    RegionRec region;
    uint64_t serial = 0;
    void *dstBuf = NULL;
    Bool scheduled = FALSE;

    if (width <= 0 || height <= 0 || !exaGpuCopy.src)
        return;
    exaCopyAttempts++;
    box = (BoxRec) { (short) srcX, (short) srcY, (short) (srcX + width), (short) (srcY + height) };
    RegionInit(&region, &box, 1);
    scheduled = lorieTryScheduleGpuCopy(exaGpuCopy.src, dst, &region,
                                        (int16_t) (dstX - srcX), (int16_t) (dstY - srcY),
                                        &serial, &dstBuf);
    if (!scheduled && exaGpuCopy.scheduled) {
        lorieGpuCopyWait(exaGpuCopy.lastSerial, 2000);
        scheduled = lorieTryScheduleGpuCopy(exaGpuCopy.src, dst, &region,
                                            (int16_t) (dstX - srcX), (int16_t) (dstY - srcY),
                                            &serial, &dstBuf);
    }
    RegionUninit(&region);
    if (scheduled) {
        exaGpuCopy.lastSerial = serial;
        exaGpuCopy.scheduled++;
        exaGpuCopy.dstBuf = dstBuf;
        exaCopyOffloads++;
        return;
    }
    if (exaGpuCopy.scheduled)
        lorieGpuCopyWait(exaGpuCopy.lastSerial, 2000);
    lorieExaCpuCopyRect(exaGpuCopy.src, dst, srcX, srcY, dstX, dstY, width, height);
    exaCopyFallbackRects++;
}

static void lorieExaDoneCopy(unused PixmapPtr dst) {
    int i;
    if (exaGpuCopy.scheduled) {
        if (!lorieGpuCopyWait(exaGpuCopy.lastSerial, 2000))
            log(ERROR, "EXA GPU copy wait timeout serial=%llu scheduled=%d",
                (unsigned long long) exaGpuCopy.lastSerial, exaGpuCopy.scheduled);
        for (i = 0; i < exaGpuCopy.scheduled; i++)
            lorieGpuCopyAck(exaGpuCopy.src, exaGpuCopy.dstBuf);
    }
    memset(&exaGpuCopy, 0, sizeof(exaGpuCopy));
}

static struct {
    PixmapPtr src;
    PixmapPtr dst;
    uint64_t lastSerial;
    int scheduled;
    void *dstBuf;
    int nrepair;
    BoxRec repair[32];
    BoxRec repairUnion;
} exaGpuComp;

static Bool lorieCanAccelCompositePictures(int op, PicturePtr src, PicturePtr mask, PicturePtr dst) {
    if (op != PictOpOver)
        return FALSE;
    if (!src || !dst || mask)
        return FALSE;
    if (src->format != PICT_a8r8g8b8)
        return FALSE;
    if (dst->format != PICT_x8r8g8b8)
        return FALSE;
    if (!src->pDrawable || !dst->pDrawable)
        return FALSE;
    if (src->transform || src->repeat)
        return FALSE;
    if (src->filter != PictFilterNearest)
        return FALSE;
    if (src->componentAlpha || dst->componentAlpha)
        return FALSE;
    if (src->alphaMap || dst->alphaMap)
        return FALSE;
    if (src->pDrawable == dst->pDrawable)
        return FALSE;
    return TRUE;
}

static Bool lorieCanAccelComposite(int op, PicturePtr src, PicturePtr mask, PicturePtr dst,
                                  PixmapPtr srcPix, PixmapPtr maskPix, PixmapPtr dstPix) {
    LorieBuffer *sb, *db;
    const LorieBuffer_Desc *sd;

    if (!lorieCanAccelCompositePictures(op, src, mask, dst))
        return FALSE;
    if (maskPix || !srcPix || !dstPix || srcPix == dstPix)
        return FALSE;
    if (lorieGpuExaDisabled() || pvfb->root.legacyDrawing ||
        !lorieConnectionAlive() || !lorieRendererAvailable())
        return FALSE;
    sb = lorieEnsureGpuSampleable(srcPix, LORIEBUFFER_AHARDWAREBUFFER);
    db = lorieEnsureGpuSampleable(dstPix, LORIEBUFFER_AHARDWAREBUFFER);
    if (!sb || !db)
        return FALSE;
    sd = LorieBuffer_description(sb);
    /* RGBX sampling forces A=1; ARGB Over needs a format that preserves alpha. */
    if (sd->format == AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM)
        return FALSE;
    return TRUE;
}

static Bool lorieExaCheckComposite(int op, PicturePtr pSrc, PicturePtr pMask, PicturePtr pDst) {
    Bool ok;

    if (lorieGpuExaDisabled() || pvfb->root.legacyDrawing ||
        !lorieConnectionAlive() || !lorieRendererAvailable()) {
        exaCompCheckFalse++;
        return FALSE;
    }
    ok = lorieCanAccelCompositePictures(op, pSrc, pMask, pDst);
    if (ok)
        exaCompCheckTrue++;
    else
        exaCompCheckFalse++;
    return ok;
}

static Bool lorieExaPrepareComposite(int op, PicturePtr pSrc, PicturePtr pMask, PicturePtr pDst,
                                    PixmapPtr pSrcPix, PixmapPtr pMaskPix, PixmapPtr pDstPix) {
    memset(&exaGpuComp, 0, sizeof(exaGpuComp));
    if (!lorieCanAccelComposite(op, pSrc, pMask, pDst, pSrcPix, pMaskPix, pDstPix)) {
        exaCompPrepareFalse++;
#ifdef __ANDROID__
        p2a2_emit("Gcomp Prepare FALSE");
#endif
        return FALSE;
    }
    exaGpuComp.src = pSrcPix;
    exaGpuComp.dst = pDstPix;
    if (exaCompSrcUpload) {
        LorieBuffer_release(exaCompSrcUpload);
        exaCompSrcUpload = NULL;
    }
    /* Snapshot BGRA src into an FD while X still holds the CPU lock. Renderer
     * cannot AHardwareBuffer_lock the live AHB, and BGRA EGLImages sample black. */
    exaCompSrcUpload = lorieCloneBgraAhbToFd(pSrcPix);
    if (!exaCompSrcUpload) {
        exaCompPrepareFalse++;
#ifdef __ANDROID__
        p2a2_emit("Gcomp Prepare FALSE");
#endif
        return FALSE;
    }
#ifdef __ANDROID__
    {
        LoriePixmapPriv *sp = LORIE_PIXMAP_PRIV_FROM_PIXMAP(pSrcPix);
        uint32_t px0 = sp && sp->locked ? *((uint32_t *) sp->locked) : 0;
        char msg[96];
        snprintf(msg, sizeof(msg), "Gcomp FDCLONE id=%llu px0=%08x",
                 (unsigned long long) LorieBuffer_description(exaCompSrcUpload)->id, px0);
        p2a2_emit(msg);
    }
#endif
    exaCompPrepareTrue++;
#ifdef __ANDROID__
    p2a2_emit("Gcomp Prepare TRUE");
#endif
    return TRUE;
}

static void lorieExaCpuOverRect(PixmapPtr src, PixmapPtr dst, int srcX, int srcY, int dstX, int dstY, int w, int h) {
    pixman_image_t *si, *di;

    if (w <= 0 || h <= 0)
        return;
    if (!loriePrepareAccess(src, EXA_PREPARE_SRC))
        return;
    if (!loriePrepareAccess(dst, EXA_PREPARE_DEST)) {
        lorieFinishAccess(src, EXA_PREPARE_SRC);
        return;
    }
    si = pixman_image_create_bits(PIXMAN_a8r8g8b8, src->drawable.width, src->drawable.height,
                                  src->devPrivate.ptr, src->devKind);
    di = pixman_image_create_bits(PIXMAN_x8r8g8b8, dst->drawable.width, dst->drawable.height,
                                  dst->devPrivate.ptr, dst->devKind);
    if (si && di)
        pixman_image_composite32(PIXMAN_OP_OVER, si, NULL, di, srcX, srcY, 0, 0, dstX, dstY, w, h);
    if (si)
        pixman_image_unref(si);
    if (di)
        pixman_image_unref(di);
    lorieFinishAccess(dst, EXA_PREPARE_DEST);
    lorieFinishAccess(src, EXA_PREPARE_SRC);
}

static void lorieExaRepairDestXByteZero(PixmapPtr dst, BoxPtr boxes, int nbox) {
    int b, y, x, x1, y1, x2, y2, dstride;
    uint8_t *d;

    if (!dst || nbox <= 0 || dst->drawable.bitsPerPixel != 32)
        return;
    if (!loriePrepareAccess(dst, EXA_PREPARE_DEST))
        return;
    d = dst->devPrivate.ptr;
    dstride = dst->devKind;
    if (d) {
        for (b = 0; b < nbox; b++) {
            x1 = boxes[b].x1; y1 = boxes[b].y1; x2 = boxes[b].x2; y2 = boxes[b].y2;
            if (x1 < 0) x1 = 0;
            if (y1 < 0) y1 = 0;
            if (x2 > dst->drawable.width) x2 = dst->drawable.width;
            if (y2 > dst->drawable.height) y2 = dst->drawable.height;
            for (y = y1; y < y2; y++) {
                uint8_t *row = d + y * dstride + x1 * 4;
                for (x = 0; x < x2 - x1; x++)
                    row[x * 4 + 3] = 0;
            }
        }
    }
    lorieFinishAccess(dst, EXA_PREPARE_DEST);
}

static void lorieExaComposite(PixmapPtr dst, int srcX, int srcY, unused int maskX, unused int maskY,
                             int dstX, int dstY, int width, int height) {
    BoxRec box;
    RegionRec region;
    uint64_t serial = 0;
    void *dstBuf = NULL;
    Bool scheduled = FALSE;

    if (width <= 0 || height <= 0 || !exaGpuComp.src)
        return;
    box = (BoxRec) { (short) srcX, (short) srcY, (short) (srcX + width), (short) (srcY + height) };
    RegionInit(&region, &box, 1);
#ifdef __ANDROID__
    {
        LoriePixmapPriv *sp = LORIE_PIXMAP_PRIV_FROM_PIXMAP(exaGpuComp.src);
        const LorieBuffer_Desc *sd = sp && sp->buffer ? LorieBuffer_description(sp->buffer) : NULL;
        lorieP2b2Stamp("REQ_AHB", exaGpuComp.src, sp, sd, sp ? sp->locked : NULL,
                       LORIEBUFFER_AHARDWAREBUFFER, srcX, srcY, width, height);
        if (exaCompSrcUpload) {
            const LorieBuffer_Desc *ud = LorieBuffer_description(exaCompSrcUpload);
            lorieP2b2Stamp("S3_FD", exaGpuComp.src, sp, ud, ud->data,
                           LORIEBUFFER_FD, srcX, srcY, width, height);
        }
    }
#endif
    scheduled = lorieTryScheduleGpuBlit(exaGpuComp.src, dst, &region,
                                        (int16_t) (dstX - srcX), (int16_t) (dstY - srcY),
                                        LORIE_GPU_OP_COMPOSITE, &serial, &dstBuf);
    if (!scheduled && exaGpuComp.scheduled) {
        lorieGpuCopyWait(exaGpuComp.lastSerial, 2000);
        scheduled = lorieTryScheduleGpuBlit(exaGpuComp.src, dst, &region,
                                            (int16_t) (dstX - srcX), (int16_t) (dstY - srcY),
                                            LORIE_GPU_OP_COMPOSITE, &serial, &dstBuf);
    }
    RegionUninit(&region);
    if (scheduled) {
        BoxRec dbox = { (short) dstX, (short) dstY, (short) (dstX + width), (short) (dstY + height) };
        exaGpuComp.lastSerial = serial;
        exaGpuComp.scheduled++;
        exaGpuComp.dstBuf = dstBuf;
        if (exaGpuComp.nrepair == 0)
            exaGpuComp.repairUnion = dbox;
        else {
            if (dbox.x1 < exaGpuComp.repairUnion.x1) exaGpuComp.repairUnion.x1 = dbox.x1;
            if (dbox.y1 < exaGpuComp.repairUnion.y1) exaGpuComp.repairUnion.y1 = dbox.y1;
            if (dbox.x2 > exaGpuComp.repairUnion.x2) exaGpuComp.repairUnion.x2 = dbox.x2;
            if (dbox.y2 > exaGpuComp.repairUnion.y2) exaGpuComp.repairUnion.y2 = dbox.y2;
        }
        if (exaGpuComp.nrepair < 32)
            exaGpuComp.repair[exaGpuComp.nrepair++] = dbox;
        exaCompGpuRects++;
#ifdef __ANDROID__
        {
            char msg[160];
            snprintf(msg, sizeof(msg), "Gcomp RECT src=%d,%d dst=%d,%d %dx%d",
                     srcX, srcY, dstX, dstY, width, height);
            p2a2_emit(msg);
        }
#endif
        return;
    }
    if (exaGpuComp.scheduled)
        lorieGpuCopyWait(exaGpuComp.lastSerial, 2000);
    lorieExaCpuOverRect(exaGpuComp.src, dst, srcX, srcY, dstX, dstY, width, height);
    exaCompCpuRects++;
}

static void lorieExaDoneComposite(PixmapPtr dst) {
    int i;
    LorieBuffer *upload = exaCompSrcUpload;

    if (exaGpuComp.scheduled) {
        if (!lorieGpuCopyWait(exaGpuComp.lastSerial, 2000))
            log(ERROR, "EXA GPU composite wait timeout serial=%llu scheduled=%d",
                (unsigned long long) exaGpuComp.lastSerial, exaGpuComp.scheduled);
        if (dst && dst->drawable.depth < 32) {
            if (exaGpuComp.nrepair > 0 && exaGpuComp.nrepair < 32)
                lorieExaRepairDestXByteZero(dst, exaGpuComp.repair, exaGpuComp.nrepair);
            else
                lorieExaRepairDestXByteZero(dst, &exaGpuComp.repairUnion, 1);
        }
        for (i = 0; i < exaGpuComp.scheduled; i++) {
            if (upload) {
                LorieBuffer_gpuCopyPendingDec(upload);
                LorieBuffer_release(upload);
                if (exaGpuComp.dstBuf) {
                    LorieBuffer_gpuCopyPendingDec((LorieBuffer *) exaGpuComp.dstBuf);
                    LorieBuffer_release((LorieBuffer *) exaGpuComp.dstBuf);
                } else
                    pvfb->rootGpuCopyPending--;
            } else {
                lorieGpuCopyAck(exaGpuComp.src, exaGpuComp.dstBuf);
            }
        }
    }
    if (upload) {
        LorieBuffer_release(upload);
        exaCompSrcUpload = NULL;
    }
    exaCompDone++;
#ifdef __ANDROID__
    p2a2_emit("Gcomp Done");
#endif
    memset(&exaGpuComp, 0, sizeof(exaGpuComp));
}

Bool loriePresentFlip(__unused RRCrtcPtr crtc, __unused uint64_t event_id, __unused uint64_t target_msc, PixmapPtr pixmap, __unused Bool sync_flip) {
    LoriePixmapPriv* priv = (LoriePixmapPriv*) exaGetPixmapDriverPrivate(pixmap);
    if (!priv || !priv->buffer || priv->mem || pvfb->root.width != pixmap->drawable.width || pvfb->root.width != pixmap->drawable.height)
        return FALSE;

    const LorieBuffer_Desc *desc = LorieBuffer_description(priv->buffer);
    char *forceFlip = getenv("TERMUX_X11_FORCE_FLIP");
    if (desc->type == LORIEBUFFER_FD && priv->imported && !(forceFlip && strcmp(forceFlip, "1") == 0))
        return FALSE; // For some reason it does not work fine with turnip.

    // Regular buffers can not be shared to activity, we must explicitly convert LorieBuffer to FD or AHardwareBuffer
    lorieEnsureGpuSampleable(pixmap, pvfb->root.legacyDrawing ? LORIEBUFFER_FD : LORIEBUFFER_AHARDWAREBUFFER);

    if (desc->type != LORIEBUFFER_FD && desc->type != LORIEBUFFER_AHARDWAREBUFFER)
        return FALSE;

    lorieRegisterBuffer(priv->buffer);
    return TRUE;
}

void loriePresentAfterFlip(__unused RRCrtcPtr crtc, uint64_t event_id, uint64_t ust, uint64_t target_msc, __unused PixmapPtr pixmap) {
    // X server was patched to call this function right after finishing all present_flip shenanigans
    // Since we do not invoke DRM API or anything similar we do not need to implement this as callback
    // For some reason calling present_event_notify in BlockHandler or as QueueWorkProc/eventfd callback
    // adds some delay which may be easily avoided this way.
    static BoxRec box = { 0, 0, 1, 1 }; // lorieRedraw only checks if it is empty or not.
    RegionReset(DamageRegion(pvfb->damage), &box);
    pvfb->current_msc = min(pvfb->current_msc + 1, target_msc);
    present_event_notify(event_id, ust, pvfb->current_msc);
}

void loriePresentUnflip(__unused ScreenPtr screen, uint64_t event_id) {
    present_event_notify(event_id, 0, 0);
}

static struct present_screen_info loriePresentInfo = {
        .get_crtc = loriePresentGetCrtc,
        .get_ust_msc = loriePresentGetUstMsc,
        .queue_vblank = loriePresentQueueVblank,
        .abort_vblank = loriePresentAbortVblank,
        // check_flip is called only in present_check_flip_window during window reconfiguration.
        // The function should tell if pixmap can be used for flipping window.
        // Since there are no other drivers involved here we assume it always fits.
        .check_flip = TrueNoop,
        .flip = loriePresentFlip,
        .after_flip = loriePresentAfterFlip,
        .unflip = loriePresentUnflip,
};

void exaDDXDriverInit(__unused ScreenPtr pScreen) {}

void *lorieCreatePixmap(__unused ScreenPtr pScreen, int width, int height, int depth, int usage_hint, __unused int bpp, int *new_fb_pitch) {
    LoriePixmapPriv *priv;
    size_t size = sizeof(LoriePixmapPriv);
    int8_t format;
    uint8_t type;
    *new_fb_pitch = 0;

    priv = calloc(1, size);
    if (!priv)
        return NULL;

    if (width == 0 || height == 0)
        return priv;

    type = usage_hint != CREATE_PIXMAP_USAGE_LORIEBUFFER_BACKED ? LORIEBUFFER_REGULAR : pvfb->root.legacyDrawing ? LORIEBUFFER_FD : LORIEBUFFER_AHARDWAREBUFFER;
    format = depth >= 32 ? AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM : AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM;
    priv->buffer = LorieBuffer_allocate(width, height, format, type);
    *new_fb_pitch = LorieBuffer_description(priv->buffer)->stride * 4;

    LorieBuffer_lock(priv->buffer, &priv->locked);
    if (!priv->buffer) {
        free(priv);
        return NULL;
    }

    return priv;
}

void lorieExaDestroyPixmap(__unused ScreenPtr pScreen, void *driverPriv) {
    LoriePixmapPriv *priv = driverPriv;
    if (priv->buffer) {
        if (priv->locked)
            LorieBuffer_unlock(priv->buffer);
        lorieUnregisterBuffer(priv->buffer);
        LorieBuffer_release(priv->buffer);
    }
    free(priv);
}

Bool lorieModifyPixmapHeader(PixmapPtr pPix, __unused int w, __unused int h, __unused int depth, __unused int bitsPerbppPixel, __unused int devKind, __unused void *data) {
    LoriePixmapPriv *priv = exaGetPixmapDriverPrivate(pPix);
    if (priv && data)
        priv->mem = data;
    return FALSE;
}

// Whether a CPU access to pPix could race a GPU write from the renderer, and so needs state->lock.
static inline __always_inline Bool lorieNeedsGpuLock(PixmapPtr pPix, LoriePixmapPriv *priv, int index) {
    if (pScreenPtr->GetScreenPixmap(pScreenPtr) == pPix)
        return index == EXA_PREPARE_DEST || (pvfb->rootGpuCopyPending && !pvfb->root.legacyDrawing);
    return !pvfb->root.legacyDrawing && priv->buffer &&
           LorieBuffer_description(priv->buffer)->type == LORIEBUFFER_AHARDWAREBUFFER &&
           LorieBuffer_hasGpuCopyPending(priv->buffer);
}

Bool loriePrepareAccess(PixmapPtr pPix, int index) {
    LoriePixmapPriv *priv = exaGetPixmapDriverPrivate(pPix);
    if (lorieNeedsGpuLock(pPix, priv, index))
        lorie_mutex_lock(&pvfb->state->lock, &pvfb->state->lockingPid);

    if (!priv->locked && !priv->mem) {
        int err = LorieBuffer_lock(priv->buffer, &priv->locked);
        if (err) {
            dprintf(2, "Failed to lock buffer, err %d\n", err);
            return FALSE;
        }
        priv->wasLocked = FALSE;
    } else
        priv->wasLocked = TRUE;

    pPix->devPrivate.ptr = priv->locked ?: priv->mem;
    return TRUE;
}

void lorieFinishAccess(PixmapPtr pPix, int index) {
    LoriePixmapPriv *priv = exaGetPixmapDriverPrivate(pPix);
    if (lorieNeedsGpuLock(pPix, priv, index))
        lorie_mutex_unlock(&pvfb->state->lock, &pvfb->state->lockingPid);

    if (!priv->wasLocked) {
        LorieBuffer_unlock(priv->buffer);
        priv->locked = NULL;
        priv->wasLocked = FALSE;
    }
}

static ExaDriverRec lorieExa = {
        .exa_major = EXA_VERSION_MAJOR, .exa_minor = EXA_VERSION_MINOR, .maxX = 32767, .maxY = 32767,
        .flags = EXA_OFFSCREEN_PIXMAPS | EXA_HANDLES_PIXMAPS, .pixmapPitchAlign = 32,
        .PrepareSolid = lorieExaPrepareSolid, .Solid = lorieExaSolid, .DoneSolid = lorieExaDoneSolid,
        .PrepareCopy = lorieExaPrepareCopy, .Copy = lorieExaCopy, .DoneCopy = lorieExaDoneCopy,
        .CheckComposite = lorieExaCheckComposite,
        .PrepareComposite = lorieExaPrepareComposite,
        .Composite = lorieExaComposite,
        .DoneComposite = lorieExaDoneComposite,
        .PixmapIsOffscreen = TrueNoop, .WaitMarker = VoidNoop,
        .PrepareAccess = loriePrepareAccess, .FinishAccess = lorieFinishAccess,
        .CreatePixmap2 = lorieCreatePixmap, .DestroyPixmap = lorieExaDestroyPixmap,
        .ModifyPixmapHeader = lorieModifyPixmapHeader,
};

static PixmapPtr loriePixmapFromFds(ScreenPtr screen, CARD8 num_fds, const int *fds, CARD16 width, CARD16 height,
                                    const CARD32 *strides, const CARD32 *offsets, CARD8 depth, __unused CARD8 bpp, CARD64 modifier) {
#define fail(msg, ...) do { log(ERROR, msg, ##__VA_ARGS__); goto fail; } while(0)
#define check(cond, msg, ...) if ((cond)) fail(msg, ##__VA_ARGS__)
    const CARD64 AHARDWAREBUFFER_SOCKET_FD = 1255;
    const CARD64 AHARDWAREBUFFER_FLIPPED_SOCKET_FD = 1256;
    const CARD64 RAW_MMAPPABLE_FD = 1274;
    AHardwareBuffer_Desc desc = {0};
    PixmapPtr pixmap = NullPixmap;
    LoriePixmapPriv *priv = NULL;

    check(num_fds > 1, "DRI3: More than 1 fd");
    check(modifier != RAW_MMAPPABLE_FD && modifier != AHARDWAREBUFFER_SOCKET_FD && modifier != AHARDWAREBUFFER_FLIPPED_SOCKET_FD &&
          modifier != DRM_FORMAT_MOD_INVALID && modifier != DRM_FORMAT_MOD_LINEAR, "DRI3: Modifier is not RAW_MMAPPABLE_FD or AHARDWAREBUFFER_SOCKET_FD");

    pixmap = screen->CreatePixmap(screen, 0, 0, depth, 0);
    check(!pixmap, "DRI3: failed to create pixmap");

    priv = exaGetPixmapDriverPrivate(pixmap);
    check(!priv, "DRI3: failed to obtain pixmap private");

    priv->imported = true;

    if (modifier == DRM_FORMAT_MOD_INVALID || modifier == DRM_FORMAT_MOD_LINEAR || modifier == RAW_MMAPPABLE_FD) {
        check(!(priv->buffer = LorieBuffer_wrapFileDescriptor(width, strides[0]/4, height, AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM, fds[0], offsets[0])), "DRI3: LorieBuffer_wrapAHardwareBuffer failed.");
        screen->ModifyPixmapHeader(pixmap, width, height, 0, 0, strides[0], NULL);
        if (lorieServerDebugEnabled)
            log(INFO, "DRI3: imported raw fd, modifier %llu, %ux%u stride %u", (unsigned long long) modifier, width, height, strides[0]);
        return pixmap;
    }

    if (modifier == AHARDWAREBUFFER_SOCKET_FD || modifier == AHARDWAREBUFFER_FLIPPED_SOCKET_FD) {
        AHardwareBuffer* buffer;
        struct stat info;
        uint8_t buf = 1;
        int r;

        priv->flipped = modifier == AHARDWAREBUFFER_FLIPPED_SOCKET_FD;
        check(fstat(fds[0], &info) != 0, "DRI3: fstat failed: %s", strerror(errno));
        check(!S_ISSOCK(info.st_mode), "DRI3: modifier is AHARDWAREBUFFER_SOCKET_FD but fd is not a socket");
        // Sending signal to other end of socket to send buffer.
        check(write(fds[0], &buf, 1) != 1, "DRI3: AHARDWAREBUFFER_SOCKET_FD: failed to write to socket: %s", strerror(errno));
        check((r = LorieBuffer_recvAHardwareBufferHandleFromUnixSocket(fds[0], &buffer)) != 0,
              "DRI3: AHARDWAREBUFFER_SOCKET_FD: failed to obtain AHardwareBuffer from socket: %d", r);
        check(!buffer, "DRI3: AHARDWAREBUFFER_SOCKET_FD: did not receive AHardwareSocket from buffer");
        LorieBuffer_describeAHardwareBuffer(buffer, &desc);
        check(desc.format != AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM
            && desc.format != AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM
            && desc.format != AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM,
            "DRI3: AHARDWAREBUFFER_SOCKET_FD: wrong format of AHardwareBuffer. Must be one of: AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM, AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM, AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM (stands for 5).");
        check(!(priv->buffer = LorieBuffer_wrapAHardwareBuffer(buffer)), "DRI3: LorieBuffer_wrapAHardwareBuffer failed.");

        screen->ModifyPixmapHeader(pixmap, desc.width, desc.height, 0, 0, desc.stride * 4, NULL);
        if (lorieServerDebugEnabled)
            log(INFO, "DRI3: imported AHardwareBuffer, modifier %llu, %ux%u stride %u", (unsigned long long) modifier, desc.width, desc.height, desc.stride);
    }

    return pixmap;

    fail:
    if (pixmap)
        screen->DestroyPixmap(pixmap);

    return NULL;
}

// DRI3 fds_from_pixmap: hands back a plain dma-buf fd for any GPU-sampleable pixmap, which is
// what every regular DRI3 consumer (Vulkan, unpatched mesa) expects. AHardwareBuffer doesn't
// expose its underlying dma-buf through public NDK API, so it's extracted the same way
// renderer.cpp already probes for dma-buf backing: hand the whole AHardwareBuffer to ourselves
// over a socketpair and pick out whichever received fd actually is a dma-buf.
static int lorieFdsFromPixmap(__unused ScreenPtr screen, PixmapPtr pixmap, int *fds, uint32_t *strides, uint32_t *offsets, uint64_t *modifier) {
    LorieBuffer *buffer = lorieEnsureGpuSampleable(pixmap, LORIEBUFFER_AHARDWAREBUFFER);
    const LorieBuffer_Desc *desc;
    int sv[2] = { -1, -1 }, fd = -1;
    struct cmsghdr *cmsg;

    if (!buffer)
        return 0;
    desc = LorieBuffer_description(buffer);

    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0)
        return 0;

    LorieBuffer_sendRawAHardwareBufferHandleToUnixSocket(desc->buffer, sv[0]);
    shutdown(sv[0], SHUT_WR);

    // Reads every fd handed over in ancillary data until EOF, keeping the first one that turns
    // out to be backed by a dma-buf (identified through its /proc/self/fd/N symlink target, the
    // same trick renderer.cpp uses for detecting dma-buf-backed AHardwareBuffers); the rest are
    // just other planes/vendor metadata of the same native handle and get closed.
    for (;;) {
        uint8_t data[4096];
        union { uint8_t buf[CMSG_SPACE(32 * sizeof(int))]; struct cmsghdr align; } control;
        struct iovec iov = { .iov_base = data, .iov_len = sizeof(data) };
        struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1, .msg_control = control.buf, .msg_controllen = sizeof(control.buf) };
        ssize_t n = recvmsg(sv[1], &msg, 0);
        if (n <= 0)
            break;

        for (cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
            int *cfds, ncfds, i;
            if (cmsg->cmsg_level != SOL_SOCKET || cmsg->cmsg_type != SCM_RIGHTS)
                continue;

            cfds = (int *) CMSG_DATA(cmsg);
            ncfds = (int) ((cmsg->cmsg_len - CMSG_LEN(0)) / sizeof(int));
            for (i = 0; i < ncfds; i++) {
                char path[32], target[256];
                ssize_t len;
                if (fd >= 0) {
                    close(cfds[i]);
                    continue;
                }

                snprintf(path, sizeof(path), "/proc/self/fd/%d", cfds[i]);
                len = readlink(path, target, sizeof(target) - 1);
                if (len > 0) {
                    target[len] = '\0';
                    if (strstr(target, "dmabuf") || strstr(target, "dma_heap") || strstr(target, "/dev/dma")) {
                        fd = cfds[i];
                        continue;
                    }
                }
                close(cfds[i]);
            }
        }
    }
    close(sv[0]);
    close(sv[1]);

    if (fd < 0)
        return 0;

    fds[0] = fd;
    strides[0] = desc->stride * 4;
    offsets[0] = 0;
    *modifier = DRM_FORMAT_MOD_LINEAR;
    return 1;
}

// Serves the Lorie-private DRI3AHardwareBufferFromPixmap request: sends the pixmap's backing
// AHardwareBuffer itself (as opposed to fds_from_pixmap's plain dma-buf) to clients that need
// the full handle, e.g. GLES import via eglGetNativeClientBufferANDROID.
int lorieSendAHardwareBufferForPixmap(PixmapPtr pixmap, int socketFd) {
    LorieBuffer *buffer = lorieEnsureGpuSampleable(pixmap, LORIEBUFFER_AHARDWAREBUFFER);
    if (!buffer)
        return 0;

    LorieBuffer_sendRawAHardwareBufferHandleToUnixSocket(LorieBuffer_description(buffer)->buffer, socketFd);
    return 1;
}

static int lorieGetFormats(__unused ScreenPtr screen, CARD32 *num_formats, CARD32 **formats) {
    static CARD32 format = DRM_FORMAT_ARGB8888;
    *num_formats = 1;
    *formats = &format;
    return TRUE;
}

static int lorieGetModifiers(__unused ScreenPtr screen, uint32_t format, uint32_t *num_modifiers, uint64_t **modifiers) {
    static uint64_t modifier = DRM_FORMAT_MOD_LINEAR;

    if (format != DRM_FORMAT_ARGB8888 && format != DRM_FORMAT_XRGB8888) {
        *num_modifiers = 0;
        *modifiers = NULL;
        return TRUE;
    }

    *num_modifiers = 1;
    *modifiers = &modifier;
    return TRUE;
}

static dri3_screen_info_rec lorieDri3Info = {
        .version = 2,
        .fds_from_pixmap = lorieFdsFromPixmap,
        .pixmap_from_fds = loriePixmapFromFds,
        .get_formats = lorieGetFormats,
        .get_modifiers = lorieGetModifiers,
        .get_drawable_modifiers = FalseNoop
};

static GLboolean drawableSwapBuffers(unused ClientPtr client, unused __GLXdrawable * drawable) { return TRUE; }
static void drawableCopySubBuffer(unused __GLXdrawable * basePrivate, unused int x, unused int y, unused int w, unused int h) {}
static __GLXdrawable * createDrawable(unused ClientPtr client, __GLXscreen * screen, DrawablePtr pDraw,
                                      unused XID drawId, int type, XID glxDrawId, __GLXconfig * glxConfig) {
    __GLXdrawable *private = calloc(1, sizeof *private);
    if (private == NULL)
        return NULL;

    if (!__glXDrawableInit(private, screen, pDraw, type, glxDrawId, glxConfig)) {
        free(private);
        return NULL;
    }

    private->destroy = (void (*)(__GLXdrawable *)) free;
    private->swapBuffers = drawableSwapBuffers;
    private->copySubBuffer = drawableCopySubBuffer;

    return private;
}

static void glXDRIscreenDestroy(__GLXscreen *baseScreen) {
    free(baseScreen->GLXextensions);
    free(baseScreen->GLextensions);
    free(baseScreen->visuals);
    free(baseScreen);
}

static __GLXscreen *glXDRIscreenProbe(ScreenPtr pScreen) {
    __GLXscreen *screen;

    screen = calloc(1, sizeof *screen);
    if (screen == NULL)
        return NULL;

    screen->destroy = glXDRIscreenDestroy;
    screen->createDrawable = createDrawable;
    screen->pScreen = pScreen;
    screen->fbconfigs = configs;
    screen->glvnd = "mesa";

    __glXInitExtensionEnableBits(screen->glx_enable_bits);
    /* There is no real GLX support, but anyways swrast reports it. */
    __glXEnableExtension(screen->glx_enable_bits, "GLX_MESA_copy_sub_buffer");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_EXT_no_config_context");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_ARB_create_context");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_ARB_create_context_no_error");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_ARB_create_context_profile");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_EXT_create_context_es_profile");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_EXT_create_context_es2_profile");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_EXT_framebuffer_sRGB");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_ARB_fbconfig_float");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_EXT_fbconfig_packed_float");
    __glXEnableExtension(screen->glx_enable_bits, "GLX_EXT_texture_from_pixmap");
    __glXScreenInit(screen, pScreen);

    return screen;
}

__GLXprovider __glXDRISWRastProvider = {
        glXDRIscreenProbe,
        "DRISWRAST",
        NULL
};
