#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <unistd.h>
#include <string.h>
#include <pthread.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/mman.h>
#include <errno.h>
#include <jni.h>
#include <android/looper.h>
#include <wchar.h>
#include <linux/in.h>
#include <arpa/inet.h>
#include <poll.h>
#include "lorie.h"

#pragma clang diagnostic ignored "-Wunknown-pragmas"
#pragma ide diagnostic ignored "cppcoreguidelines-narrowing-conversions"
#pragma ide diagnostic ignored "ConstantFunctionResult"
#define log(prio, ...) __android_log_print(ANDROID_LOG_ ## prio, "LorieNative", __VA_ARGS__)
#define sendEvent(...) do { lorieEvent e = { __VA_ARGS__ }; (void)lorieActivitySendLegacyRecord(&e); } while (0)

extern volatile int conn_fd; // Guarded by lorieActivityWriterMutex for write/close/rebind.
pthread_mutex_t lorieActivityWriterMutex = PTHREAD_MUTEX_INITIALIZER;
bool lorieDebugEnabled = false;

static int lorieActivityWriteLocked(int fd, const void *first, size_t firstLen,
                                    const void *second, size_t secondLen) {
    if (fd < 0) {
        errno = EBADF;
        return -1;
    }
    if (first == NULL || firstLen == 0 || (secondLen != 0 && second == NULL)) {
        errno = EINVAL;
        return -1;
    }
    if (lorieGateAWriteFull(fd, first, firstLen) != (ssize_t)firstLen)
        return -1;
    if (secondLen != 0
        && lorieGateAWriteFull(fd, second, secondLen) != (ssize_t)secondLen)
        return -1;
    return 0;
}

int lorieActivitySendLegacyPayload(const lorieEvent *event,
                                    const void *payload, size_t payloadLen) {
    int fd, rc, savedErrno;
    pthread_mutex_lock(&lorieActivityWriterMutex);
    fd = conn_fd;
    rc = lorieActivityWriteLocked(fd, event, event ? sizeof(*event) : 0,
                                  payload, payloadLen);
    savedErrno = rc == 0 ? 0 : errno;
    pthread_mutex_unlock(&lorieActivityWriterMutex);
    errno = savedErrno;
    return rc;
}

int lorieActivitySendLegacyRecord(const lorieEvent *event) {
    return lorieActivitySendLegacyPayload(event, NULL, 0);
}

int lorieActivitySendLegacyFd(const lorieEvent *event, int sentFd) {
    int fd, rc = -1, savedErrno = EBADF;
    pthread_mutex_lock(&lorieActivityWriterMutex);
    fd = conn_fd;
    if (fd < 0 || sentFd < 0) {
        savedErrno = EBADF;
    } else if (event == NULL) {
        savedErrno = EINVAL;
    } else if (lorieGateAWriteFull(fd, event, sizeof(*event))
               != (ssize_t)sizeof(*event)
               || ancil_send_fd(fd, sentFd) != 0) {
        savedErrno = errno != 0 ? errno : EIO;
    } else {
        rc = 0;
        savedErrno = 0;
    }
    pthread_mutex_unlock(&lorieActivityWriterMutex);
    errno = savedErrno;
    return rc;
}

int lorieActivitySendGateFrame(const struct LorieGateAFrame *frame,
                               const void *body, size_t bodyLen) {
    int fd, rc, savedErrno;
    pthread_mutex_lock(&lorieActivityWriterMutex);
    fd = conn_fd;
    rc = lorieActivityWriteLocked(fd, frame, frame ? sizeof(*frame) : 0,
                                  body, bodyLen);
    savedErrno = errno;
    pthread_mutex_unlock(&lorieActivityWriterMutex);
    errno = savedErrno;
    return rc;
}

// Timestamp of the last real input reaching the X session, from any source. Read/written only
// from the Android main thread via JNI.
static volatile int64_t lastInputTimestampMs = 0;

static int64_t nowMs(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t) ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static struct {
    jclass self;
    jmethodID getInstance, clientConnectedStateChanged, resetIme;
} MainActivity = {0};

static struct {
    jclass self;
    jmethodID forName;
    jmethodID decode;
} Charset = {0};

static struct {
    jclass self;
    jmethodID toString;
} CharBuffer = {0};

static JNIEnv *guienv = NULL; // Must be used only in GUI thread.
static jobject globalThiz = NULL;
static Renderer g_renderer;

