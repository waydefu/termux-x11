#pragma clang diagnostic ignored "-Wunknown-pragmas"
#pragma clang diagnostic ignored "-Wmissing-prototypes"
#pragma ide diagnostic ignored "bugprone-reserved-identifier"
#pragma ide diagnostic ignored "OCUnusedMacroInspection"
#pragma ide diagnostic ignored "EndlessLoop"
#define __USE_GNU
#ifdef HAVE_DIX_CONFIG_H
#include <dix-config.h>
#endif
#include <jni.h>
#include <android/log.h>
#include <android/native_window_jni.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/ioctl.h>
#include <libgen.h>
#include <cerrno>
extern "C" {
#include <globals.h>
#define class lorie_reserved_class
#define public lorie_reserved_public
#include <xkbsrv.h>
#include <inpututils.h>
#include <randrstr.h>
#undef class
#undef public
}
#include <linux/in.h>
#include <arpa/inet.h>
#include <poll.h>
#include <pthread.h>
#include "lorie.h"

#define log(prio, ...) __android_log_print(ANDROID_LOG_ ## prio, "LorieNative", __VA_ARGS__)

static int argc = 0;
static char** argv = nullptr;
__LIBC_HIDDEN__ volatile int conn_fd = -1; // The only variable shared with activity code.
extern DeviceIntPtr lorieMouse, lorieTouch, lorieKeyboard, loriePen, lorieEraser;
extern ScreenPtr pScreenPtr;
extern "C" int ucs2keysym(long ucs);
extern "C" void lorieKeysymKeyboardEvent(KeySym keysym, int down);

char *xtrans_unix_path_x11 = nullptr;
char *xtrans_unix_dir_x11 = nullptr;

struct xorg_list registeredBuffers;

/* ---- Gate A P1 X-side registry (dormant unless flag + active generation) ----
 * Fixed pool: FULL refuses new REGISTER locally (P3 falls back to D0a).
 * gateARegistryMutex is a leaf: never held across socket I/O or waits.
 * Pool-stable slots: waiter addresses stay valid for process lifetime, so
 * input-thread signaling needs no refcounting. */
#define LORIE_GATEA_XREGISTRY_SIZE 16
struct LorieGateAXEntry {
    struct LorieGateABufferMeta meta;
    struct LorieGateAWaiter waiter;
    int inUse;
};
static struct LorieGateAXEntry gateAXRegistry[LORIE_GATEA_XREGISTRY_SIZE];
static pthread_mutex_t gateARegistryMutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t gateASendMutex = PTHREAD_MUTEX_INITIALIZER;

static struct LorieGateAXEntry *gateAXFindLocked(uint64_t id) {
    for (int i = 0; i < LORIE_GATEA_XREGISTRY_SIZE; i++) {
        if (gateAXRegistry[i].inUse && gateAXRegistry[i].meta.bufferId == id)
            return &gateAXRegistry[i];
    }
    return NULL;
}

static int gateAXWaiterArmedLocked(struct LorieGateAXEntry *e) {
    int armed;
    pthread_mutex_lock(&e->waiter.lock);
    armed = (e->waiter.state == LORIE_GATEA_WAIT_ARMED);
    pthread_mutex_unlock(&e->waiter.lock);
    return armed;
}

int lorieGateARegistryInsert(uint64_t nonce, uint64_t generation, uint64_t id, uint64_t fingerprint) {
    int i, rc = -1;
    if (!lorieGateAProtoEnabled() || id == 0 || nonce == 0 || generation == 0)
        return -1;
    pthread_mutex_lock(&gateARegistryMutex);
    {
        struct LorieGateAXEntry *dup = gateAXFindLocked(id);
        if (dup != NULL && dup->meta.nonce == nonce && dup->meta.generation == generation) {
            pthread_mutex_unlock(&gateARegistryMutex);
            return -1; /* strict double-insert; P3 never does this */
        }
        if (dup != NULL) {
            /* Same ID, older generation: replace only if fully quiescent
             * (no armed waiter, no pending, no submitted serial). Otherwise
             * refuse; P3 treats refusal per its admission table. */
            if (gateAXWaiterArmedLocked(dup) || dup->meta.pendingCount != 0
                || dup->meta.lastSubmittedSerial != 0) {
                pthread_mutex_unlock(&gateARegistryMutex);
                return -1;
            }
            lorieGateAWaiterDestroy(&dup->waiter);
            dup->inUse = 0;
        }
        for (i = 0; i < LORIE_GATEA_XREGISTRY_SIZE; i++) {
            if (!gateAXRegistry[i].inUse) {
                gateAXRegistry[i].inUse = 1;
                gateAXRegistry[i].meta.nonce = nonce;
                gateAXRegistry[i].meta.generation = generation;
                gateAXRegistry[i].meta.bufferId = id;
                gateAXRegistry[i].meta.fingerprint = fingerprint;
                gateAXRegistry[i].meta.lastSubmittedSerial = 0;
                gateAXRegistry[i].meta.state = LORIE_GATEA_REG_REGISTERING;
                gateAXRegistry[i].meta.pendingCount = 0;
                gateAXRegistry[i].meta.cpuLocked = 0;
                gateAXRegistry[i].meta.unregisterAcked = 0;
                gateAXRegistry[i].meta.ownerRef = NULL; /* P3 admission sets */
                lorieGateAWaiterInit(&gateAXRegistry[i].waiter);
                rc = 0;
                break;
            }
        }
    }
    pthread_mutex_unlock(&gateARegistryMutex);
    return rc;
}

