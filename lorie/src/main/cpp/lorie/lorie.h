#pragma once

#include <android/hardware_buffer.h>
#include <android/native_window_jni.h>
#include <android/choreographer.h>
#include <android/log.h>

#include <stdbool.h>
#include <X11/Xdefs.h>
#include <X11/keysymdef.h>
#include <jni.h>
#include <screenint.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/socket.h>
#include "linux/input-event-codes.h"
#include "buffer.h"
#include "b3a_telemetry.h"

#define PORT 7892
#define MAGIC "0xDEADBEEF"

#ifdef __cplusplus
extern "C" {
#endif

struct lorie_shared_server_state;

void lorieConfigureNotify(int width, int height, int framerate, size_t name_size, char* name);
void lorieEnableClipboardSync(Bool enable);
void lorieSendClipboardData(const char* data);
void lorieInitClipboard(void);
void lorieRequestClipboard(void);
void lorieHandleClipboardAnnounce(void);
void lorieHandleClipboardData(const char* data);
void lorieSetStylusEnabled(Bool enabled);
void lorieSyncLockKeysState(uint8_t state);
void lorieWakeServer(void);
void lorieRecheckGpuCopies(void);
void lorieChoreographerFrameCallback(__unused long t, AChoreographer* d);
void lorieActivityConnected(void);
void lorieInstallFlightRecorder(void);
void lorieDumpFlightRecorder(const char *why);
void lorieSendSharedServerState(int memfd);
void lorieRegisterBuffer(LorieBuffer* buffer);
void lorieUnregisterBuffer(LorieBuffer* buffer);
bool lorieConnectionAlive(void);
extern bool lorieDebugEnabled; // Set in activity.cpp's startLogcat, only called when TERMUX_X11_DEBUG=1.
void lorieSetRendererWakeupCond(int fd);

__unused void rendererTestCapabilities(int* legacy_drawing, int* gpu_present_disabled);

static inline __always_inline void lorie_mutex_lock(pthread_mutex_t* mutex, pid_t* lockingPid) {
    // Unfortunately there is no robust mutexes in bionic.
    // Posix does not define any valid way to unlock stuck non-robust mutex
    // so in the case if renderer or X server process unexpectedly die with locked mutex
    // we will simply reinitialize it.
    struct timespec ts = {0};
    while(true) {
        clock_gettime(CLOCK_MONOTONIC, &ts);

        // 33 msec is enough to complete any drawing operation on both X server and renderer side
        // In the case if mutex is locked most likely other thread died with the mutex locked
        ts.tv_nsec += 33UL * 1000000UL;
        if (ts.tv_nsec >= 1000000000L) {
            ts.tv_sec  += ts.tv_nsec / 1000000000L;
            ts.tv_nsec  = ts.tv_nsec % 1000000000L;
        }

        int ret = pthread_mutex_timedlock(mutex, &ts);
        if (ret == ETIMEDOUT) {
            if (*lockingPid == getpid() || lorieConnectionAlive())
                continue;

            pthread_mutexattr_t attr;
            pthread_mutex_t initializer = PTHREAD_MUTEX_INITIALIZER;
            pthread_mutexattr_init(&attr);
            pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
            pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
            memcpy(mutex, &initializer, sizeof(initializer));
            pthread_mutex_init(mutex, &attr);
            // Mutex will be locked fine on the next iteration
        } else {
            *lockingPid = getpid();
            return;
        }
    }
}

static inline __always_inline void lorie_mutex_unlock(pthread_mutex_t* mutex, pid_t* lockingPid) {
    *lockingPid = 0;
    pthread_mutex_unlock(mutex);
}

typedef enum {
    EVENT_UNKNOWN __unused = 0,
    EVENT_SHARED_SERVER_STATE,
    EVENT_ADD_BUFFER,
    EVENT_REMOVE_BUFFER,
    EVENT_SCREEN_SIZE,
    EVENT_TOUCH,
    EVENT_MOUSE,
    EVENT_KEY,
    EVENT_STYLUS,
    EVENT_STYLUS_ENABLE,
    EVENT_UNICODE,
    EVENT_CLIPBOARD_ENABLE,
    EVENT_CLIPBOARD_ANNOUNCE,
    EVENT_CLIPBOARD_REQUEST,
    EVENT_CLIPBOARD_SEND,
    EVENT_WINDOW_FOCUS_CHANGED,
    EVENT_RENDERER_WAKEUP_COND,
    EVENT_GPU_COPY_DONE,
    EVENT_LOCK_KEYS_STATE,
} eventType;

typedef union {
    uint8_t type;
    struct {
        uint8_t t;
        uint16_t width, height, framerate;
        size_t name_size;
        char *name;
    } screenSize;
    struct {
        uint8_t t;
        unsigned long id;
    } removeBuffer;
    struct {
        uint8_t t;
        uint16_t type, id, x, y;
    } touch;
    struct {
        uint8_t t;
        float x, y;
        uint8_t detail, down, relative;
    } mouse;
    struct {
        uint8_t t;
        uint16_t key;
        uint8_t state;
    } key;
    struct {
        uint8_t t;
        float x, y;
        uint16_t pressure;
        int8_t tilt_x, tilt_y;
        int16_t orientation;
        uint8_t buttons, eraser, mouse;
    } stylus;
    struct {
        uint8_t t, enable;
    } stylusEnable;
    struct {
        uint8_t t;
        uint32_t code;
    } unicode;
    struct {
        uint8_t t;
        uint8_t enable;
    } clipboardEnable;
    struct {
        uint8_t t;
        uint32_t count;
    } clipboardSend;
    struct {
        uint8_t t;
        uint8_t state; // bit0 = Caps Lock, bit1 = Num Lock, bit2 = Scroll Lock
    } lockKeysState;
} lorieEvent;

typedef struct { int16_t x1, y1, x2, y2; } LorieGpuCopyRect;

#define LORIE_GPU_COPY_MAX_RECTS 16
#define LORIE_GPU_COPY_QUEUE_CAPACITY 8

#define LORIE_GPU_OP_COPY      0
#define LORIE_GPU_OP_SOLID     1
#define LORIE_GPU_OP_COMPOSITE 2

typedef struct {
    uint64_t serial;
    uint64_t srcBufferId;
    uint64_t dstBufferId;
    uint32_t telemetryIndex;
    int16_t xOff, yOff;
    uint16_t numRects;
    uint8_t op;
    uint8_t dstIsRgba;
    uint32_t color; /* X11 0x00RRGGBB for SOLID */
    LorieGpuCopyRect rects[LORIE_GPU_COPY_MAX_RECTS];
} LorieGpuCopyEntry;

/* ==========================================================================
 * Gate A P0 foundation — frozen protocol ABI, atomics, fatal/waiter framing.
 * R3 authority: GATE-A-PROTOCOL-ABI-REVIEW-R3-20260912.md.
 *
 * ADDITIVE ONLY. No production call site uses anything below yet, so
 * DEFAULT-OFF behavior is trivially == current D0a. P1+ wires call sites;
 * P5 rewires queue publication/consumption to the accessors. Shared fields
 * are NEVER touched directly (no volatile-only or ordinary load/store sync).
 * ========================================================================== */

#if defined(__cplusplus)
#define LORIE_GATEA_STATIC_ASSERT(cond, msg) static_assert(cond, msg)
#define LORIE_GATEA_ALIGNOF(t) alignof(t)
#else
#define LORIE_GATEA_STATIC_ASSERT(cond, msg) _Static_assert(cond, msg)
#define LORIE_GATEA_ALIGNOF(t) _Alignof(t)
#endif

/* Prototype feature flag: exact "1" only. No call site yet (P3 admits later). */
static inline __always_inline int lorieGateAProtoEnabled(void) {
    const char *e = getenv("TERMUX_X11_GATEA_PROTO");
    return e != NULL && e[0] == '1' && e[1] == '\0';
}

/* Terminal RESULT is always derived, never stored per entry. */
typedef enum {
    LORIE_GATEA_RESULT_NONE = 0,            /* not terminal for this serial */
    LORIE_GATEA_RESULT_SUCCESS = 1,
    LORIE_GATEA_RESULT_FAILED_QUIESCED = 2, /* quiesced; generation ends, no replay */
    LORIE_GATEA_RESULT_FATAL = 3,           /* poisoned; terminate session */
} LorieGateAResult;

/* Failure/reason codes. 0 is reserved for "none"; every real code is nonzero
 * so a single nonzero word doubles as the fatal flag (R3 §3 refinement:
 * the CAS word carries flag+reason atomically, so concurrent racers cannot
 * interleave a losing reason with a winning flag). */
typedef enum {
    LORIE_GATEA_FAIL_NONE = 0,
    LORIE_GATEA_FAIL_IMPORT = 1,
    LORIE_GATEA_FAIL_DRAW = 2,
    LORIE_GATEA_FAIL_FENCE = 3,
    LORIE_GATEA_FAIL_TIMEOUT = 4,
    LORIE_GATEA_FAIL_PROTOCOL = 5,
    LORIE_GATEA_FAIL_GENERATION = 6,
    LORIE_GATEA_FAIL_UNREGISTER = 7,
    LORIE_GATEA_FAIL_CLOSE = 8,
} LorieGateAFailCode;

/* Pure derivation from acquire-loaded (completed, firstFailed, fatal).
 * Caller MUST load fatal first each waiter iteration (observation-order rule):
 * a SUCCESS fully derived before fatal is observed stays credible. */
static inline __always_inline LorieGateAResult lorieGateADeriveResult(uint64_t completed, uint64_t firstFailed, uint32_t fatal, uint64_t serial) {
    if (fatal != 0)
        return LORIE_GATEA_RESULT_FATAL;
    if (firstFailed != 0 && serial > firstFailed)
        return LORIE_GATEA_RESULT_FATAL; /* never executed; poisoned */
    if (firstFailed != 0 && serial == firstFailed)
        return completed >= serial ? LORIE_GATEA_RESULT_FAILED_QUIESCED : LORIE_GATEA_RESULT_NONE;
    return completed >= serial ? LORIE_GATEA_RESULT_SUCCESS : LORIE_GATEA_RESULT_NONE;
}

/* Frozen 40-byte shared result sideband, appended to lorie_shared_server_state.
 * Plain fields on purpose: every cross-process access MUST use the accessors
 * below (release/acquire). Direct or volatile-only access is forbidden. */
struct LorieGateAProtocol {
    uint32_t protocolVersion;    /* +0  */
    uint32_t generationFatal;    /* +4  sticky: 0 clean, else LorieGateAFailCode */
    uint64_t sessionNonce;       /* +8  */
    uint64_t generation;         /* +16 */
    uint64_t firstFailedSerial;  /* +24 sticky, 0 = none */
    uint32_t firstFailureCode;   /* +32 valid iff firstFailedSerial != 0 */
    uint32_t fatalReason;        /* +36 diagnostic mirror of generationFatal */
};

LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateAProtocol) == 40, "gatea sideband size");
/* Struct alignment is 8 on LP64 but 4 on LP32 (x86/armeabi-v7a); member offsets
 * stay identical on both because every 64-bit member sits at a multiple of 8.
 * The protocol needs 8-aligned 64-bit members (lock-free atomics), not struct
 * align 8. CI 34705593764 proved == 8 wrong on i686. */
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateAProtocol, sessionNonce) % 8) == 0, "gatea nonce aligned");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateAProtocol, generation) % 8) == 0, "gatea generation aligned");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateAProtocol, firstFailedSerial) % 8) == 0, "gatea firstFailed aligned");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAProtocol, protocolVersion) == 0, "gatea version off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAProtocol, generationFatal) == 4, "gatea fatal off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAProtocol, sessionNonce) == 8, "gatea nonce off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAProtocol, generation) == 16, "gatea generation off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAProtocol, firstFailedSerial) == 24, "gatea firstFailed off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAProtocol, firstFailureCode) == 32, "gatea failCode off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAProtocol, fatalReason) == 36, "gatea fatalReason off");
LORIE_GATEA_STATIC_ASSERT(sizeof(LorieGpuCopyEntry) == 168, "queue entry ABI unchanged");
LORIE_GATEA_STATIC_ASSERT(__atomic_always_lock_free(4, (const volatile void *)0)
    && __atomic_always_lock_free(8, (const volatile void *)0), "gatea atomics lock-free");