/* ---- Gate A P1 renderer-side binding (xcallback/looper thread) ----
 * Bound tuple guards every inbound Gate A frame. This thread NEVER does GL
 * and NEVER sends Gate A frames: REGISTER outcomes go to the GL thread via
 * lorieGateAEnqueueImport, which owns all renderer-side sends. Fatal paths
 * bypass blocking rendezvous (a renderer stuck in a fence wait would hang
 * teardown instead of containing it). */
static pthread_mutex_t gateABindMutex = PTHREAD_MUTEX_INITIALIZER;
static uint64_t gateABoundNonce = 0;
static uint64_t gateABoundGeneration = 0;
static int gateABound = 0;

int lorieGateABoundTuple(uint64_t *nonce, uint64_t *generation) {
    int bound;
    pthread_mutex_lock(&gateABindMutex);
    bound = gateABound;
    if (bound) {
        *nonce = gateABoundNonce;
        *generation = gateABoundGeneration;
    }
    pthread_mutex_unlock(&gateABindMutex);
    return bound;
}

int lorieGateAUnbindTuple(uint64_t nonce, uint64_t generation) {
    int rc = -1;
    pthread_mutex_lock(&gateABindMutex);
    if (gateABound && gateABoundNonce == nonce
        && gateABoundGeneration == generation) {
        gateABound = 0;
        gateABoundNonce = 0;
        gateABoundGeneration = 0;
        rc = 0;
    }
    pthread_mutex_unlock(&gateABindMutex);
    return rc;
}

void lorieGateAWakeRenderer(void) {
    g_renderer.wakeGateA();
}

/* Bind from a freshly mapped shared state (socket-recv ordered before these
 * bytes). Exact version + nonzero tuple required; anything else leaves the
 * previous binding untouched (P1: any live imports at rebind time are fatal,
 * checked by the caller via lorieGateAImportBusy). */
static void gateABindFromState(struct lorie_shared_server_state *state) {
    uint32_t version;
    uint64_t nonce, generation;
    int bound;
    if (state == NULL)
        return;
    version = lorieGateALoadU32Acquire(&state->gateA.protocolVersion);
    nonce = lorieGateALoadU64Acquire(&state->gateA.sessionNonce);
    generation = lorieGateALoadU64Acquire(&state->gateA.generation);
    pthread_mutex_lock(&gateABindMutex);
    if (version == LORIE_GATEA_PROTOCOL_VERSION && nonce != 0 && generation != 0) {
        /* Re-share over live imports would orphan renderer GL objects this
         * thread cannot destroy: fail closed instead. Clean shares always
         * arrive with empty registries (HUP halts otherwise). */
        if (gateABound && (nonce != gateABoundNonce || generation != gateABoundGeneration)
            && lorieGateAImportBusy())
            lorieGateAFatalHalt("r-rebind-busy", LORIE_GATEA_FAIL_GENERATION);
        gateABoundNonce = nonce;
        gateABoundGeneration = generation;
        gateABound = 1;
    } else {
        gateABound = 0;
    }
    bound = gateABound;
    pthread_mutex_unlock(&gateABindMutex);
    log(INFO, "GATEA_BIND version=%u nonce=%llu generation=%llu bound=%d",
        version, (unsigned long long)nonce, (unsigned long long)generation, bound);
}

static int gateAPeekIsGateA(int fd) {
    uint32_t magic = 0;
    return recv(fd, &magic, sizeof(magic), MSG_PEEK) == (ssize_t)sizeof(magic)
        && magic == LORIE_GATEA_MAGIC;
}

/* Consume a REGISTER body after the common frame header has been read. The AHB
 * handle is consumed in all cases so the stream cannot desynchronize. */