int lorieGateARegistryFind(uint64_t id, struct LorieGateABufferMeta *out) {
    struct LorieGateAXEntry *e;
    if (out == NULL)
        return -1;
    pthread_mutex_lock(&gateARegistryMutex);
    e = gateAXFindLocked(id);
    if (e != NULL)
        *out = e->meta;
    pthread_mutex_unlock(&gateARegistryMutex);
    return e != NULL ? 0 : -1;
}

struct LorieGateAWaiter *lorieGateARegistryWaiter(uint64_t id) {
    struct LorieGateAWaiter *w = NULL;
    pthread_mutex_lock(&gateARegistryMutex);
    {
        struct LorieGateAXEntry *e = gateAXFindLocked(id);
        if (e != NULL)
            w = &e->waiter;
    }
    pthread_mutex_unlock(&gateARegistryMutex);
    return w;
}

/* Atomic find + tuple + fingerprint check + mark + signal under one lock hold
 * (no TOCTOU between lookup and update). Returns 0 if marked, 1 if the id is
 * unknown/stale for this tuple, 2 on fingerprint mismatch. The dispatcher
 * halts on nonzero iff a generation is bound+active, else drops. */
int lorieGateARegistryMarkChecked(uint64_t id, uint64_t nonce, uint64_t generation,
                                  uint64_t fingerprint, int ready, uint32_t code) {
    int rc = 1;
    (void)code; /* P3 wires failure-code diagnostics; terminal state decides here */
    pthread_mutex_lock(&gateARegistryMutex);
    {
        struct LorieGateAXEntry *e = gateAXFindLocked(id);
        if (e != NULL && e->meta.nonce == nonce && e->meta.generation == generation) {
            if (ready && e->meta.fingerprint != fingerprint) {
                rc = 2;
            } else {
                e->meta.state = ready ? LORIE_GATEA_REG_READY : LORIE_GATEA_REG_DEAD;
                lorieGateAWaiterSignal(&e->waiter,
                    ready ? LORIE_GATEA_WAIT_DONE : LORIE_GATEA_WAIT_FAILED);
                rc = 0;
            }
        }
    }
    pthread_mutex_unlock(&gateARegistryMutex);
    return rc;
}

/* Signal every registered waiter FAILED. Used when shared fatal is observed or
 * published; waiters then read the terminal state themselves. */
static void gateABroadcastGateAFailed(void) {
    int i;
    pthread_mutex_lock(&gateARegistryMutex);
    for (i = 0; i < LORIE_GATEA_XREGISTRY_SIZE; i++) {
        if (gateAXRegistry[i].inUse)
            lorieGateAWaiterSignal(&gateAXRegistry[i].waiter, LORIE_GATEA_WAIT_FAILED);
    }
    pthread_mutex_unlock(&gateARegistryMutex);
}

/* Tombstone one generation rotation step: every entry still carrying the old
 * tuple wakes FAILED and can never admit again (insert-replace rules govern
 * any same-ID reuse). Entries with unterminal P3 state (pending/submitted)
 * cannot exist across rotation without proven quiescence — in P1 nothing ever
 * submits, so reaching that branch is corruption. */