/* Called once by X before the mapping is shared; plain stores are safe here.
 * nonce and generation MUST be nonzero (P1: getrandom nonce, 1-based generation). */
static inline __always_inline void lorieGateAProtocolInit(struct LorieGateAProtocol *p, uint64_t nonce, uint64_t generation) {
    p->protocolVersion = 1u;
    p->generationFatal = 0u;
    p->sessionNonce = nonce;
    p->generation = generation;
    p->firstFailedSerial = 0u;
    p->firstFailureCode = 0u;
    p->fatalReason = 0u;
}

/* Runtime gate before ACTIVE: every shared 32/64-bit field must be lock-free
 * for its actual address in both processes. False disables Gate A pre-REGISTER. */
static inline __always_inline bool lorieGateAAtomicsLockFree(const struct LorieGateAProtocol *p) {
    return __atomic_is_lock_free(sizeof(p->protocolVersion), &p->protocolVersion)
        && __atomic_is_lock_free(sizeof(p->generationFatal), &p->generationFatal)
        && __atomic_is_lock_free(sizeof(p->sessionNonce), &p->sessionNonce)
        && __atomic_is_lock_free(sizeof(p->generation), &p->generation)
        && __atomic_is_lock_free(sizeof(p->firstFailedSerial), &p->firstFailedSerial)
        && __atomic_is_lock_free(sizeof(p->firstFailureCode), &p->firstFailureCode)
        && __atomic_is_lock_free(sizeof(p->fatalReason), &p->fatalReason);
}