static void gateAHandleRegister(int fd, const struct LorieGateAFrame *fr,
                                uint64_t nonce, uint64_t generation) {
    struct LorieGateARegisterBody body;
    AHardwareBuffer *ahb = NULL;
    AHardwareBuffer_Desc desc = {};
    uint64_t fingerprint;
    if (fr->length != sizeof(body) || fr->bufferId == 0)
        lorieGateAFatalHalt("r-bad-register", LORIE_GATEA_FAIL_PROTOCOL);
    if (lorieGateAReadFull(fd, &body, sizeof(body)) != (ssize_t)sizeof(body))
        lorieGateAFatalHalt("r-short-register", LORIE_GATEA_FAIL_PROTOCOL);
    if (LorieBuffer_recvAHardwareBufferHandleFromUnixSocket(fd, &ahb) != 0 || ahb == NULL) {
        if (lorieGateAEnqueueImport(fr->bufferId, nonce, generation, 0, NULL,
                                    LORIE_GATEA_FAIL_IMPORT) != 0)
            lorieGateAFatalHalt("r-import-enqueue", LORIE_GATEA_FAIL_IMPORT);
        return;
    }
    if (body.width == 0 || body.height == 0 || (int32_t)body.stride < (int32_t)body.width
        || (body.format != AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM
            && body.format != AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM)) {
        lorieGateAReleaseAhb(ahb);
        if (lorieGateAEnqueueImport(fr->bufferId, nonce, generation, 0, NULL,
                                    LORIE_GATEA_FAIL_PROTOCOL) != 0)
            lorieGateAFatalHalt("r-import-enqueue", LORIE_GATEA_FAIL_PROTOCOL);
        return;
    }
    LorieBuffer_describeAHardwareBuffer(ahb, &desc);
    if (desc.width != (int32_t)body.width || desc.height != (int32_t)body.height
        || desc.stride != (int32_t)body.stride || desc.format != (int32_t)body.format) {
        lorieGateAReleaseAhb(ahb);
        if (lorieGateAEnqueueImport(fr->bufferId, nonce, generation, 0, NULL,
                                    LORIE_GATEA_FAIL_PROTOCOL) != 0)
            lorieGateAFatalHalt("r-import-enqueue", LORIE_GATEA_FAIL_PROTOCOL);
        return;
    }
    fingerprint = lorieGateAFingerprint(body.width, body.height, body.stride, body.format);
    if (lorieGateAEnqueueImport(fr->bufferId, nonce, generation, fingerprint, ahb,
                                LORIE_GATEA_FAIL_NONE) != 0) {
        lorieGateAReleaseAhb(ahb);
        lorieGateAFatalHalt("r-import-overflow", LORIE_GATEA_FAIL_IMPORT);
    }
}

/* Consume one X→renderer Gate A frame after a magic peek. REGISTER carries an
 * AHB handle; retirement controls are enqueued to the owning GL thread. */
static void gateAHandleFrame(int fd) {
    struct LorieGateAFrame fr;
    uint64_t nonce = 0, generation = 0;
    if (!lorieGateABoundTuple(&nonce, &generation))
        return;
    if (lorieGateAReadFull(fd, &fr, sizeof(fr)) != (ssize_t)sizeof(fr))
        lorieGateAFatalHalt("r-short-frame", LORIE_GATEA_FAIL_PROTOCOL);
    if (fr.magic != LORIE_GATEA_MAGIC || fr.version != LORIE_GATEA_PROTOCOL_VERSION
        || fr.reserved != 0 || fr.nonce != nonce || fr.generation != generation)
        lorieGateAFatalHalt("r-bad-frame", LORIE_GATEA_FAIL_PROTOCOL);
    log(INFO, "GATEA_HANDLE type=%u id=%llu", fr.type, (unsigned long long)fr.bufferId);
    switch (fr.type) {
    case LORIE_GATEA_MSG_REGISTER:
        gateAHandleRegister(fd, &fr, nonce, generation);
        return;
    case LORIE_GATEA_MSG_UNREGISTER: {
        struct LorieGateAUnregisterBody body;
        if (fr.length != sizeof(body) || fr.bufferId == 0
            || lorieGateAReadFull(fd, &body, sizeof(body)) != (ssize_t)sizeof(body))
            lorieGateAFatalHalt("r-bad-unregister", LORIE_GATEA_FAIL_PROTOCOL);
        if (lorieGateAEnqueueControl(fr.type, fr.bufferId, nonce, generation,
                                     body.lastSubmittedSerial) != 0)
            lorieGateAFatalHalt("r-unregister-overflow", LORIE_GATEA_FAIL_UNREGISTER);
        return;
    }
    case LORIE_GATEA_MSG_GENERATION_CLOSE: {
        struct LorieGateAGenerationCloseBody body;
        if (fr.length != sizeof(body) || fr.bufferId != 0
            || lorieGateAReadFull(fd, &body, sizeof(body)) != (ssize_t)sizeof(body))
            lorieGateAFatalHalt("r-bad-generation-close", LORIE_GATEA_FAIL_PROTOCOL);
        if (lorieGateAEnqueueControl(fr.type, 0, nonce, generation,
                                     body.lastPublishedSerial) != 0)
            lorieGateAFatalHalt("r-close-overflow", LORIE_GATEA_FAIL_CLOSE);
        return;
    }
    default:
        lorieGateAFatalHalt("r-unexpected-frame", LORIE_GATEA_FAIL_PROTOCOL);
    }
}