void lorieGateARegistryCloseGeneration(uint64_t oldNonce, uint64_t oldGeneration) {
    int i;
    if (oldNonce == 0 || oldGeneration == 0)
        return;
    pthread_mutex_lock(&gateARegistryMutex);
    for (i = 0; i < LORIE_GATEA_XREGISTRY_SIZE; i++) {
        struct LorieGateAXEntry *e = &gateAXRegistry[i];
        if (!e->inUse || e->meta.nonce != oldNonce || e->meta.generation != oldGeneration)
            continue;
        if (e->meta.pendingCount != 0 || e->meta.lastSubmittedSerial != 0) {
            pthread_mutex_unlock(&gateARegistryMutex);
            lorieGateAFatalHalt("x-bump-unterminal", LORIE_GATEA_FAIL_GENERATION);
            return; /* unreachable; silences fallthrough analysis */
        }
        e->meta.state = LORIE_GATEA_REG_DEAD;
        lorieGateAWaiterSignal(&e->waiter, LORIE_GATEA_WAIT_FAILED);
    }
    pthread_mutex_unlock(&gateARegistryMutex);
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_termux_x11_CmdEntryPoint_start(JNIEnv *env, __unused jclass cls, jobjectArray args) {
    pthread_t t;
    JavaVM* vm = nullptr;
    auto detectTracer = []() -> Bool {
        FILE *fp;
        char line[256];
        int pid = 0;

        fp = fopen("/proc/self/status", "r");
        if (!fp)
            return TRUE;

        while (fgets(line, sizeof(line), fp)) {
            if (strncmp(line, "TracerPid:", 10) == 0) {
                sscanf(line+10, "%d", &pid);
                break;
            }
        }

        if (pid != 0)
            log(INFO, "Tracer detected");

        fclose(fp);
        return pid != 0;
    };
    // execv's argv array is a bit incompatible with Java's String[], so we do some converting here...
    argc = env->GetArrayLength(args) + 1; // Leading executable path
    argv = (char**) calloc(argc, sizeof(char*));

    argv[0] = (char*) "Xlorie";
    for(int i=1; i<argc; i++) {
        auto js = (jstring) env->GetObjectArrayElement(args, i - 1);
        const char *pjc = env->GetStringUTFChars(js, JNI_FALSE);
        argv[i] = (char *) calloc(strlen(pjc) + 1, sizeof(char)); //Extra char for the terminating NULL
        strcpy((char *) argv[i], pjc);
        env->ReleaseStringUTFChars(js, pjc);
    }

    {
        cpu_set_t mask;
        long num_cpus = sysconf(_SC_NPROCESSORS_ONLN);

        for (int i = num_cpus/2; i < num_cpus; i++)
            CPU_SET(i, &mask);

        if (sched_setaffinity(0, sizeof(cpu_set_t), &mask) == -1)
            log(ERROR, "Failed to set process affinity: %s", strerror(errno));
    }

    if (getenv("TERMUX_X11_DEBUG") && !fork()) {
        // Printing logs of local logcat.
        char pid[32] = {0};
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        sprintf(pid, "%d", getppid());
        execlp("logcat", "logcat", "--pid", pid, nullptr);
    }

    // No matter what tracer is attached.
    // In the case of gdb or lldb LD_PRELOAD is already set.
    // In the case of proot or proot-distro libtermux-exec in LD_PRELOAD will break linking.
    if (access("/data/data/com.termux/files/usr/lib/libtermux-exec.so", F_OK) == 0 && !detectTracer()
            && !getenv("XSTARTUP_LD_PRELOAD"))
        setenv("LD_PRELOAD", "/data/data/com.termux/files/usr/lib/libtermux-exec.so", 1);

    // adb sets TMPDIR to /data/local/tmp which is pretty useless.
    if (!strcmp("/data/local/tmp", getenv("TMPDIR") ?: ""))
        unsetenv("TMPDIR");

    if (!getenv("TMPDIR")) {
        if (access("/tmp", F_OK) == 0)
            setenv("TMPDIR", "/tmp", 1);
        else if (access("/data/data/com.termux/files/usr/tmp", F_OK) == 0)
            setenv("TMPDIR", "/data/data/com.termux/files/usr/tmp", 1);
    }

    if (!getenv("TMPDIR")) {
        char* error = (char*) "$TMPDIR is not set. Normally it is pointing to /tmp of a container.";
        log(ERROR, "%s", error);
        dprintf(2, "%s\n", error);
        return JNI_FALSE;
    }

    {
        char* tmp = getenv("TMPDIR");
        char cwd[1024] = {0};

        if (!getcwd(cwd, sizeof(cwd)) || access(cwd, F_OK) != 0)
            chdir(tmp);
        asprintf(&xtrans_unix_path_x11, "%s/.X11-unix/X", tmp);
        asprintf(&xtrans_unix_dir_x11, "%s/.X11-unix/", tmp);
    }

    log(VERBOSE, "Using TMPDIR=\"%s\"", getenv("TMPDIR"));

    {
        const char *root_dir = dirname(getenv("TMPDIR"));
        const char* pathes[] = {
                "/etc/X11/fonts", "/usr/share/fonts/X11", "/share/fonts", nullptr
        };
        for (int i=0; pathes[i]; i++) {
            char current_path[1024] = {0};
            snprintf(current_path, sizeof(current_path), "%s%s", root_dir, pathes[i]);
            if (access(current_path, F_OK) == 0) {
                char default_font_path[4096] = {0};
                snprintf(default_font_path, sizeof(default_font_path),
                         "%s/misc,%s/TTF,%s/OTF,%s/Type1,%s/100dpi,%s/75dpi",
                         current_path, current_path, current_path, current_path, current_path, current_path);
                defaultFontPath = strdup(default_font_path);
                break;
            }
        }
    }

    if (!getenv("XKB_CONFIG_ROOT")) {
        // chroot case
        const char *root_dir = dirname(getenv("TMPDIR"));
        char current_path[1024] = {0};
        snprintf(current_path, sizeof(current_path), "%s/usr/share/X11/xkb", root_dir);
        if (access(current_path, F_OK) == 0)
            setenv("XKB_CONFIG_ROOT", current_path, 1);
    }

    if (!getenv("XKB_CONFIG_ROOT")) {
        // proot case
        if (access("/usr/share/xkeyboard-config-2", F_OK) == 0)
            setenv("XKB_CONFIG_ROOT", "/usr/share/xkeyboard-config-2", 1);
        else if (access("/usr/share/X11/xkb", F_OK) == 0)
            setenv("XKB_CONFIG_ROOT", "/usr/share/X11/xkb", 1);
        // Termux case
        else if (access("/data/data/com.termux/files/usr/share/xkeyboard-config-2", F_OK) == 0)
            setenv("XKB_CONFIG_ROOT", "/data/data/com.termux/files/usr/share/xkeyboard-config-2", 1);
        else if (access("/data/data/com.termux/files/usr/share/X11/xkb", F_OK) == 0)
            setenv("XKB_CONFIG_ROOT", "/data/data/com.termux/files/usr/share/X11/xkb", 1);
    }

    if (!getenv("XKB_CONFIG_ROOT")) {
        char* error = (char*) "$XKB_CONFIG_ROOT is not set. Normally it is pointing to /usr/share/X11/xkb of a container.";
        log(ERROR, "%s", error);
        dprintf(2, "%s\n", error);
        return JNI_FALSE;
    }

    XkbBaseDirectory = getenv("XKB_CONFIG_ROOT");
    if (access(XkbBaseDirectory, F_OK) != 0) {
        log(ERROR, "%s is unaccessible: %s\n", XkbBaseDirectory, strerror(errno));
        printf("%s is unaccessible: %s\n", XkbBaseDirectory, strerror(errno));
        return JNI_FALSE;
    }

    env->GetJavaVM(&vm);

    AChoreographer *choreographer = AChoreographer_getInstance();
    // Trigger it first time
    AChoreographer_postFrameCallback(choreographer, (AChoreographer_frameCallback) lorieChoreographerFrameCallback, choreographer);

    xorg_list_init(&registeredBuffers);
    pthread_create(&t, nullptr, +[](__unused void* cookie) -> void* {
        exit(dix_main(argc, (char**) argv, (char*[]) { nullptr }));
    }, vm);
    return JNI_TRUE;
}

static Bool handleTouchEvent(__unused ClientPtr pClient, void *closure) {
    ValuatorMask mask;
    auto *e = (lorieEvent*) closure;
    double x = max(min((float) e->touch.x, pScreenPtr->width), 0);
    double y = max(min((float) e->touch.y, pScreenPtr->height), 0);
    valuator_mask_zero(&mask);
    DDXTouchPointInfoPtr touch = TouchFindByDDXID(lorieTouch, e->touch.id, FALSE);

    // Avoid duplicating events
    if (touch && touch->active) {
        double oldx = 0, oldy = 0;
        if (e->touch.type == XI_TouchUpdate &&
            valuator_mask_fetch_double(touch->valuators, 0, &oldx) &&
            valuator_mask_fetch_double(touch->valuators, 1, &oldy) &&
            oldx == x && oldy == y)
            goto end;
    }

    // Sometimes activity part does not send XI_TouchBegin and sends only XI_TouchUpdate.
    if (e->touch.type == XI_TouchUpdate && (!touch || !touch->active))
        e->touch.type = XI_TouchBegin;

    if (e->touch.type == XI_TouchEnd && (!touch || !touch->active))
        goto end;

    valuator_mask_set_double(&mask, 0, x * 0xFFFF / (float) pScreenPtr->width);
    valuator_mask_set_double(&mask, 1, y * 0xFFFF / (float) pScreenPtr->height);
    QueueTouchEvents(lorieTouch, e->touch.type, e->touch.id, 0, &mask);

    end:
    free(e);
    return TRUE;
}

/* ---- Gate A P1 input-thread dispatch ----
 * Peek for magic-gated frames before the legacy parse. Flag OFF (or no magic)
 * → legacy path byte-identical (same blocking profile: peek blocks exactly
 * where the legacy read below would). */
static int gateAPeekIsGateA(int fd) {
    uint32_t magic = 0;
    return recv(fd, &magic, sizeof(magic), MSG_PEEK) == (ssize_t)sizeof(magic)
        && magic == LORIE_GATEA_MAGIC;
}

/* Bound-tuple match for an inbound frame. Uses acquire loads; the generation
 * can only advance under the share path, never backwards. */
static int gateAFrameTupleMatch(const struct LorieGateAFrame *fr) {
    struct LorieGateAProtocol *shared = lorieGateAShared();
    if (shared == NULL)
        return 0;
    return fr->nonce == lorieGateALoadU64Acquire(&shared->sessionNonce)
        && fr->nonce != 0
        && fr->generation == lorieGateALoadU64Acquire(&shared->generation)
        && fr->generation != 0;
}

/* Fatal from the input thread: poison shared state (best-effort containment
 * for the renderer side, which may still hold the mapping), wake every Gate A
 * waiter FAILED, then halt without normal cleanup. */
static void gateAFatalFromInput(uint32_t reason, const char *what) {
    struct LorieGateAProtocol *shared = lorieGateAShared();
    if (shared != NULL)
        lorieGateAPublishFatal(shared, reason);
    gateABroadcastGateAFailed();
    lorieGateAFatalHalt(what, reason);
}

/* Consume exactly one Gate A frame (caller verified magic via peek), or take
 * the fatal path. Short reads mean stream desync: FATAL while a generation is
 * bound, deterministic drop during teardown when nothing is bound. */
static void handleGateAFrame(int fd) {
    struct LorieGateAFrame fr;
    int active = lorieGateAActive();
    if (lorieGateAReadFull(fd, &fr, sizeof(fr)) != (ssize_t)sizeof(fr)) {
        if (active)
            gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL, "x-short-frame");
        return;
    }
    if (fr.magic != LORIE_GATEA_MAGIC || fr.version != LORIE_GATEA_PROTOCOL_VERSION
        || fr.reserved != 0) {
        if (active)
            gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL, "x-bad-frame");
        return;
    }
    if (!gateAFrameTupleMatch(&fr)) {
        if (active)
            gateAFatalFromInput(LORIE_GATEA_FAIL_GENERATION, "x-wrong-generation");
        return;
    }
    switch (fr.type) {
    case LORIE_GATEA_MSG_READY:
    case LORIE_GATEA_MSG_REGISTER_FAILED: {
        /* Both bodies are fixed 8 bytes: READY carries the fingerprint,
         * FAILED carries {code,reserved} (low 32 bits are the code). One
         * atomic lookup+check+update: unsolicited or fingerprint-mismatched
         * replies for the bound generation are corruption (P3 inserts before
         * sending, so a legitimate reply always matches). */
        uint64_t bodyWord = 0;
        int mrc;
        if (fr.length != sizeof(bodyWord)) {
            if (active)
                gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL, "x-bad-result-length");
            return;
        }
        if (lorieGateAReadFull(fd, &bodyWord, sizeof(bodyWord)) != (ssize_t)sizeof(bodyWord)) {
            if (active)
                gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL, "x-short-result");
            return;
        }
        mrc = lorieGateARegistryMarkChecked(fr.bufferId, fr.nonce, fr.generation,
            bodyWord,
            fr.type == LORIE_GATEA_MSG_READY,
            fr.type == LORIE_GATEA_MSG_READY ? 0 : (uint32_t)(bodyWord & 0xffffffffu));
        if (mrc != 0 && active)
            gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL,
                mrc == 2 ? "x-result-fingerprint" : "x-unsolicited-result");
        return;
    }
    case LORIE_GATEA_MSG_FATAL_NOTIFY: {
        struct LorieGateAFatalNotifyBody body;
        struct LorieGateAProtocol *shared;
        if (fr.length != sizeof(body))
            { if (active) gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL, "x-bad-fatal-length"); return; }
        if (lorieGateAReadFull(fd, &body, sizeof(body)) != (ssize_t)sizeof(body))
            { if (active) gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL, "x-short-fatal"); return; }
        /* Hint only: correctness comes from the shared atomic. Because the
         * renderer CASes before notifying and the flag is sticky, observing a
         * genuine hint with a clear flag is impossible — treat it as corrupt. */
        shared = lorieGateAShared();
        if (shared == NULL || lorieGateAObserveFatal(shared) == 0)
            gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL, "x-spurious-fatal-hint");
        gateABroadcastGateAFailed();
        return;
    }
    default:
        /* REGISTER/UNREGISTER/CLOSE/CLOSED inbound to X, or unknown type:
         * corruption in P1 (no such flows exist yet). */
        if (active)
            gateAFatalFromInput(LORIE_GATEA_FAIL_PROTOCOL, "x-unexpected-msg");
        return;
    }
}