/* ---- Centralized release/acquire accessors (P0 provides, P5 rewires) ---- */

static inline __always_inline uint32_t lorieGateALoadU32Acquire(const uint32_t *p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}

static inline __always_inline void lorieGateAStoreU32Release(uint32_t *p, uint32_t v) {
    __atomic_store_n(p, v, __ATOMIC_RELEASE);
}

static inline __always_inline uint64_t lorieGateALoadU64Acquire(const uint64_t *p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}

static inline __always_inline void lorieGateAStoreU64Release(uint64_t *p, uint64_t v) {
    __atomic_store_n(p, v, __ATOMIC_RELEASE);
}

/* Queue indices: X release-stores writeIndex, renderer acquire-loads it;
 * renderer release-stores readIndex (slot copied/dequeued only), X acquire-loads
 * it for slot reuse. completedSerial: renderer release-stores after fence-proven
 * quiescence, X acquire-loads it. completedSerial is quiescence, NOT success. */
static inline __always_inline void lorieGateAPublishWriteIndex(uint32_t *p, uint32_t v) {
    __atomic_store_n(p, v, __ATOMIC_RELEASE);
}

static inline __always_inline uint32_t lorieGateAObserveWriteIndex(const uint32_t *p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}

static inline __always_inline void lorieGateAPublishReadIndex(uint32_t *p, uint32_t v) {
    __atomic_store_n(p, v, __ATOMIC_RELEASE);
}

static inline __always_inline uint32_t lorieGateAObserveReadIndex(const uint32_t *p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}

static inline __always_inline void lorieGateAPublishCompleted(uint64_t *p, uint64_t serial) {
    __atomic_store_n(p, serial, __ATOMIC_RELEASE);
}

static inline __always_inline uint64_t lorieGateAObserveCompleted(const uint64_t *p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}

/* First-failure edge. Single writer (renderer) by construction; the CAS still
 * detects protocol corruption. Code is relaxed-stored before the release CAS,
 * so the code is visible to anyone acquiring firstFailedSerial. */
static inline __always_inline int lorieGateAFirstFailedCAS(struct LorieGateAProtocol *p, uint64_t serial, uint32_t code) {
    __atomic_store_n(&p->firstFailureCode, code, __ATOMIC_RELAXED);
    {
        uint64_t expected = 0;
        return __atomic_compare_exchange_n(&p->firstFailedSerial, &expected, serial,
                                           false, __ATOMIC_RELEASE, __ATOMIC_ACQUIRE);
    }
}

/* Fatal edge: THE single atomic authority. reason MUST be a nonzero
 * LorieGateAFailCode. First detail wins; concurrent losers observe the sticky
 * word and stop. fatalReason is a diagnostic mirror only — decisions MUST read
 * generationFatal itself (the mirror store is sequenced after the winning CAS
 * and carries no synchronization). */
static inline __always_inline int lorieGateAPublishFatal(struct LorieGateAProtocol *p, uint32_t reason) {
    uint32_t expected = 0u;
    if (!__atomic_compare_exchange_n(&p->generationFatal, &expected, reason,
                                     false, __ATOMIC_RELEASE, __ATOMIC_ACQUIRE))
        return 0;
    p->fatalReason = reason;
    return 1;
}

static inline __always_inline uint32_t lorieGateAObserveFatal(const struct LorieGateAProtocol *p) {
    return __atomic_load_n(&p->generationFatal, __ATOMIC_ACQUIRE);
}

/* ---- Waiter foundation (dedicated condvars; fatal always wakes) ----
 *
 * Instances live process-locally (P1+). Signaled ONLY by the input/protocol
 * thread that owns the registry update — never via an X-thread WorkProc the
 * waiter would need, and never while holding state->lock. Fatal wake uses the
 * same signal path with FAILED state. */

typedef enum {
    LORIE_GATEA_WAIT_IDLE = 0,
    LORIE_GATEA_WAIT_ARMED = 1,
    LORIE_GATEA_WAIT_DONE = 2,
    LORIE_GATEA_WAIT_FAILED = 3,
} LorieGateAWaiterState;

struct LorieGateAWaiter {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    uint32_t state;
    uint32_t reserved;
};

static inline __always_inline void lorieGateAWaiterInit(struct LorieGateAWaiter *w) {
    pthread_mutex_init(&w->lock, NULL);
    pthread_cond_init(&w->cond, NULL);
    w->state = LORIE_GATEA_WAIT_IDLE;
    w->reserved = 0u;
}

static inline __always_inline void lorieGateAWaiterDestroy(struct LorieGateAWaiter *w) {
    pthread_cond_destroy(&w->cond);
    pthread_mutex_destroy(&w->lock);
}

static inline __always_inline void lorieGateAWaiterArm(struct LorieGateAWaiter *w) {
    pthread_mutex_lock(&w->lock);
    w->state = LORIE_GATEA_WAIT_ARMED;
    pthread_mutex_unlock(&w->lock);
}

/* Terminal signal (DONE or FAILED). Late signals after leave-ARMED are dropped
 * deterministically; re-arm is explicit. Broadcast wakes every waiter sharing
 * this instance. */
static inline __always_inline void lorieGateAWaiterSignal(struct LorieGateAWaiter *w, uint32_t terminal) {
    pthread_mutex_lock(&w->lock);
    if (w->state == LORIE_GATEA_WAIT_ARMED)
        w->state = terminal;
    pthread_cond_broadcast(&w->cond);
    pthread_mutex_unlock(&w->lock);
}

/* One bounded wait quantum against an absolute deadline. Returns current state
 * (callers loop: check shared fatal first, wait, recheck). ETIMEDOUT ends the
 * quantum; the deadline keeps every waiter bounded. */