static jclass FindClassOrDie(JNIEnv *env, const char* name) {
    jclass clazz = env->FindClass(name);
    if (!clazz) {
        char buffer[1024] = {0};
        sprintf(buffer, "class %s not found", name);
        log(ERROR, "%s", buffer);
        env->FatalError(buffer);
        return NULL;
    }

    return (jclass) env->NewGlobalRef(clazz);
}

static jmethodID FindMethodOrDie(JNIEnv *env, jclass clazz, const char* name, const char* signature, jboolean isStatic) {
    jmethodID method = isStatic ? env->GetStaticMethodID(clazz, name, signature) : env->GetMethodID(clazz, name, signature);
    if (!method) {
        char buffer[1024] = {0};
        sprintf(buffer, "method %s %s not found", name, signature);
        log(ERROR, "%s", buffer);
        env->FatalError(buffer);
        return NULL;
    }

    return method;
}

static jboolean requestConnection(__unused JNIEnv *env, __unused jclass clazz) {
#define check(cond, fmt, ...) if ((cond)) do { __android_log_print(ANDROID_LOG_ERROR, "requestConnection", fmt, ## __VA_ARGS__); goto end; } while (0)
    bool sent = JNI_FALSE;
    // We do not want to block GUI thread for a long time so we will set timeout to 20 msec.
    struct sockaddr_in server = { .sin_family = AF_INET, .sin_port = htons(PORT) };
    server.sin_addr.s_addr = inet_addr("127.0.0.1");
    int so_error, sock = socket(AF_INET, SOCK_STREAM, 0);
    check(sock < 0, "Could not create socket: %s", strerror(errno));
    check(fcntl(sock, F_SETFL, O_NONBLOCK) < 0, "failed to set socket non-block: %s", strerror(errno));
    int r;
    r = connect(sock, (struct sockaddr *)&server, sizeof(server));
    check(r < 0 && errno != EINPROGRESS, "failed to connect socket: %s", strerror(errno));
    if (r < 0 && errno == EINPROGRESS) {
        // Connection is in progress; use poll to wait for it
        struct pollfd pfd = { .fd = sock, .events = POLLOUT };
        r = poll(&pfd, 1, 20);  // timeout set to 50ms
        if (!r) goto end;
        // check(!r, "Connection timed out after 20ms."); // We do not want to flood logcat with this message
        check(r < 0, "poll failed: %s", strerror(errno));
        socklen_t len = sizeof(so_error);
        check(getsockopt(sock, SOL_SOCKET, SO_ERROR, &so_error, &len) < 0, "getsockopt failed: %s", strerror(errno));
        if (so_error == ECONNREFUSED) goto end; // Regular situation which happens often if server is not started. No need to spam logcat with this.
        check(so_error != 0, "Connection failed: %s", strerror(so_error));

        check(write(sock, MAGIC, sizeof(MAGIC)) < 0, "failed to send message: %s", strerror(errno));
        sent = JNI_TRUE;
        goto end;
    }

    check(1, "something went wrong: %s, %s", strerror(errno), strerror(r));

    end: if (sock >= 0) close(sock);
    return sent;
#undef errorReturn
}

static void connect_(__unused JNIEnv* env, __unused jobject cls, jint fd);
static void nativeInit(JNIEnv *env, jobject thiz) {
    JavaVM* vm;
    if (!Charset.self) {
        // Init clipboard-related JNI stuff
        Charset.self = FindClassOrDie(env, "java/nio/charset/Charset");
        Charset.forName = FindMethodOrDie(env, Charset.self, "forName", "(Ljava/lang/String;)Ljava/nio/charset/Charset;", JNI_TRUE);
        Charset.decode = FindMethodOrDie(env, Charset.self, "decode", "(Ljava/nio/ByteBuffer;)Ljava/nio/CharBuffer;", JNI_FALSE);

        CharBuffer.self = FindClassOrDie(env,  "java/nio/CharBuffer");
        CharBuffer.toString = FindMethodOrDie(env, CharBuffer.self, "toString", "()Ljava/lang/String;", JNI_FALSE);

        MainActivity.self = FindClassOrDie(env,  "com/termux/x11/MainActivity");
        MainActivity.getInstance = FindMethodOrDie(env, MainActivity.self, "getInstance", "()Lcom/termux/x11/MainActivity;", JNI_TRUE);
        MainActivity.clientConnectedStateChanged = FindMethodOrDie(env, MainActivity.self, "clientConnectedStateChanged", "()V", JNI_FALSE);
        MainActivity.resetIme = FindMethodOrDie(env, env->GetObjectClass(thiz), "resetIme", "()V", JNI_FALSE);
    }

    g_renderer.init(env);

    env->GetJavaVM(&vm);
    vm->AttachCurrentThread(&guienv, NULL);
    globalThiz = guienv->NewGlobalRef(thiz);
    connect_(NULL, NULL, -1);
}