void handleLorieEvents(int fd, __unused int ready, __unused void *ignored) {
    ValuatorMask mask;
    lorieEvent e = {0};
    valuator_mask_zero(&mask);

    if (ready & X_NOTIFY_ERROR) {
        LorieBuffer* buf;
        InputThreadUnregisterDev(fd);
        close(fd);
        conn_fd = -1;
        lorieEnableClipboardSync(FALSE);
        while ((buf = LorieBufferList_first(&registeredBuffers)))
            LorieBuffer_removeFromList(buf);
        /* P1 Gate A supplement: an active generation cannot survive HUP.
         * Poison shared state (best-effort containment for the renderer side),
         * wake every waiter FAILED, then halt without normal cleanup. Legacy
         * path above is unchanged. */
        if (lorieGateAProtoEnabled() && lorieGateAActive()) {
            struct LorieGateAProtocol *gp = lorieGateAShared();
            if (gp != NULL)
                lorieGateAPublishFatal(gp, LORIE_GATEA_FAIL_GENERATION);
            gateABroadcastGateAFailed();
            lorieGateAFatalHalt("x-hup", LORIE_GATEA_FAIL_GENERATION);
        }
        return;
    }

    /* P1 Gate A magic gate. OFF (or no magic) → legacy path byte-identical. */
    if (lorieGateAProtoEnabled() && gateAPeekIsGateA(fd)) {
        handleGateAFrame(fd);
        goto again;
    }

    again:
    if (read(fd, &e, sizeof(e)) == sizeof(e)) {
        switch(e.type) {
            case EVENT_SCREEN_SIZE: {
                auto *copy = (lorieEvent*) calloc(1, sizeof(lorieEvent) + e.screenSize.name_size + 1);
                memcpy(copy, &e, sizeof(e));
                copy->screenSize.name = copy->screenSize.name_size ? (char*) (copy + 1) : nullptr;
                if (copy->screenSize.name_size)
                    read(fd, copy->screenSize.name, copy->screenSize.name_size);
                QueueWorkProc(+[](__unused ClientPtr pClient, void *closure) -> Bool {
                    // This must be done only on X server thread.
                    auto* e = (lorieEvent*) closure;
                    __android_log_print(ANDROID_LOG_ERROR, "tx11-request", "window changed: %d %d %s", e->screenSize.width, e->screenSize.height, e->screenSize.name);
                    lorieConfigureNotify(e->screenSize.width, e->screenSize.height, e->screenSize.framerate, e->screenSize.name_size, e->screenSize.name);
                    free(e);
                    return TRUE;
                }, nullptr, copy);
                lorieWakeServer();
                break;
            }
            case EVENT_TOUCH: {
                auto *copy = (lorieEvent*) calloc(1, sizeof(lorieEvent));
                memcpy(copy, &e, sizeof(e));
                QueueWorkProc(handleTouchEvent, nullptr, copy);
                lorieWakeServer();
                break;
            }
            case EVENT_STYLUS: {
                static int buttons_prev = 0;
                uint32_t released, pressed, diff;
                DeviceIntPtr device = e.stylus.mouse ? lorieMouse : (e.stylus.eraser ? lorieEraser : loriePen);
                if (!device) {
                    __android_log_print(ANDROID_LOG_DEBUG, "LorieNative", "got stylus event but device is not requested\n");
                    break;
                }
                __android_log_print(ANDROID_LOG_DEBUG, "LorieNative", "got stylus event %f %f %d %d %d %d %s\n", e.stylus.x, e.stylus.y, e.stylus.pressure, e.stylus.tilt_x, e.stylus.tilt_y, e.stylus.orientation,
                                    device == lorieMouse ? "lorieMouse" : (device == loriePen ? "loriePen" : "lorieEraser"));

                valuator_mask_set_double(&mask, 0, max(min(e.stylus.x, pScreenPtr->width), 0));
                valuator_mask_set_double(&mask, 1, max(min(e.stylus.y, pScreenPtr->height), 0));
                if (device != lorieMouse) {
                    valuator_mask_set_double(&mask, 2, e.stylus.pressure);
                    valuator_mask_set_double(&mask, 3, e.stylus.tilt_x);
                    valuator_mask_set_double(&mask, 4, e.stylus.tilt_y);
                    valuator_mask_set_double(&mask, 5, e.stylus.orientation);
                }
                QueuePointerEvents(device, MotionNotify, 0, POINTER_ABSOLUTE | POINTER_DESKTOP | (device == lorieMouse ? POINTER_NORAW : 0), &mask);

                diff = buttons_prev ^ e.stylus.buttons;
                released = diff & ~e.stylus.buttons;
                pressed = diff & e.stylus.buttons;

                for (int i=0; i<3; i++) {
                    if (released & 0x1) {
                        QueuePointerEvents(device, ButtonRelease, i + 1, POINTER_RELATIVE, nullptr);
                        __android_log_print(ANDROID_LOG_DEBUG, "LorieNative", "sending %d press", i+1);
                    }
                    if (pressed & 0x1) {
                        QueuePointerEvents(device, ButtonPress, i + 1, POINTER_RELATIVE, nullptr);
                        __android_log_print(ANDROID_LOG_DEBUG, "LorieNative", "sending %d release", i+1);
                    }
                    released >>= 1;
                    pressed >>= 1;
                }
                buttons_prev = e.stylus.buttons;

                break;
            }
            case EVENT_STYLUS_ENABLE: {
                lorieSetStylusEnabled(e.stylusEnable.enable);
                break;
            }
            case EVENT_MOUSE: {
                int flags;
                switch(e.mouse.detail) {
                    case 0: // BUTTON_UNDEFINED
                        flags = (e.mouse.relative) ? POINTER_RELATIVE | POINTER_ACCELERATE : POINTER_ABSOLUTE | POINTER_SCREEN | POINTER_NORAW;
                        if (!e.mouse.relative) {
                            e.mouse.x = max(0, min(e.mouse.x, pScreenPtr->width));
                            e.mouse.y = max(0, min(e.mouse.y, pScreenPtr->height));
                        }
                        valuator_mask_set_double(&mask, 0, (double) e.mouse.x);
                        valuator_mask_set_double(&mask, 1, (double) e.mouse.y);
                        QueuePointerEvents(lorieMouse, MotionNotify, 0, flags, &mask);
                        break;
                    case 1: // BUTTON_LEFT
                    case 2: // BUTTON_MIDDLE
                    case 3: // BUTTON_RIGHT
                        QueuePointerEvents(lorieMouse, e.mouse.down ? ButtonPress : ButtonRelease, e.mouse.detail, POINTER_RELATIVE, nullptr);
                        break;
                    case 4: // BUTTON_SCROLL
                        if (e.mouse.x) {
                            valuator_mask_zero(&mask);
                            valuator_mask_set_double(&mask, 2, (double) e.mouse.x / 120);
                            QueuePointerEvents(lorieMouse, MotionNotify, 0, POINTER_RELATIVE, &mask);
                        }
                        if (e.mouse.y) {
                            valuator_mask_zero(&mask);
                            valuator_mask_set_double(&mask, 3, (double) e.mouse.y / 120);
                            QueuePointerEvents(lorieMouse, MotionNotify, 0, POINTER_RELATIVE, &mask);
                        }
                        break;
                }
                break;
            }
            case EVENT_KEY:
                QueueKeyboardEvents(lorieKeyboard, e.key.state ? KeyPress : KeyRelease, e.key.key);
                break;
            case EVENT_UNICODE: {
                int ks = ucs2keysym((long) e.unicode.code);
                __android_log_print(ANDROID_LOG_DEBUG, "LorieNative", "Trying to input keysym %d\n", ks);
                lorieKeysymKeyboardEvent(ks, TRUE);
                lorieKeysymKeyboardEvent(ks, FALSE);
                break;
            }
            case EVENT_CLIPBOARD_ENABLE:
                lorieEnableClipboardSync(e.clipboardEnable.enable);
                break;
            case EVENT_CLIPBOARD_ANNOUNCE:
                QueueWorkProc(+[](__unused ClientPtr pClient, __unused void *closure) -> Bool {
                    // This must be done only on X server thread.
                    lorieHandleClipboardAnnounce();
                    return TRUE;
                }, nullptr, nullptr);
                lorieWakeServer();
                break;
            case EVENT_CLIPBOARD_SEND: {
                char *data = (char*) calloc(1, e.clipboardSend.count + 1);
                read(conn_fd, data, e.clipboardSend.count);
                data[e.clipboardSend.count] = 0;
                QueueWorkProc(+[](__unused ClientPtr pClient, void *closure) -> Bool {
                    // This must be done only on X server thread.
                    lorieHandleClipboardData((const char*) closure);
                    return TRUE;
                }, nullptr, data);
                lorieWakeServer();
                break;
            }
            case EVENT_RENDERER_WAKEUP_COND: {
                int wakeupFd = ancil_recv_fd(fd);
                if (wakeupFd >= 0)
                    lorieSetRendererWakeupCond(wakeupFd);
                break;
            }
            case EVENT_GPU_COPY_DONE:
                QueueWorkProc(+[](__unused ClientPtr pClient, __unused void *closure) -> Bool {
                    // This must be done only on X server thread (touches present's internal vblank queue).
                    lorieRecheckGpuCopies();
                    return TRUE;
                }, nullptr, nullptr);
                lorieWakeServer();
                break;
            case EVENT_LOCK_KEYS_STATE: {
                auto *copy = (lorieEvent*) calloc(1, sizeof(lorieEvent));
                memcpy(copy, &e, sizeof(e));
                QueueWorkProc(+[](__unused ClientPtr pClient, void *closure) -> Bool {
                    // This must be done only on X server thread (touches XKB state directly).
                    auto *e = (lorieEvent*) closure;
                    lorieSyncLockKeysState(e->lockKeysState.state);
                    free(e);
                    return TRUE;
                }, nullptr, copy);
                lorieWakeServer();
                break;
            }
        }

        int n;
        if (ioctl(fd, FIONREAD, &n) >= 0 && n > sizeof(e))
            goto again;
    }
}