static inline __always_inline uint32_t lorieGateAWaiterWaitUntil(struct LorieGateAWaiter *w, const struct timespec *deadline) {
    uint32_t s;
    pthread_mutex_lock(&w->lock);
    while (w->state == LORIE_GATEA_WAIT_ARMED) {
        if (pthread_cond_timedwait(&w->cond, &w->lock, deadline) == ETIMEDOUT)
            break;
    }
    s = w->state;
    pthread_mutex_unlock(&w->lock);
    return s;
}

/* ---- Gate A control wire ABI (frozen, 40-byte header, fixed bodies) ----
 *
 * Separate framing from legacy lorieEvent traffic; Gate A frames are selected
 * by header magic. No raw padded-struct writes: every multi-byte field is
 * fixed-width and naturally aligned. AHB handles travel via the existing
 * SCM_RIGHTS ancillary channel, never inside frames. */

#define LORIE_GATEA_MAGIC 0x45544147u /* "GATE" little-endian */
#define LORIE_GATEA_PROTOCOL_VERSION 1u

typedef enum {
    LORIE_GATEA_MSG_REGISTER = 1,
    LORIE_GATEA_MSG_READY = 2,
    LORIE_GATEA_MSG_REGISTER_FAILED = 3,
    LORIE_GATEA_MSG_UNREGISTER = 4,
    LORIE_GATEA_MSG_UNREGISTER_ACK = 5,
    LORIE_GATEA_MSG_GENERATION_CLOSE = 6,
    LORIE_GATEA_MSG_GENERATION_CLOSED = 7,
    LORIE_GATEA_MSG_FATAL_NOTIFY = 8,
} LorieGateAMsgType;

struct LorieGateAFrame {
    uint32_t magic;        /* +0  LORIE_GATEA_MAGIC */
    uint16_t version;      /* +4  LORIE_GATEA_PROTOCOL_VERSION */
    uint16_t type;         /* +6  LorieGateAMsgType */
    uint32_t length;       /* +8  body bytes following this header */
    uint32_t reserved;     /* +12 = 0 */
    uint64_t nonce;        /* +16 sessionNonce */
    uint64_t generation;   /* +24 */
    uint64_t bufferId;     /* +32 0 when not applicable */
};

LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateAFrame) == 40, "gatea frame size");
/* Same LP32 note as the sideband: frame member offsets are identical on LP32
 * and LP64; 64-bit members are 8-aligned on both. */
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateAFrame, nonce) % 8) == 0, "gatea frame nonce aligned");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateAFrame, generation) % 8) == 0, "gatea frame generation aligned");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateAFrame, bufferId) % 8) == 0, "gatea frame bufferId aligned");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAFrame, magic) == 0, "gatea frame magic off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAFrame, version) == 4, "gatea frame version off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAFrame, type) == 6, "gatea frame type off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAFrame, length) == 8, "gatea frame length off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAFrame, reserved) == 12, "gatea frame reserved off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAFrame, nonce) == 16, "gatea frame nonce off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAFrame, generation) == 24, "gatea frame generation off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateAFrame, bufferId) == 32, "gatea frame bufferId off");

struct LorieGateARegisterBody { uint32_t width, height, stride, format; };     /* 16 */
struct LorieGateAReadyBody { uint64_t fingerprint; };                          /* 8 */
struct LorieGateARegisterFailedBody { uint32_t code, reserved; };              /* 8 */
struct LorieGateAUnregisterBody { uint64_t lastSubmittedSerial; };             /* 8 */
struct LorieGateAGenerationCloseBody { uint64_t lastPublishedSerial; };        /* 8 */
struct LorieGateAFatalNotifyBody { uint32_t reason, reserved; };               /* 8 */
/* UNREGISTER_ACK and GENERATION_CLOSED carry empty bodies (length 0). */

LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateARegisterBody) == 16, "gatea register body");
LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateAReadyBody) == 8, "gatea ready body");
LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateARegisterFailedBody) == 8, "gatea regfail body");
LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateAUnregisterBody) == 8, "gatea unregister body");
LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateAGenerationCloseBody) == 8, "gatea genclose body");
LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateAFatalNotifyBody) == 8, "gatea fatalnotify body");

/* Exact-length transfer. Returns bytes moved, or -1 with errno preserved
 * (EINTR retried internally). A short result means EOF/HUP or error: Gate A
 * callers treat any short frame while a generation is ACTIVE as FATAL. */
static inline __always_inline ssize_t lorieGateAWriteFull(int fd, const void *buf, size_t len) {
    size_t done = 0;
    while (done < len) {
        ssize_t n = write(fd, (const char *)buf + done, len - done);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (n == 0) {
            errno = EIO;
            return -1;
        }
        done += (size_t)n;
    }
    return (ssize_t)done;
}

static inline __always_inline ssize_t lorieGateAReadFull(int fd, void *buf, size_t len) {
    size_t done = 0;
    while (done < len) {
        ssize_t n = read(fd, (char *)buf + done, len - done);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (n == 0)
            break; /* EOF/HUP: caller sees short count */
        done += (size_t)n;
    }
    return (ssize_t)done;
}

/* ---- Buffer retirement metadata (process-local registries, never shared) ---- */

typedef enum {
    LORIE_GATEA_REG_UNREGISTERED = 0,
    LORIE_GATEA_REG_REGISTERING = 1,
    LORIE_GATEA_REG_READY = 2,
    LORIE_GATEA_REG_RETIRING = 3,
    LORIE_GATEA_REG_DEAD = 4,
} LorieGateARegState;

/* X-side per registered buffer. lastSubmittedSerial is updated on every source
 * AND destination use; admission requires it terminal (R3 §2). */
struct LorieGateABufferMeta {
    uint64_t nonce, generation, bufferId, fingerprint, lastSubmittedSerial;
    uint32_t state, pendingCount, cpuLocked, unregisterAcked;
    void *ownerRef; /* X wrapper reference; process-local only, never shared */
};

/* Renderer-side per import identity. AHB/EGL/texture handles are added by the
 * P2 registry; the identity + ready/tombstone discipline is frozen here. */
struct LorieGateAImportEntry {
    uint64_t id, nonce, generation, fingerprint;
    uint32_t ready, tombstone;
};

/* Descriptor fingerprint for REGISTER/READY matching and duplicate detection:
 * same ID with different fingerprint/generation is FATAL. */
static inline __always_inline uint64_t lorieGateAFingerprint(uint32_t w, uint32_t h, uint32_t stride, uint32_t format) {
    uint64_t f = 1469598103934665603ull;
    f ^= w; f *= 1099511628211ull;
    f ^= h; f *= 1099511628211ull;
    f ^= stride; f *= 1099511628211ull;
    f ^= format; f *= 1099511628211ull;
    return f;
}