static int xcallback(int fd, int events, __unused void* data) {
    JNIEnv *env = guienv;
    jobject thiz = globalThiz;

    if (events & (ALOOPER_EVENT_ERROR | ALOOPER_EVENT_HANGUP)) {
        jobject instance = env->CallStaticObjectMethod(MainActivity.self, MainActivity.getInstance);
        if (instance)
            env->CallVoidMethod(instance, MainActivity.clientConnectedStateChanged);

        /* Bound tuple is the Activity-side enable signal. The APK process
         * never inherits TERMUX_X11_GATEA_PROTO from the X launcher. */
        {
            uint64_t bn = 0, bg = 0;
            if (lorieGateABoundTuple(&bn, &bg))
                lorieGateAFatalHalt("r-hup", LORIE_GATEA_FAIL_GENERATION);
        }

        ALooper_removeFd(ALooper_forThread(), fd);
        pthread_mutex_lock(&lorieActivityWriterMutex);
        if (conn_fd == fd) {
            conn_fd = -1;
            close(fd);
        }
        pthread_mutex_unlock(&lorieActivityWriterMutex);
        g_renderer.setSharedState(NULL);
        g_renderer.removeAllBuffers();
        log(DEBUG, "disconnected");
        return 1;
    }

    if (conn_fd != -1) {
        lorieEvent e = {0};

        /* Magic peek is valid only after the shared tuple is bound. Do not
         * require Activity getenv: am start never has TERMUX_X11_GATEA_PROTO.
         * Peek-true + unbound must not fall through to lorieEvent read. */
        if (gateAPeekIsGateA(conn_fd)) {
            uint64_t bn = 0, bg = 0;
            int bound = lorieGateABoundTuple(&bn, &bg);
            log(INFO, "GATEA_PEEK magic=1 bound=%d nonce=%llu generation=%llu",
                bound, (unsigned long long)bn, (unsigned long long)bg);
            if (bound) {
                /* Process exactly one complete Gate record for this looper
                 * callback. Level-triggered delivery schedules another callback
                 * if X already queued more bytes; never block on a second peek. */
                gateAHandleFrame(conn_fd);
                return 1;
            }
            lorieGateAFatalHalt("r-unbound-frame", LORIE_GATEA_FAIL_PROTOCOL);
        }
        if (read(conn_fd, &e, sizeof(e)) == sizeof(e)) {
            switch(e.type) {
                case EVENT_CLIPBOARD_SEND: {
                    if (!e.clipboardSend.count)
                        break;
                    char clipboard[e.clipboardSend.count + 1];
                    memset(clipboard, 0, e.clipboardSend.count + 1);
                    read(conn_fd, clipboard, sizeof(clipboard));
                    clipboard[e.clipboardSend.count] = 0;
                    log(DEBUG, "Clipboard content (%zu symbols) is %s", strlen(clipboard), clipboard);
                    jmethodID id = env->GetMethodID(env->GetObjectClass(thiz), "setClipboardText","(Ljava/lang/String;)V");
                    jobject bb = env->NewDirectByteBuffer(clipboard, strlen(clipboard));
                    jobject charset = env->CallStaticObjectMethod(Charset.self, Charset.forName, env->NewStringUTF("UTF-8"));
                    jobject cb = env->CallObjectMethod(charset, Charset.decode, bb);
                    env->DeleteLocalRef(bb);

                    jstring str = (jstring) env->CallObjectMethod(cb, CharBuffer.toString);
                    env->CallVoidMethod(thiz, id, str);
                    break;
                }
                case EVENT_CLIPBOARD_REQUEST: {
                    env->CallVoidMethod(thiz, env->GetMethodID(env->GetObjectClass(thiz), "requestClipboard", "()V"));
                    break;
                }
                case EVENT_SHARED_SERVER_STATE: {
                    struct lorie_shared_server_state* state = NULL;
                    int stateFd = ancil_recv_fd(conn_fd);

                    if (stateFd < 0)
                        break;

                    state = (struct lorie_shared_server_state*) mmap(NULL, sizeof(*state), PROT_READ|PROT_WRITE, MAP_SHARED, stateFd, 0);
                    if (!state || state == MAP_FAILED) {
                        log(ERROR, "Failed to map server state: %s", strerror(errno));
                        state = NULL;
                    }

                    /* Bind from the mapped tuple, not Activity getenv. X is
                     * the only process that sees TERMUX_X11_GATEA_PROTO=1;
                     * a zero generation leaves the previous binding untouched. */
                    gateABindFromState(state);

                    g_renderer.setSharedState(state);

                    close(stateFd); // Closing file descriptor does not unmmap shared memory fragment.
                    break;
                }
                case EVENT_ADD_BUFFER: {
                    static LorieBuffer* buffer = NULL;
                    const LorieBuffer_Desc* desc;
                    LorieBuffer_recvHandleFromUnixSocket(conn_fd, &buffer);
                    if (!buffer) {
                        log(ERROR, "Failed to receive shared buffer");
                        break;
                    }
                    desc = LorieBuffer_description(buffer);
                    log(INFO, "Received shared buffer width %d stride %d height %d format %d type %d id %llu", desc->width, desc->stride, desc->height, desc->format, desc->type, desc->id);
                    g_renderer.addBuffer(buffer);
                    break;
                }
                case EVENT_REMOVE_BUFFER: {
                    g_renderer.removeBuffer(e.removeBuffer.id);
                    break;
                }
                case EVENT_WINDOW_FOCUS_CHANGED: {
                    env->CallVoidMethod(thiz, MainActivity.resetIme);
                }
            }
        }
    }

    return 1;
}