void lorieSendClipboardData(const char* data) {
    if (data && conn_fd != -1) {
        size_t len = strlen(data);
        lorieEvent e = { .clipboardSend = { .t = EVENT_CLIPBOARD_SEND, .count = (uint32_t) len } };
        write(conn_fd, &e, sizeof(e));
        write(conn_fd, data, len);
    }
}

void lorieRequestClipboard(void) {
    if (conn_fd != -1) {
        lorieEvent e = { .type = EVENT_CLIPBOARD_REQUEST };
        write(conn_fd, &e, sizeof(e));
    }
}

bool lorieConnectionAlive(void) {
    if (conn_fd == -1)
        return false;

    // Check if socket is closed or has errors.
    struct pollfd p = { .fd = conn_fd, .events = POLLIN | POLLHUP | POLLERR | POLLRDHUP };
    return !(poll(&p, 1, 0) == 1 && (p.revents & (POLLERR | POLLNVAL | POLLRDHUP | POLLHUP)));
}

void lorieSendSharedServerState(int memfd) {
    if (conn_fd != -1) {
        lorieEvent e = { .type = EVENT_SHARED_SERVER_STATE };
        write(conn_fd, &e, sizeof(e));
        ancil_send_fd(conn_fd, memfd);
    }
}

void lorieRegisterBuffer(LorieBuffer* buffer) {
    unsigned long id = LorieBuffer_description(buffer)->id;
    if (conn_fd == -1 || LorieBufferList_findById(&registeredBuffers, id))
        return; // Already registered

    if (conn_fd != -1 && buffer) {
        lorieEvent e = { .type = EVENT_ADD_BUFFER };
        write(conn_fd, &e, sizeof(e));
        LorieBuffer_sendHandleToUnixSocket(buffer, conn_fd);
        LorieBuffer_addToList(buffer, &registeredBuffers);
        const LorieBuffer_Desc* desc = LorieBuffer_description(buffer);
        log(INFO, "Sent shared buffer width %d stride %d height %d format %d type %d id %llu", desc->width, desc->stride, desc->height, desc->format, desc->type, desc->id);
    }
}