/* ---- Gate A P1 cross-TU entry points ----
 * Defined in cmdentrypoint.cpp / renderer.cpp / activity.cpp / InitOutput.c.
 * All dormant unless TERMUX_X11_GATEA_PROTO=1 and a generation is bound+active.
 * P3 admission will be the first caller of the send/insert paths. */

/* Fail-closed process halt for Gate A fatal paths. Logs, then exits without
 * running cleanup that assumes GPU quiescence (EGL/driver cleanup follows
 * process teardown). Exit 127 separates protocol fatal from generic _exit(1). */
__attribute__((noreturn)) static inline __always_inline void lorieGateAFatalHalt(const char *what, uint32_t reason) {
    __android_log_print(ANDROID_LOG_FATAL, "gatea-a1", "GATEA_FATAL_HALT what=%s reason=%u", what, (unsigned)reason);
    _exit(127);
}

/* X-side framed REGISTER send (cmdentrypoint.cpp). No callers in P1 (P3
 * admission calls it). Returns 0 on full frame+handle delivery, -1 otherwise. */
int lorieGateASendRegister(uint64_t id, uint64_t nonce, uint64_t generation,
                           uint32_t w, uint32_t h, uint32_t stride, uint32_t format,
                           AHardwareBuffer *ahb);

/* X-side registry (cmdentrypoint.cpp). Pool-stable slots: waiter addresses
 * stay valid for process lifetime, so input-thread signaling needs no
 * refcounting. Insert arms nothing (P3 arms before send); mark updates state
 * and signals the entry waiter. */
int lorieGateARegistryInsert(uint64_t nonce, uint64_t generation, uint64_t id, uint64_t fingerprint);
int lorieGateARegistryFind(uint64_t id, struct LorieGateABufferMeta *out);
int lorieGateARegistryMarkChecked(uint64_t id, uint64_t nonce, uint64_t generation,
                                  uint64_t fingerprint, int ready, uint32_t code);
struct LorieGateAWaiter *lorieGateARegistryWaiter(uint64_t id);
/* Tombstone every entry of an old generation (wake waiters FAILED). Called on
 * generation rotation; entries never resurrect (insert-replace rules apply). */
void lorieGateARegistryCloseGeneration(uint64_t oldNonce, uint64_t oldGeneration);

/* X-side shared-state telescope (InitOutput.c). */
struct LorieGateAProtocol *lorieGateAShared(void);
int lorieGateAActive(void);

/* Renderer-side import enqueue (renderer.cpp). Called once per REGISTER from
 * activity.cpp xcallback; transfers the received AHB reference. Returns 0 if
 * queued for GL-thread validation. */
int lorieGateAEnqueueImport(uint64_t id, uint64_t nonce, uint64_t generation,
                            uint64_t fingerprint, AHardwareBuffer *ahb, uint32_t failCode);

/* Renderer import occupancy for the activity re-share path (renderer.cpp).
 * Nonzero iff any pending node, ready entry, or sticky overflow exists. */
int lorieGateAImportBusy(void);

/* Release a received AHB reference on any thread (refcounted, no GL needed).
 * Delegates to the availability-guarded buffer.c wrapper: direct NDK calls
 * are forbidden outside buffer.c (minSdk 24 vs API-26 symbols). */
static inline __always_inline void lorieGateAReleaseAhb(AHardwareBuffer *ahb) {
    if (ahb != NULL)
        LorieBuffer_releaseAHardwareBuffer(ahb);
}

/* Renderer-side bound tuple (activity.cpp). Returns nonzero iff bound. */
int lorieGateABoundTuple(uint64_t *nonce, uint64_t *generation);

struct lorie_shared_server_state {
    /*
     * Renderer and X server are separated into 2 different processes.
     * Root window and cursor content and properties are shared across these 2 processes.
     * Reading/drawing root window in renderer the same time X server writes it can cause
     * tearing, texture garbling and other visual artifacts so we should block X server while we are drawing.
     */
    pthread_mutex_t lock; // initialized at X server side.
    pid_t lockingPid;

    /*
     * Single-producer (X server, present_execute_copy)/single-consumer (renderer) ring buffer
     * of deferred GPU copies to be applied to the root window texture before it is drawn to screen.
     * X server only ever advances writeIndex, renderer only ever advances readIndex and completedSerial.
     */
    struct {
        volatile uint32_t writeIndex;
        volatile uint32_t readIndex;
        volatile uint64_t completedSerial;
        LorieGpuCopyEntry entries[LORIE_GPU_COPY_QUEUE_CAPACITY];
    } gpuCopyQueue;

    /* ID of root window texture to be drawn. */
    uint64_t rootWindowTextureID;

    /* A signal to renderer to update root window texture content from shared fragment if needed */
    volatile uint8_t drawRequested;

    /* We should avoid triggering renderer if there is no output surface */
    volatile uint8_t surfaceAvailable;

    /*
     * We do not want to block the X server for an extended period; ideally, we would avoid blocking it at all.
     * However, if we don’t block the X server, it will overwrite root window memory fragment, causing tearing or frame distortion.
     * On some devices, there is no way to make EGL/GLES2 render a frame without calling eglSwapBuffers;
     * calls like glFinish, eglWaitGL, and eglWaitClient have no effect.
     * The only way to force EGL to render a frame and flush the command queue is by invoking eglSwapBuffers.
     * But eglSwapBuffers will not return until Android actually displays the frame.
     * Since we want to proceed as quickly as possible, waiting for the frame to be shown is not acceptable.
     *
     * Therefore, we set eglSwapInterval(dpy, 1), so that eglSwapBuffers does not block until the frame is displayed.
     * Even then, we do not want to waste GPU resources rendering more than one full-screen quad per vsync,
     * because that would spend GPU time on a frame that will never be shown.
     * To handle this, we use a waitForNextFrame flag, which we set after a successful render and clear from the AChoreographer’s frame callback.
     */
    volatile uint8_t waitForNextFrame;

    /* Needed to show FPS counter in logcat */
    volatile int renderedFrames;

    struct {
        // We should not allow updating cursor content the same time renderer draws it.
        // locking the mutex protecting the root window can cause waiting for the frame to be drawn which is unacceptable
        pthread_mutex_t lock; // initialized at X server side.
        pid_t lockingPid;
        uint32_t x, y, xhot, yhot, width, height;
        uint32_t bits[512*512]; // 1 megabyte should be enough for any cursor up to 512x512
        // Signals to renderer to update cursor's texture or its coordinates
        volatile uint8_t updated, moved;
    } cursor;