static void connect_(__unused JNIEnv* env, __unused jobject cls, jint fd) {
    int oldFd;
    pthread_mutex_lock(&lorieActivityWriterMutex);
    oldFd = conn_fd;
    if (oldFd != -1) {
        ALooper_removeFd(ALooper_forThread(), oldFd);
        conn_fd = -1;
        close(oldFd);
    }
    pthread_mutex_unlock(&lorieActivityWriterMutex);
    if (oldFd != -1) {
        g_renderer.setSharedState(NULL);
        g_renderer.removeAllBuffers();
        log(DEBUG, "disconnected");
    }

    if (fd != -1) {
        pthread_mutex_lock(&lorieActivityWriterMutex);
        conn_fd = fd;
        pthread_mutex_unlock(&lorieActivityWriterMutex);
        ALooper_addFd(ALooper_forThread(), fd, 0,
                      ALOOPER_EVENT_INPUT | ALOOPER_EVENT_ERROR
                      | ALOOPER_EVENT_HANGUP, xcallback, NULL);

        /* Give the X server the renderer wakeup fd as one serialized logical
         * record so no UI or renderer writer can split event from SCM_RIGHTS. */
        lorieEvent e = { .type = EVENT_RENDERER_WAKEUP_COND };
        if (lorieActivitySendLegacyFd(&e, g_renderer.getWakeupCondFd()) != 0)
            log(ERROR, "Failed to send renderer wakeup fd: %s", strerror(errno));

        log(DEBUG, "XCB connection is successfull");
    }
}

static void startLogcat(JNIEnv *env, __unused jobject cls, jint fd) {
    log(DEBUG, "Starting logcat with output to given fd");
    lorieDebugEnabled = true;

    switch(fork()) {
        case -1:
            log(ERROR, "fork: %s", strerror(errno));
            return;
        case 0:
            dup2(fd, 1);
            dup2(fd, 2);
            prctl(PR_SET_PDEATHSIG, SIGTERM);
            char buf[64] = {0};
            sprintf(buf, "--pid=%d", getppid());
            execl("/system/bin/logcat", "logcat", buf, NULL);
            log(ERROR, "exec logcat: %s", strerror(errno));
            env->FatalError("Exiting");
    }
}