/* ---- Gate A P1 framed REGISTER send ----
 * X server thread is the sole X-side Gate A writer; gateASendMutex serializes
 * the frame + AHB handle as one logical transaction. No callers in P1 (P3
 * admission calls it). */
int lorieGateASendRegister(uint64_t id, uint64_t nonce, uint64_t generation,
                           uint32_t w, uint32_t h, uint32_t stride, uint32_t format,
                           AHardwareBuffer *ahb) {
    struct LorieGateAFrame fr;
    struct LorieGateARegisterBody body;
    int ok = 0;
    if (!lorieGateAProtoEnabled() || conn_fd == -1 || ahb == NULL || id == 0)
        return -1;
    fr.magic = LORIE_GATEA_MAGIC;
    fr.version = LORIE_GATEA_PROTOCOL_VERSION;
    fr.type = LORIE_GATEA_MSG_REGISTER;
    fr.length = sizeof(body);
    fr.reserved = 0;
    fr.nonce = nonce;
    fr.generation = generation;
    fr.bufferId = id;
    body.width = w;
    body.height = h;
    body.stride = stride;
    body.format = format;
    pthread_mutex_lock(&gateASendMutex);
    if (lorieGateAWriteFull(conn_fd, &fr, sizeof(fr)) != (ssize_t)sizeof(fr))
        goto out;
    if (lorieGateAWriteFull(conn_fd, &body, sizeof(body)) != (ssize_t)sizeof(body))
        goto out;
    /* Checked raw-handle send (buffer.c wrapper guards the API-26 symbol;
     * direct NDK calls are forbidden outside buffer.c). */
    if (LorieBuffer_sendRawAHardwareBufferHandleChecked(ahb, conn_fd) != 0)
        goto out;
    ok = 1;
out:
    pthread_mutex_unlock(&gateASendMutex);
    return ok ? 0 : -1;
}