    volatile uint64_t rendererSolidSubmits;
    volatile uint64_t rendererSolidComplete;

    /* Optional P2-B.3a records. Zero overhead apart from a disabled branch when off. */
    LorieB3aTelemetry b3aTelemetry;

    /* Gate A P0 result sideband. Appended last: existing offsets unchanged.
     * Zeroed with the mapping at creation; X initializes identity via
     * lorieGateAProtocolInit before sharing. All cross-process access uses
     * the centralized accessors above. */
    struct LorieGateAProtocol gateA;
};

#ifdef __cplusplus
}
#endif

#ifdef __cplusplus
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include "list.h"

struct Renderer {
    EGLDisplay egl_display = EGL_NO_DISPLAY;
    EGLContext ctx = EGL_NO_CONTEXT;
    EGLSurface defaultSfc = EGL_NO_SURFACE, sfc = EGL_NO_SURFACE;
    EGLConfig cfg = 0;
    ANativeWindow *defaultWin = nullptr, *win = nullptr;
    struct xorg_list addedBuffers, buffers, removedBuffers;
    volatile jint filtering = GL_NEAREST;

    volatile bool stateChanged = false, windowChanged = false, viewportChanged = false;
    struct lorie_shared_server_state* pendingState = nullptr;
    ANativeWindow* pendingWin = nullptr;
    volatile int viewportX = 0, viewportY = 0, viewportW = 0, viewportH = 0, expectedW = 0, expectedH = 0;
    volatile int hiddenBottom = 0;
    volatile int zoomPercent = 100;
    float panSourceLeft = 0.f, panSourceTop = 0.f;
    float hiddenPanSourceTop = -1.f; // the vertical pan of the other keyboard state, negative until there was one
    bool bottomWasHidden = false;
    JNIEnv* rendererEnv = nullptr;
    JavaVM* jvm = nullptr; // Stashed by init() so initThread() can be reached via `this` from a plain (non-capturing) pthread_create callback.
    jclass lorieViewClass = nullptr;
    jmethodID setRendererViewportMethod = nullptr;
    int reportedViewportX = -1, reportedViewportY = -1, reportedViewportW = -1, reportedViewportH = -1;
    float reportedSourceLeft = -1.f, reportedSourceTop = -1.f, reportedSourceWidth = -1.f, reportedSourceHeight = -1.f;

    pthread_mutex_t stateLock;
    // Shared with the X server so it can signal us directly. Only this thread ever waits on it, so stateLock
    // (the companion mutex) doesn't need to be shared too.
    pthread_cond_t* stateCond = nullptr;
    pthread_cond_t stateChangeFinishCond;
    pthread_spinlock_t bufferLock;
    int stateCondFd = -1;
    struct lorie_shared_server_state* state = nullptr;
    struct {
        GLuint id;
        bool cursorChanged;
    } cursor{};

    // FBO used to blit deferred Present "copy" entries (see lorieTryScheduleGpuCopy) into the root texture.
    GLuint gpuCopyFbo = 0;

    GLuint g_texture_program = 0, gv_pos = 0, gv_coords = 0;
    GLuint g_texture_program_bgra = 0, gv_pos_bgra = 0, gv_coords_bgra = 0;
    GLuint g_solid_program = 0, gv_solid_pos = 0, g_solid_color = 0;

    EGLint configAttribs[13] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_ALPHA_SIZE, 0,
        EGL_NONE
    };

    // Formerly function-local statics; moved here for the same reason as everything else above -
    // they are per-instance state, not per-process.
    uint64_t dstSizeLogCount = 0, srcSizeLogCount = 0;
    uint64_t lastRequestedBufferId = 0;

    void init(JNIEnv* env);
    void* initThread();
    int getWakeupCondFd() const;
    void setFiltering(jint f);
    void testCapabilities(int* legacy_drawing, int* gpu_present_disabled);
    void setSharedState(struct lorie_shared_server_state* newState);
    void addBuffer(LorieBuffer* buf);
    void removeBuffer(uint64_t id);
    void removeAllBuffers();
    void setWindow(JNIEnv* env, jobject jsfc);
    void setViewport(int x, int y, int w, int h, int ew, int eh, int hidden);
    void setZoom(int percent);
    void releaseWinAndSurface(ANativeWindow** anw, EGLSurface* esfc);
    void refreshContext();
    LorieBuffer* findBufferWithRetry(uint64_t id);
    uint64_t applyPendingGpuCopiesLocked();
    void applyPendingGpuCopies();
    void redrawLocked(bool* waitingForBuffers);
    bool shouldWait(bool* waitingForBuffers);
    void threadLoop();
    void bindTexture(GLuint id) const;
    void reportViewport(int dstX, int dstY, int dstW, int dstH, float left, float top, float width, float height);
    void drawRegion(GLuint id, float x0, float y0, float x1, float y1, float u0, float v0, float u1, float v1, uint8_t flip, uint8_t forceNearest = 0);
    void drawSolid(float x0, float y0, float x1, float y1, float r, float g, float b, float a);
    void drawCursor(float displayWidth, float displayHeight, float sourceLeft, float sourceTop);
};
#endif