static void sendTextEvent(JNIEnv *env, __unused jobject thiz, jbyteArray text) {
    lastInputTimestampMs = nowMs();
    if (conn_fd != -1 && text) {
        jsize length = env->GetArrayLength(text);
        jbyte *str = env->GetByteArrayElements(text, NULL);
        char *p = (char*) str;
        mbstate_t mbstate = { 0 };
        if (!length)
            return;

        log(DEBUG, "Parsing text: %.*s", length, str);

        while (*p) {
            wchar_t wc;
            size_t len = mbrtowc(&wc, p, MB_CUR_MAX, &mbstate);

            if (len == (size_t)-1 || len == (size_t)-2) {
                log(ERROR, "Invalid UTF-8 sequence encountered");
                break;
            }

            if (len == 0)
                break;

            log(DEBUG, "Sending unicode event: %lc (U+%X)", wc, wc);
            lorieEvent e = { .unicode = { .t = EVENT_UNICODE, .code = (uint32_t) wc } };
            (void)lorieActivitySendLegacyRecord(&e);
            p += len;
            if (p - (char*) str >= length)
                break;
            usleep(2500);
        }

        env->ReleaseByteArrayElements(text, str, JNI_ABORT);
    }
}

JNIEXPORT jint JNI_OnLoad(JavaVM *vm, __unused void *reserved) {
    JNIEnv* env;
    static JNINativeMethod methods[] = {
            {"nativeInit", "()V", (void *)&nativeInit},
            {"surfaceChanged", "(Landroid/view/Surface;)V", (void *) +[](JNIEnv *env, __unused jobject thiz, jobject sfc) {
                g_renderer.setWindow(env, sfc);
            }},
            {"setViewport", "(IIIIIII)V", (void *) +[](__unused JNIEnv *env, __unused jclass clazz, jint x, jint y, jint w, jint h, jint ew, jint eh, jint hidden) {
                g_renderer.setViewport(x, y, w, h, ew, eh, hidden);
            }},
            {"setRendererZoom", "(I)V", (void *) +[](__unused JNIEnv *env, __unused jclass clazz, jint percent) {
                g_renderer.setZoom(percent);
            }},
            {"setFiltering", "(I)V", (void *) +[](__unused JNIEnv* env, __unused jobject self, jint filtering) {
                g_renderer.setFiltering(filtering);
            }},
            {"connect", "(I)V", (void *)&connect_},
            {"connected", "()Z", (void *) +[](__unused JNIEnv* env, __unused jclass clazz) -> jboolean {
                return conn_fd != -1;
            }},
            {"startLogcat", "(I)V", (void *)&startLogcat},
            {"setClipboardSyncEnabled", "(ZZ)V", (void *) +[](__unused JNIEnv* env, __unused jobject cls, jboolean enable, __unused jboolean ignored) {
                sendEvent(.clipboardEnable = { .t = EVENT_CLIPBOARD_ENABLE, .enable = enable });
            }},
            {"sendClipboardAnnounce", "()V", (void *) +[](__unused JNIEnv *env, __unused jobject thiz) {
                sendEvent(.type = EVENT_CLIPBOARD_ANNOUNCE);
            }},
            {"sendClipboardEvent", "([B)V", (void *) +[](JNIEnv *env, __unused jobject thiz, jbyteArray text) {
                if (conn_fd != -1 && text) {
                    jsize length = env->GetArrayLength(text);
                    jbyte* str = env->GetByteArrayElements(text, NULL);
                    lorieEvent e = { .clipboardSend = {
                        .t = EVENT_CLIPBOARD_SEND, .count = (uint32_t) length } };
                    (void)lorieActivitySendLegacyPayload(&e, str, (size_t)length);
                    env->ReleaseByteArrayElements(text, str, JNI_ABORT);
                }
            }},
            {"sendWindowChange", "(IIILjava/lang/String;)V", (void *) +[](__unused JNIEnv* env, __unused jobject cls, jint width, jint height, jint framerate, jstring jname) {
                if (conn_fd != -1) {
                    const char *name = (!jname || width <= 0 || height <= 0) ? NULL : env->GetStringUTFChars(jname, JNI_FALSE);
                    size_t nameLen = name ? strlen(name) : 0;
                    lorieEvent e = { .screenSize = {
                        .t = EVENT_SCREEN_SIZE, .width = (uint16_t) width,
                        .height = (uint16_t) height,
                        .framerate = (uint16_t) framerate,
                        .name_size = nameLen } };
                    (void)lorieActivitySendLegacyPayload(&e, name, nameLen);
                    if (name)
                        env->ReleaseStringUTFChars(jname, name);
                }
            }},
            {"sendMouseEvent", "(FFIZZ)V", (void *) +[](__unused JNIEnv* env, __unused jobject cls, jfloat x, jfloat y, jint which_button, jboolean button_down, jboolean relative) {
                lastInputTimestampMs = nowMs();
                if (conn_fd != -1) {
                    if (which_button > 0)
                        env->CallVoidMethod(globalThiz, MainActivity.resetIme);
                    sendEvent(.mouse = { .t = EVENT_MOUSE, .x = x, .y = y, .detail = (uint8_t) which_button, .down = button_down, .relative = relative });
                }
            }},
            {"sendTouchEvent", "(IIII)V", (void *) +[](__unused JNIEnv* env, __unused jobject cls, jint action, jint id, jint x, jint y) {
                lastInputTimestampMs = nowMs();
                if (action != -1)
                    sendEvent(.touch = { .t = EVENT_TOUCH, .type = (uint16_t) action, .id = (uint16_t) id, .x = (uint16_t) x, .y = (uint16_t) y });
            }},
            {"sendStylusEvent", "(FFIIIIIZZ)V", (void *) +[](__unused JNIEnv *env, __unused jobject thiz, jfloat x, jfloat y, jint pressure, jint tilt_x, jint tilt_y, jint orientation, jint buttons, jboolean eraser, jboolean mouse) {
                lastInputTimestampMs = nowMs();
                if (conn_fd != -1) {
                    env->CallVoidMethod(globalThiz, MainActivity.resetIme);
                    sendEvent(.stylus = { .t = EVENT_STYLUS, .x = x, .y = y, .pressure = (uint16_t) pressure, .tilt_x = (int8_t) tilt_x, .tilt_y = (int8_t) tilt_y, .orientation = (int16_t) orientation, .buttons = (uint8_t) buttons, .eraser = eraser, .mouse = mouse });
                }
            }},
            {"requestStylusEnabled", "(Z)V", (void *) +[](__unused JNIEnv *env, __unused jclass clazz, jboolean enabled) {
                sendEvent(.stylusEnable = { .t = EVENT_STYLUS_ENABLE, .enable = enabled });
            }},
            {"sendLockKeysState", "(I)V", (void *) +[](__unused JNIEnv *env, __unused jclass clazz, jint state) {
                sendEvent(.lockKeysState = { .t = EVENT_LOCK_KEYS_STATE, .state = (uint8_t) state });
            }},
            {"sendKeyEvent", "(IIZ)Z", (void *) +[](__unused JNIEnv* env, __unused jobject cls, jint scan_code, jint key_code, jboolean key_down) -> jboolean {
                lastInputTimestampMs = nowMs();
                if (conn_fd != -1) {
                    int code = (scan_code) ?: android_to_linux_keycode[key_code];
                    sendEvent(.key = { .t = EVENT_KEY, .key = (uint16_t) (code + 8), .state = key_down });
                }
                return true;
            }},
            {"sendTextEvent", "([B)V", (void *)&sendTextEvent},
            {"requestConnection", "()Z", (void *)&requestConnection},
            {"getLastInputTimestamp", "()J", (void *) +[](__unused JNIEnv* env, __unused jclass clazz) -> jlong {
                return (jlong) lastInputTimestampMs;
            }},
            {"markUserActivity", "()V", (void *) +[](__unused JNIEnv* env, __unused jclass clazz) {
                lastInputTimestampMs = nowMs();
            }},
    };
    vm->AttachCurrentThread(&env, NULL);
    jclass cls = env->FindClass("com/termux/x11/LorieView");
    env->RegisterNatives(cls, methods, sizeof(methods)/sizeof(methods[0]));

    return JNI_VERSION_1_6;
}


// It is needed to redirect stderr to logcat
static void* stderrToLogcatThread(__unused void* cookie) {
    FILE *fp;
    int p[2];
    size_t len;
    char *line = NULL;
    pipe(p);

    fp = fdopen(p[0], "r");

    dup2(p[1], 2);
    dup2(p[1], 1);
    while ((getline(&line, &len, fp)) != -1) {
        log(DEBUG, "%s%s", line, (line[len - 1] == '\n') ? "" : "\n");
    }

    return NULL;
}

extern char* __progname;
__attribute__((constructor)) static void init(void) {
    pthread_t t;
    if (!strcmp(__progname, "com.termux.x11"))
        pthread_create(&t, NULL, stderrToLogcatThread, NULL);
}