void lorieUnregisterBuffer(LorieBuffer* buffer) {
    unsigned long id;
    if (!buffer || (!LorieBufferList_findById(&registeredBuffers, (id = LorieBuffer_description(buffer)->id))))
        return;  // Not exist or not registered so no need to unregister

    if (conn_fd != -1 && buffer) {
        lorieEvent e = { .removeBuffer = { .t = EVENT_REMOVE_BUFFER, .id = id } };
        write(conn_fd, &e, sizeof(e));
        LorieBuffer_removeFromList(buffer);
    }
}

extern "C" void DDXNotifyFocusChanged(void) {
    if (conn_fd != -1) {
        lorieEvent e = { .type = EVENT_WINDOW_FOCUS_CHANGED };
        write(conn_fd, &e, sizeof(e));
    }
}

extern "C" JNIEXPORT jobject JNICALL
Java_com_termux_x11_CmdEntryPoint_getXConnection(JNIEnv *env, __unused jobject cls) {
    int client[2];
    jclass ParcelFileDescriptorClass = env->FindClass("android/os/ParcelFileDescriptor");
    jmethodID adoptFd = env->GetStaticMethodID(ParcelFileDescriptorClass, "adoptFd", "(I)Landroid/os/ParcelFileDescriptor;");
    socketpair(AF_UNIX, SOCK_STREAM, 0, client);
    QueueWorkProc(+[](__unused ClientPtr pClient, void *closure) -> Bool {
        InputThreadRegisterDev((int) (int64_t) closure, handleLorieEvents, nullptr);
        conn_fd = (int) (int64_t) closure;
        lorieActivityConnected();
        return TRUE;
    }, nullptr, (void*) (int64_t) client[1]);
    lorieWakeServer();

    return env->CallStaticObjectMethod(ParcelFileDescriptorClass, adoptFd, client[0]);
}