#ifdef __cplusplus
extern "C" {
#endif

static int android_to_linux_keycode[304] = {
        [ 4   /* ANDROID_KEYCODE_BACK */] = KEY_ESC,
        [ 7   /* ANDROID_KEYCODE_0 */] = KEY_0,
        [ 8   /* ANDROID_KEYCODE_1 */] = KEY_1,
        [ 9   /* ANDROID_KEYCODE_2 */] = KEY_2,
        [ 10  /* ANDROID_KEYCODE_3 */] = KEY_3,
        [ 11  /* ANDROID_KEYCODE_4 */] = KEY_4,
        [ 12  /* ANDROID_KEYCODE_5 */] = KEY_5,
        [ 13  /* ANDROID_KEYCODE_6 */] = KEY_6,
        [ 14  /* ANDROID_KEYCODE_7 */] = KEY_7,
        [ 15  /* ANDROID_KEYCODE_8 */] = KEY_8,
        [ 16  /* ANDROID_KEYCODE_9 */] = KEY_9,
        [ 17  /* ANDROID_KEYCODE_STAR */] = KEY_KPASTERISK,
        [ 19  /* ANDROID_KEYCODE_DPAD_UP */] = KEY_UP,
        [ 20  /* ANDROID_KEYCODE_DPAD_DOWN */] = KEY_DOWN,
        [ 21  /* ANDROID_KEYCODE_DPAD_LEFT */] = KEY_LEFT,
        [ 22  /* ANDROID_KEYCODE_DPAD_RIGHT */] = KEY_RIGHT,
        [ 23  /* ANDROID_KEYCODE_DPAD_CENTER */] = KEY_ENTER,
        [ 24  /* ANDROID_KEYCODE_VOLUME_UP */] = KEY_VOLUMEUP, // XF86XK_AudioRaiseVolume
        [ 25  /* ANDROID_KEYCODE_VOLUME_DOWN */] = KEY_VOLUMEDOWN, // XF86XK_AudioLowerVolume
        [ 26  /* ANDROID_KEYCODE_POWER */] = KEY_POWER,
        [ 27  /* ANDROID_KEYCODE_CAMERA */] = KEY_CAMERA,
        [ 28  /* ANDROID_KEYCODE_CLEAR */] = KEY_CLEAR,
        [ 29  /* ANDROID_KEYCODE_A */] = KEY_A,
        [ 30  /* ANDROID_KEYCODE_B */] = KEY_B,
        [ 31  /* ANDROID_KEYCODE_C */] = KEY_C,
        [ 32  /* ANDROID_KEYCODE_D */] = KEY_D,
        [ 33  /* ANDROID_KEYCODE_E */] = KEY_E,
        [ 34  /* ANDROID_KEYCODE_F */] = KEY_F,
        [ 35  /* ANDROID_KEYCODE_G */] = KEY_G,
        [ 36  /* ANDROID_KEYCODE_H */] = KEY_H,
        [ 37  /* ANDROID_KEYCODE_I */] = KEY_I,
        [ 38  /* ANDROID_KEYCODE_J */] = KEY_J,
        [ 39  /* ANDROID_KEYCODE_K */] = KEY_K,
        [ 40  /* ANDROID_KEYCODE_L */] = KEY_L,
        [ 41  /* ANDROID_KEYCODE_M */] = KEY_M,
        [ 42  /* ANDROID_KEYCODE_N */] = KEY_N,
        [ 43  /* ANDROID_KEYCODE_O */] = KEY_O,
        [ 44  /* ANDROID_KEYCODE_P */] = KEY_P,
        [ 45  /* ANDROID_KEYCODE_Q */] = KEY_Q,
        [ 46  /* ANDROID_KEYCODE_R */] = KEY_R,
        [ 47  /* ANDROID_KEYCODE_S */] = KEY_S,
        [ 48  /* ANDROID_KEYCODE_T */] = KEY_T,
        [ 49  /* ANDROID_KEYCODE_U */] = KEY_U,
        [ 50  /* ANDROID_KEYCODE_V */] = KEY_V,
        [ 51  /* ANDROID_KEYCODE_W */] = KEY_W,
        [ 52  /* ANDROID_KEYCODE_X */] = KEY_X,
        [ 53  /* ANDROID_KEYCODE_Y */] = KEY_Y,
        [ 54  /* ANDROID_KEYCODE_Z */] = KEY_Z,
        [ 55  /* ANDROID_KEYCODE_COMMA */] = KEY_COMMA,
        [ 56  /* ANDROID_KEYCODE_PERIOD */] = KEY_DOT,
        [ 57  /* ANDROID_KEYCODE_ALT_LEFT */] = KEY_LEFTALT,
        [ 58  /* ANDROID_KEYCODE_ALT_RIGHT */] = KEY_RIGHTALT,
        [ 59  /* ANDROID_KEYCODE_SHIFT_LEFT */] = KEY_LEFTSHIFT,
        [ 60  /* ANDROID_KEYCODE_SHIFT_RIGHT */] = KEY_RIGHTSHIFT,
        [ 61  /* ANDROID_KEYCODE_TAB */] = KEY_TAB,
        [ 62  /* ANDROID_KEYCODE_SPACE */] = KEY_SPACE,
        [ 64  /* ANDROID_KEYCODE_EXPLORER */] = KEY_WWW,
        [ 65  /* ANDROID_KEYCODE_ENVELOPE */] = KEY_MAIL,
        [ 66  /* ANDROID_KEYCODE_ENTER */] = KEY_ENTER,
        [ 67  /* ANDROID_KEYCODE_DEL */] = KEY_BACKSPACE,
        [ 68  /* ANDROID_KEYCODE_GRAVE */] = KEY_GRAVE,
        [ 69  /* ANDROID_KEYCODE_MINUS */] = KEY_MINUS,
        [ 70  /* ANDROID_KEYCODE_EQUALS */] = KEY_EQUAL,
        [ 71  /* ANDROID_KEYCODE_LEFT_BRACKET */] = KEY_LEFTBRACE,
        [ 72  /* ANDROID_KEYCODE_RIGHT_BRACKET */] = KEY_RIGHTBRACE,
        [ 73  /* ANDROID_KEYCODE_BACKSLASH */] = KEY_BACKSLASH,
        [ 74  /* ANDROID_KEYCODE_SEMICOLON */] = KEY_SEMICOLON,
        [ 75  /* ANDROID_KEYCODE_APOSTROPHE */] = KEY_APOSTROPHE,
        [ 76  /* ANDROID_KEYCODE_SLASH */] = KEY_SLASH,
        [ 81  /* ANDROID_KEYCODE_PLUS */] = KEY_KPPLUS,
        [ 82  /* ANDROID_KEYCODE_MENU */] = KEY_CONTEXT_MENU,
        [ 84  /* ANDROID_KEYCODE_SEARCH */] = KEY_SEARCH,
        [ 85  /* ANDROID_KEYCODE_MEDIA_PLAY_PAUSE */] = KEY_PLAYPAUSE,
        [ 86  /* ANDROID_KEYCODE_MEDIA_STOP */] = KEY_STOP_RECORD,
        [ 87  /* ANDROID_KEYCODE_MEDIA_NEXT */] = KEY_NEXTSONG,
        [ 88  /* ANDROID_KEYCODE_MEDIA_PREVIOUS */] = KEY_PREVIOUSSONG,
        [ 89  /* ANDROID_KEYCODE_MEDIA_REWIND */] = KEY_REWIND,
        [ 90  /* ANDROID_KEYCODE_MEDIA_FAST_FORWARD */] = KEY_FASTFORWARD,
        [ 91  /* ANDROID_KEYCODE_MUTE */] = KEY_MUTE,
        [ 92  /* ANDROID_KEYCODE_PAGE_UP */] = KEY_PAGEUP,
        [ 93  /* ANDROID_KEYCODE_PAGE_DOWN */] = KEY_PAGEDOWN,
        [ 111  /* ANDROID_KEYCODE_ESCAPE */] = KEY_ESC,
        [ 112  /* ANDROID_KEYCODE_FORWARD_DEL */] = KEY_DELETE,
        [ 113  /* ANDROID_KEYCODE_CTRL_LEFT */] = KEY_LEFTCTRL,
        [ 114  /* ANDROID_KEYCODE_CTRL_RIGHT */] = KEY_RIGHTCTRL,
        [ 115  /* ANDROID_KEYCODE_CAPS_LOCK */] = KEY_CAPSLOCK,
        [ 116  /* ANDROID_KEYCODE_SCROLL_LOCK */] = KEY_SCROLLLOCK,
        [ 117  /* ANDROID_KEYCODE_META_LEFT */] = KEY_LEFTMETA,
        [ 118  /* ANDROID_KEYCODE_META_RIGHT */] = KEY_RIGHTMETA,
        [ 120  /* ANDROID_KEYCODE_SYSRQ */] = KEY_PRINT,
        [ 121  /* ANDROID_KEYCODE_BREAK */] = KEY_BREAK,
        [ 122  /* ANDROID_KEYCODE_MOVE_HOME */] = KEY_HOME,
        [ 123  /* ANDROID_KEYCODE_MOVE_END */] = KEY_END,
        [ 124  /* ANDROID_KEYCODE_INSERT */] = KEY_INSERT,
        [ 125  /* ANDROID_KEYCODE_FORWARD */] = KEY_FORWARD,
        [ 126  /* ANDROID_KEYCODE_MEDIA_PLAY */] = KEY_PLAYCD,
        [ 127  /* ANDROID_KEYCODE_MEDIA_PAUSE */] = KEY_PAUSECD,
        [ 128  /* ANDROID_KEYCODE_MEDIA_CLOSE */] = KEY_CLOSECD,
        [ 129  /* ANDROID_KEYCODE_MEDIA_EJECT */] = KEY_EJECTCD,
        [ 130  /* ANDROID_KEYCODE_MEDIA_RECORD */] = KEY_RECORD,
        [ 131  /* ANDROID_KEYCODE_F1 */] = KEY_F1,
        [ 132  /* ANDROID_KEYCODE_F2 */] = KEY_F2,
        [ 133  /* ANDROID_KEYCODE_F3 */] = KEY_F3,
        [ 134  /* ANDROID_KEYCODE_F4 */] = KEY_F4,
        [ 135  /* ANDROID_KEYCODE_F5 */] = KEY_F5,
        [ 136  /* ANDROID_KEYCODE_F6 */] = KEY_F6,
        [ 137  /* ANDROID_KEYCODE_F7 */] = KEY_F7,
        [ 138  /* ANDROID_KEYCODE_F8 */] = KEY_F8,
        [ 139  /* ANDROID_KEYCODE_F9 */] = KEY_F9,
        [ 140  /* ANDROID_KEYCODE_F10 */] = KEY_F10,
        [ 141  /* ANDROID_KEYCODE_F11 */] = KEY_F11,
        [ 142  /* ANDROID_KEYCODE_F12 */] = KEY_F12,
        [ 143  /* ANDROID_KEYCODE_NUM_LOCK */] = KEY_NUMLOCK,
        [ 144  /* ANDROID_KEYCODE_NUMPAD_0 */] = KEY_KP0,
        [ 145  /* ANDROID_KEYCODE_NUMPAD_1 */] = KEY_KP1,
        [ 146  /* ANDROID_KEYCODE_NUMPAD_2 */] = KEY_KP2,
        [ 147  /* ANDROID_KEYCODE_NUMPAD_3 */] = KEY_KP3,
        [ 148  /* ANDROID_KEYCODE_NUMPAD_4 */] = KEY_KP4,
        [ 149  /* ANDROID_KEYCODE_NUMPAD_5 */] = KEY_KP5,
        [ 150  /* ANDROID_KEYCODE_NUMPAD_6 */] = KEY_KP6,
        [ 151  /* ANDROID_KEYCODE_NUMPAD_7 */] = KEY_KP7,
        [ 152  /* ANDROID_KEYCODE_NUMPAD_8 */] = KEY_KP8,
        [ 153  /* ANDROID_KEYCODE_NUMPAD_9 */] = KEY_KP9,
        [ 154  /* ANDROID_KEYCODE_NUMPAD_DIVIDE */] = KEY_KPSLASH,
        [ 155  /* ANDROID_KEYCODE_NUMPAD_MULTIPLY */] = KEY_KPASTERISK,
        [ 156  /* ANDROID_KEYCODE_NUMPAD_SUBTRACT */] = KEY_KPMINUS,
        [ 157  /* ANDROID_KEYCODE_NUMPAD_ADD */] = KEY_KPPLUS,
        [ 158  /* ANDROID_KEYCODE_NUMPAD_DOT */] = KEY_KPDOT,
        [ 159  /* ANDROID_KEYCODE_NUMPAD_COMMA */] = KEY_KPCOMMA,
        [ 160  /* ANDROID_KEYCODE_NUMPAD_ENTER */] = KEY_KPENTER,
        [ 161  /* ANDROID_KEYCODE_NUMPAD_EQUALS */] = KEY_KPEQUAL,
        [ 162  /* ANDROID_KEYCODE_NUMPAD_LEFT_PAREN */] = KEY_KPLEFTPAREN,
        [ 163  /* ANDROID_KEYCODE_NUMPAD_RIGHT_PAREN */] = KEY_KPRIGHTPAREN,
        [ 164  /* ANDROID_KEYCODE_VOLUME_MUTE */] = KEY_MUTE,
        [ 165  /* ANDROID_KEYCODE_INFO */] = KEY_INFO,
        [ 166  /* ANDROID_KEYCODE_CHANNEL_UP */] = KEY_CHANNELUP,
        [ 167  /* ANDROID_KEYCODE_CHANNEL_DOWN */] = KEY_CHANNELDOWN,
        [ 168  /* ANDROID_KEYCODE_ZOOM_IN */] = KEY_ZOOMIN,
        [ 169  /* ANDROID_KEYCODE_ZOOM_OUT */] = KEY_ZOOMOUT,
        [ 170  /* ANDROID_KEYCODE_TV */] = KEY_TV,
        [ 208  /* ANDROID_KEYCODE_CALENDAR */] = KEY_CALENDAR,
        [ 210  /* ANDROID_KEYCODE_CALCULATOR */] = KEY_CALC,
};

#ifdef __cplusplus
}
#endif