extern "C" JNIEXPORT jobject JNICALL
Java_com_termux_x11_CmdEntryPoint_getLogcatOutput(JNIEnv *env, __unused jobject cls) {
    jclass ParcelFileDescriptorClass = env->FindClass("android/os/ParcelFileDescriptor");
    jmethodID adoptFd = env->GetStaticMethodID(ParcelFileDescriptorClass, "adoptFd", "(I)Landroid/os/ParcelFileDescriptor;");
    const char *debug = getenv("TERMUX_X11_DEBUG");
    if (debug && !strcmp(debug, "1")) {
        pthread_t t;
        int p[2];
        pipe(p);
        fchmod(p[1], 0777);
        pthread_create(&t, nullptr, +[](void *arg) -> void* {
            char buffer[4096];
            size_t len;
            while((len = read((int) (int64_t) arg, buffer, 4096)) >=0)
                write(2, buffer, len);
            close((int) (int64_t) arg);
            return nullptr;
        }, (void*) (uint64_t) p[0]);
        return env->CallStaticObjectMethod(ParcelFileDescriptorClass, adoptFd, p[1]);
    }
    return nullptr;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_termux_x11_CmdEntryPoint_connected(__unused JNIEnv *env, __unused jclass clazz) {
    return conn_fd != -1;
}

extern "C" JNIEXPORT void JNICALL
Java_com_termux_x11_CmdEntryPoint_listenForConnections(JNIEnv *env, jobject thiz) {
    int server_fd, client, count;
    struct sockaddr_in address = { .sin_family = AF_INET, .sin_port = htons(PORT), .sin_addr = { .s_addr = INADDR_ANY } };
    int addrlen = sizeof(address);
    jmethodID sendBroadcast = env->GetMethodID(env->GetObjectClass(thiz), "sendBroadcast", "()V");
    uint8_t buffer[512] = {0};
    int reuse = 1;

    // Even in the case if it will fail for some reason everything will work fine
    // But connection will be delayed a bit

    if ((server_fd = socket(AF_INET, SOCK_STREAM, 0)) == 0) {
        log(ERROR, "Socket creation failed: %s", strerror(errno));
        return;
    }

    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEPORT, &reuse, sizeof(reuse));

    if (bind(server_fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
        log(ERROR, "Socket bind failed: %s", strerror(errno));
        close(server_fd);
        return;
    }

    if (listen(server_fd, 5) < 0) {
        log(ERROR, "Socket listen failed: %s", strerror(errno));
        close(server_fd);
        return;
    }

    while(true) {
        if ((client = accept(server_fd, (struct sockaddr *)&address, (socklen_t *)&addrlen)) < 0) {
            log(ERROR, "Socket accept failed: %s", strerror(errno));
            continue;
        }

        if ((count = read(client, buffer, sizeof(buffer))) > 0) {
            if (!memcmp(buffer, MAGIC, min(count, sizeof(MAGIC)))) {
                log(DEBUG, "New client connection!\n");
                env->CallVoidMethod(thiz, sendBroadcast);
            }
        }
        close(client);
    }
}

void abort(void) {
    _exit(134);
}

void exit(int code) {
    _exit(code);
}
