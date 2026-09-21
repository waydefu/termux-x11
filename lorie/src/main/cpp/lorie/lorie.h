#pragma once

#ifdef LORIE_HOST_RECORD_DECODER_TEST
#include <stdbool.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/socket.h>
#include <linux/input-event-codes.h>
#include "b3a_telemetry.h"
#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif
#ifndef __unused
#define __unused __attribute__((unused))
#endif
typedef int Bool;
typedef struct AHardwareBuffer AHardwareBuffer;
typedef struct AChoreographer AChoreographer;
typedef struct LorieBuffer LorieBuffer;
enum {
    ANDROID_LOG_INFO = 4,
    ANDROID_LOG_FATAL = 7
};
static inline int __android_log_print(int prio, const char *tag, const char *fmt, ...) {
    (void)prio;
    (void)tag;
    (void)fmt;
    return 0;
}
static inline void LorieBuffer_releaseAHardwareBuffer(AHardwareBuffer *ahb) {
    (void)ahb;
}
#else
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
#endif

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

/* Prototype feature flag: exact "1" only. */
static inline __always_inline int lorieGateAProtoEnabled(void) {
    const char *e = getenv("TERMUX_X11_GATEA_PROTO");
    return e != NULL && e[0] == '1' && e[1] == '\0';
}

/* X-process request only. Exact "1". Activity/renderer never inherit the
 * launcher environment; they must read the published shared enable word. */
static inline __always_inline int lorieGateATelemetryRequested(void) {
    const char *e = getenv("TERMUX_X11_GATEA_TELEMETRY");
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

#include "lorie_gatea_done_class.h"
#include "lorie_gatea_wait_wake_class.h"

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

/* P2 direct identity is side metadata: the frozen queue entry remains byte-for-
 * byte unchanged. X initializes all identity fields then release-publishes
 * PUBLISHED before publishing writeIndex. Renderer acquire-loads state, checks
 * slot+serial+tuple+endpoints, copies the queue entry, then release-publishes
 * CONSUMED. X may reuse a slot only after observing CONSUMED (or EMPTY). */
typedef enum {
    LORIE_GATEA_DIRECT_EMPTY = 0,
    LORIE_GATEA_DIRECT_PUBLISHED = 1,
    LORIE_GATEA_DIRECT_CONSUMED = 2,
} LorieGateADirectState;

struct LorieGateADirectMeta {
    uint32_t state;
    uint32_t reserved;
    uint64_t nonce;
    uint64_t generation;
    uint64_t serial;
    uint64_t srcId;
    uint64_t dstId;
};

LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateADirectMeta) == 48, "gatea direct metadata size");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateADirectMeta, nonce) % 8) == 0, "gatea direct nonce aligned");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateADirectMeta, generation) % 8) == 0, "gatea direct generation aligned");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateADirectMeta, serial) % 8) == 0, "gatea direct serial aligned");

/* Default-off, Experimental-only diagnostic schema. Counters are authoritative
 * machine-readable values; the bounded ring preserves event order for small
 * qualification cells. Ring overwrite is never silent: overflow is sticky. */
typedef enum {
    LORIE_GATEA_EVENT_NONE = 0,
    LORIE_GATEA_EVENT_REGISTER_READY,
    LORIE_GATEA_EVENT_LEASE_RESERVED,
    LORIE_GATEA_EVENT_UNLOCK_SRC_OK,
    LORIE_GATEA_EVENT_UNLOCK_DST_OK,
    LORIE_GATEA_EVENT_LEASE_GPU_OWNED,
    LORIE_GATEA_EVENT_PUBLISH,
    LORIE_GATEA_EVENT_CONSUME_DIRECT,
    LORIE_GATEA_EVENT_DIRECT_LOOKUP_OK,
    LORIE_GATEA_EVENT_DIRECT_LOOKUP_FAIL,
    LORIE_GATEA_EVENT_DRAW_SUBMIT,
    LORIE_GATEA_EVENT_FENCE_SATISFIED,
    LORIE_GATEA_EVENT_FENCE_TIMEOUT,
    LORIE_GATEA_EVENT_FENCE_ERROR,
    LORIE_GATEA_EVENT_COMPLETED_SERIAL,
    LORIE_GATEA_EVENT_FIRST_FAILED_SERIAL,
    LORIE_GATEA_EVENT_GENERATION_FATAL,
    LORIE_GATEA_EVENT_SEMANTIC_SUCCESS,
    LORIE_GATEA_EVENT_RELOCK_SRC,
    LORIE_GATEA_EVENT_RELOCK_DST,
    LORIE_GATEA_EVENT_REPAIR,
    LORIE_GATEA_EVENT_ACK,
    LORIE_GATEA_EVENT_PENDING_DEC,
    LORIE_GATEA_EVENT_LEASE_RELEASE,
    LORIE_GATEA_EVENT_UNREGISTER_SEND,
    LORIE_GATEA_EVENT_UNREGISTER_ACK,
    LORIE_GATEA_EVENT_RESOURCE_DESTROY,
    LORIE_GATEA_EVENT_GENERATION_CLOSE,
    LORIE_GATEA_EVENT_GENERATION_CLOSED,
    /* R6 observability. Numbers 1..28 stay frozen. No new counters. */
    LORIE_GATEA_EVENT_REQUEST_ARRIVED,
    LORIE_GATEA_EVENT_CALLBACK_EXECUTED,
    LORIE_GATEA_EVENT_DIRECT_ADMIT_REJECT,
    LORIE_GATEA_EVENT_PRESENT_EARLY_ACK,
    LORIE_GATEA_EVENT_PRESENT_REQUEUE_FAILED,
    LORIE_GATEA_EVENT_PRESENT_ACK_AFTER_COMPLETED,
    /* Artifact B / R7. Numbers 1..34 stay frozen. Event 32 stays unused. */
    LORIE_GATEA_EVENT_TEST_FAULT_FIRED,
    LORIE_GATEA_EVENT_PRESENT_RETIRE,
    LORIE_GATEA_EVENT_MAX,
} LorieGateAEvent;

#define LORIE_GATEA_XOP_COPYAREA 1u
#define LORIE_GATEA_XOP_SOLID 2u
#define LORIE_GATEA_XOP_COMPOSITE 3u
#define LORIE_GATEA_XOP_PRESENT 4u
#define LORIE_GATEA_XOP_PREPARE_ACCESS 5u
#define LORIE_GATEA_REJECT_NOT_QUIESCENT 1u
#define LORIE_GATEA_REJECT_PAIR_ACTIVE 2u

/* R6 X-trace ABI. Implemented in InitOutput.c; dix/present call without lorie.h. */
void lorieGateATraceXRequest(int major, int minor, uint32_t clientSeq);
void lorieGateATraceXCallback(uint32_t xop, uint64_t gpuSerial, uint32_t clientSeq);
int lorieGateAPresentRequeueShouldFail(void);
void lorieGateATracePresentEarlyAck(uint64_t gpuSerial, uint64_t dstId);
void lorieGateATracePresentRequeueFailed(uint64_t gpuSerial, uint64_t dstId);
void lorieGateATracePresentAckAfterCompleted(uint64_t gpuSerial, uint64_t dstId);
void lorieGateATracePresentRetire(uint64_t gpuSerial, uint64_t dstId, uint32_t waited);
uint64_t lorieGateACopyBufferId(void *buf);
void lorieGateADumpSummary(struct lorie_shared_server_state *st, const char *where);

typedef enum {
    LORIE_GATEA_COUNTER_DIRECT_PUBLISH = 0,
    LORIE_GATEA_COUNTER_DIRECT_CONSUME,
    LORIE_GATEA_COUNTER_DIRECT_LOOKUP,
    LORIE_GATEA_COUNTER_DIRECT_LOOKUP_FAIL,
    LORIE_GATEA_COUNTER_DIRECT_DRAW,
    LORIE_GATEA_COUNTER_FENCE_SATISFIED,
    LORIE_GATEA_COUNTER_SEMANTIC_SUCCESS,
    LORIE_GATEA_COUNTER_DIRECT_TO_LEGACY,
    LORIE_GATEA_COUNTER_RELOCK,
    LORIE_GATEA_COUNTER_REPAIR,
    LORIE_GATEA_COUNTER_ACK,
    LORIE_GATEA_COUNTER_PENDING_DEC,
    LORIE_GATEA_COUNTER_AHB_ACQUIRE,
    LORIE_GATEA_COUNTER_AHB_RELEASE,
    LORIE_GATEA_COUNTER_EGLIMAGE_CREATE,
    LORIE_GATEA_COUNTER_EGLIMAGE_DESTROY,
    LORIE_GATEA_COUNTER_TEXTURE_CREATE,
    LORIE_GATEA_COUNTER_TEXTURE_DELETE,
    LORIE_GATEA_COUNTER_X_REGISTRY_CURRENT,
    LORIE_GATEA_COUNTER_RENDERER_REGISTRY_CURRENT,
    LORIE_GATEA_COUNTER_LEASE_CURRENT,
    LORIE_GATEA_COUNTER_FENCE_TIMEOUT,
    LORIE_GATEA_COUNTER_FENCE_ERROR,
    LORIE_GATEA_COUNTER_FIRST_FAILED,
    LORIE_GATEA_COUNTER_GENERATION_FATAL,
    LORIE_GATEA_COUNTER_UNREGISTER,
    LORIE_GATEA_COUNTER_RESOURCE_DESTROY,
    LORIE_GATEA_COUNTER_GENERATION_CLOSE,
    LORIE_GATEA_COUNTER_MAX,
} LorieGateACounter;

#define LORIE_GATEA_TRACE_CAPACITY 512u

typedef enum {
    LORIE_GATEA_ROLE_X = 1,
    LORIE_GATEA_ROLE_RENDERER = 2,
} LorieGateARole;

struct LorieGateATraceRecord {
    uint64_t sequence;
    uint64_t generation;
    uint64_t serial;
    uint64_t srcId;
    uint64_t dstId;
    uint32_t role;
    uint32_t event; /* release-published last; NONE means incomplete */
};

struct LorieGateATelemetry {
    uint64_t nextSequence;
    uint64_t counters[LORIE_GATEA_COUNTER_MAX];
    uint32_t overflow;
    /* Published enable: 1 iff X requested exact "1" for this mapping.
     * Layout unchanged (was reserved). Default remains 0 / OFF. */
    uint32_t reserved;
    struct LorieGateATraceRecord records[LORIE_GATEA_TRACE_CAPACITY];
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
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_GENERATION_CLOSED == 28, "event 28 frozen");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_REQUEST_ARRIVED == 29, "r6 request-arrived");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_CALLBACK_EXECUTED == 30, "r6 callback");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_DIRECT_ADMIT_REJECT == 31, "r6 admit-reject");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_PRESENT_EARLY_ACK == 32, "r6 present-early-ack");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_PRESENT_REQUEUE_FAILED == 33, "r6 present-requeue-failed");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_PRESENT_ACK_AFTER_COMPLETED == 34, "r6 present-ack-after-completed");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_TEST_FAULT_FIRED == 35, "r7 test-fault-fired");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_PRESENT_RETIRE == 36, "r7 present-retire");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_EVENT_MAX == 37, "r7 event max");
LORIE_GATEA_STATIC_ASSERT(LORIE_GATEA_COUNTER_MAX == 28, "counter ABI frozen");
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

static inline __always_inline uint32_t lorieGateAObserveDirectMeta(
        const struct LorieGateADirectMeta *p, struct LorieGateADirectMeta *out) {
    uint32_t state = lorieGateALoadU32Acquire(&p->state);
    if (out != NULL) {
        out->state = state;
        out->reserved = p->reserved;
        out->nonce = p->nonce;
        out->generation = p->generation;
        out->serial = p->serial;
        out->srcId = p->srcId;
        out->dstId = p->dstId;
    }
    return state;
}

static inline __always_inline int lorieGateAPublishDirectMeta(
        struct LorieGateADirectMeta *p, uint64_t nonce, uint64_t generation,
        uint64_t serial, uint64_t srcId, uint64_t dstId) {
    uint32_t state = lorieGateALoadU32Acquire(&p->state);
    if (state == LORIE_GATEA_DIRECT_PUBLISHED)
        return -1;
    p->reserved = 0;
    p->nonce = nonce;
    p->generation = generation;
    p->serial = serial;
    p->srcId = srcId;
    p->dstId = dstId;
    lorieGateAStoreU32Release(&p->state, LORIE_GATEA_DIRECT_PUBLISHED);
    return 0;
}

static inline __always_inline void lorieGateAConsumeDirectMeta(
        struct LorieGateADirectMeta *p) {
    lorieGateAStoreU32Release(&p->state, LORIE_GATEA_DIRECT_CONSUMED);
}

static inline __always_inline int lorieGateAPrepareLegacyMeta(
        struct LorieGateADirectMeta *p) {
    if (lorieGateALoadU32Acquire(&p->state) == LORIE_GATEA_DIRECT_PUBLISHED)
        return -1;
    p->reserved = 0;
    p->nonce = p->generation = p->serial = p->srcId = p->dstId = 0;
    lorieGateAStoreU32Release(&p->state, LORIE_GATEA_DIRECT_EMPTY);
    return 0;
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

/* First-failure observers (P2 wiring). Acquire loads: the code word is only
 * meaningful after acquiring a nonzero serial, which this pairs with. */
static inline __always_inline uint64_t lorieGateAObserveFirstFailed(const struct LorieGateAProtocol *p) {
    return __atomic_load_n(&p->firstFailedSerial, __ATOMIC_ACQUIRE);
}

static inline __always_inline uint32_t lorieGateAObserveFirstFailureCode(const struct LorieGateAProtocol *p) {
    return __atomic_load_n(&p->firstFailureCode, __ATOMIC_ACQUIRE);
}

/* Gate A P2 finite fence budget (nanoseconds). Matches the 2000 ms Done-wait
 * precedent. EGL_FOREVER is forbidden on every Gate A completion path: only
 * EGL_CONDITION_SATISFIED_KHR within this budget may lead to normal
 * completion; anything else is FATAL unless quiescence is proven separately. */
#define LORIE_GATEA_FENCE_TIMEOUT_NS 2000000000ull

/* Renderer idle recheck interval while waitForNextFrame is set.
 *
 * Invariant: if GPU-copy work becomes visible in the sticky queue while the
 * renderer is frame-gated, the renderer must re-evaluate the queue within this
 * interval even if the associated condvar signal is lost.
 *
 * This is NOT a GPU completion timeout and is NOT a change to
 * LORIE_GATEA_FENCE_TIMEOUT_NS or lorieGpuCopyWait(..., 2000). Those remain
 * the outer EXA fail-stop.
 *
 * X lorieGpuCopyWait is a usleep poll with no AChoreographer pump, so
 * waitForNextFrame stays true for the whole EXA Done-wait. X cannot take
 * Activity stateLock (process-private); publish + pthread_cond_signal can
 * lose the wakeup if GLES has already decided to wait. The cond itself is
 * process-shared (mmap) with CLOCK_MONOTONIC so the 8 ms bound is not
 * stretched by a CLOCK_REALTIME rollback. X only signals; it never waits. */
#define LORIE_RENDERER_FRAME_WAIT_NS 8000000L

/* Gate A P2 drain outcome. Returned by value from
 * Renderer::applyPendingGpuCopiesLocked (renderer thread only). */
struct LorieGateABatchOut {
    uint64_t lastSerial;    /* highest consumed serial, 0 if none */
    uint64_t generation;    /* direct tuple generation, 0 for legacy-only */
    uint64_t lastSrcId;     /* last direct endpoint, telemetry/fatal only */
    uint64_t lastDstId;
    uint32_t gateASeen;     /* nonzero iff a Gate A entry was consumed */
    uint32_t stopOnFailure; /* nonzero iff consumption halted on sticky failure */
    uint32_t batchGlError;  /* first GL error observed after a Gate A draw (0 none) */
};

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

static inline __always_inline uint32_t lorieGateAWaiterObserve(struct LorieGateAWaiter *w) {
    uint32_t s;
    pthread_mutex_lock(&w->lock);
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

/* ---- Incremental record decoder (PROTO ON only) ----
 *
 * The socket is a byte stream. This state machine consumes at most one
 * nonblocking recv/recvmsg quantum per call and keeps every partial prefix,
 * header, body, payload, or ancillary segment in the decoder. FIONREAD is not
 * used as a framing decision. The output owns payload and receivedFd until
 * lorieRecordRelease(). */
#define LORIE_GATEA_WAIT_BUDGET_NS 2000000000ull
#define LORIE_RECORD_IO_QUANTUM 4096u
#define LORIE_RECORD_GATE_BODY_MAX 16u

typedef enum {
    LORIE_RECORD_WOULD_BLOCK = 0,
    LORIE_RECORD_INCOMPLETE = 1,
    LORIE_RECORD_PROGRESSED = 2,
    LORIE_RECORD_PEER_CLOSED = 3,
    LORIE_RECORD_IO_ERROR = 4,
    LORIE_RECORD_PROTOCOL_FATAL = 5,
} LorieRecordResult;

typedef enum {
    LORIE_RECORD_DECODER_X_REPLY = 1,
} LorieRecordDecoderMode;

typedef enum {
    LORIE_RECORD_PHASE_PREFIX = 0,
    LORIE_RECORD_PHASE_GATE_HEADER = 1,
    LORIE_RECORD_PHASE_GATE_BODY = 2,
    LORIE_RECORD_PHASE_LEGACY_BASE = 3,
    LORIE_RECORD_PHASE_LEGACY_PAYLOAD = 4,
    LORIE_RECORD_PHASE_LEGACY_FD = 5,
    LORIE_RECORD_PHASE_LEGACY_COMPLETE = 6,
} LorieRecordDecoderPhase;

struct LorieDecodedRecord {
    int isGate;
    struct LorieGateAFrame gate;
    uint8_t gateBody[LORIE_RECORD_GATE_BODY_MAX];
    size_t gateBodyLen;
    lorieEvent event;
    void *payload;
    size_t payloadLen;
    int receivedFd;
    int error;
};

struct LorieRecordDecoder {
    uint32_t mode;
    uint32_t phase;
    uint8_t prefix[sizeof(uint32_t)];
    size_t prefixLen;
    uint8_t gateHeader[sizeof(struct LorieGateAFrame)];
    size_t gateHeaderLen;
    uint8_t gateBody[LORIE_RECORD_GATE_BODY_MAX];
    size_t gateBodyLen;
    size_t gateBodyNeed;
    uint8_t legacyBase[sizeof(lorieEvent)];
    size_t legacyBaseLen;
    uint8_t *payload;
    size_t payloadLen;
    size_t payloadOffset;
    int receivedFd;
    int error;
};

static inline __always_inline void lorieRecordDecoderInit(
        struct LorieRecordDecoder *decoder, uint32_t mode) {
    memset(decoder, 0, sizeof(*decoder));
    decoder->mode = mode;
    decoder->phase = LORIE_RECORD_PHASE_PREFIX;
    decoder->receivedFd = -1;
}

static inline __always_inline void lorieRecordRelease(
        struct LorieDecodedRecord *record) {
    if (record == NULL)
        return;
    free(record->payload);
    record->payload = NULL;
    record->payloadLen = 0;
    if (record->receivedFd >= 0)
        close(record->receivedFd);
    record->receivedFd = -1;
}

static inline __always_inline void lorieRecordDecoderReset(
        struct LorieRecordDecoder *decoder) {
    uint32_t mode = decoder->mode;
    free(decoder->payload);
    if (decoder->receivedFd >= 0)
        close(decoder->receivedFd);
    memset(decoder, 0, sizeof(*decoder));
    decoder->mode = mode;
    decoder->phase = LORIE_RECORD_PHASE_PREFIX;
    decoder->receivedFd = -1;
}

static inline __always_inline void lorieRecordDecoderDestroy(
        struct LorieRecordDecoder *decoder) {
    lorieRecordDecoderReset(decoder);
}

static inline __always_inline int lorieRecordDecoderRead(
        int fd, void *buffer, size_t *have, size_t need, int *error) {
    size_t remaining;
    size_t request;
    ssize_t n;
    if (*have >= need)
        return 1;
    remaining = need - *have;
    request = remaining > LORIE_RECORD_IO_QUANTUM
        ? LORIE_RECORD_IO_QUANTUM : remaining;
    do {
        n = recv(fd, (uint8_t *)buffer + *have, request, MSG_DONTWAIT);
    } while (n < 0 && errno == EINTR);
    if (n > 0) {
        *have += (size_t)n;
        return 1;
    }
    if (n == 0) {
        *error = 0;
        return 0;
    }
    if (errno == EAGAIN || errno == EWOULDBLOCK) {
        *error = EAGAIN;
        return 2;
    }
    *error = errno;
    return -1;
}

static inline __always_inline int lorieRecordDecoderExpectedGateBody(
        uint32_t mode, uint16_t type, size_t *length) {
    if (mode == LORIE_RECORD_DECODER_X_REPLY) {
        switch (type) {
        case LORIE_GATEA_MSG_READY:
        case LORIE_GATEA_MSG_REGISTER_FAILED:
        case LORIE_GATEA_MSG_FATAL_NOTIFY:
            *length = sizeof(struct LorieGateAReadyBody);
            return 0;
        case LORIE_GATEA_MSG_UNREGISTER_ACK:
        case LORIE_GATEA_MSG_GENERATION_CLOSED:
            *length = 0;
            return 0;
        default:
            return -1;
        }
    }
    return -1;
}

static inline __always_inline int lorieRecordDecoderLegacyFdExpected(
        uint32_t mode, uint8_t type) {
    return mode == LORIE_RECORD_DECODER_X_REPLY
        && type == EVENT_RENDERER_WAKEUP_COND;
}

static inline __always_inline int lorieRecordDecoderPrepareGate(
        struct LorieRecordDecoder *decoder) {
    struct LorieGateAFrame frame;
    size_t bodyLen;
    memcpy(&frame, decoder->gateHeader, sizeof(frame));
    if (frame.magic != LORIE_GATEA_MAGIC
        || frame.version != LORIE_GATEA_PROTOCOL_VERSION
        || frame.reserved != 0
        || lorieRecordDecoderExpectedGateBody(decoder->mode, frame.type,
                                               &bodyLen) != 0
        || frame.length != bodyLen
        || bodyLen > LORIE_RECORD_GATE_BODY_MAX) {
        decoder->error = EPROTO;
        return -1;
    }
    decoder->gateBodyNeed = bodyLen;
    decoder->gateBodyLen = 0;
    decoder->phase = LORIE_RECORD_PHASE_GATE_BODY;
    return 0;
}

static inline __always_inline int lorieRecordDecoderPrepareLegacy(
        struct LorieRecordDecoder *decoder) {
    lorieEvent event;
    size_t payloadLen = 0;
    memcpy(&event, decoder->legacyBase, sizeof(event));
    switch (event.type) {
    case EVENT_SCREEN_SIZE:
        payloadLen = event.screenSize.name_size;
        break;
    case EVENT_CLIPBOARD_SEND:
        payloadLen = event.clipboardSend.count;
        break;
    default:
        break;
    }
    if (payloadLen > SIZE_MAX - 1u) {
        decoder->error = EOVERFLOW;
        return -1;
    }
    decoder->payloadLen = payloadLen;
    decoder->payloadOffset = 0;
    if (payloadLen != 0) {
        decoder->payload = (uint8_t *)calloc(1, payloadLen + 1u);
        if (decoder->payload == NULL) {
            decoder->error = ENOMEM;
            return -1;
        }
    }
    if (lorieRecordDecoderLegacyFdExpected(decoder->mode, event.type))
        decoder->phase = LORIE_RECORD_PHASE_LEGACY_FD;
    else if (payloadLen != 0)
        decoder->phase = LORIE_RECORD_PHASE_LEGACY_PAYLOAD;
    else
        decoder->phase = LORIE_RECORD_PHASE_LEGACY_COMPLETE;
    return 0;
}

static inline __always_inline void lorieRecordDecoderCloseAncillaryFds(
        struct msghdr *message) {
    struct cmsghdr *cmsg;
    if (message == NULL)
        return;
    for (cmsg = CMSG_FIRSTHDR(message); cmsg != NULL;
         cmsg = CMSG_NXTHDR(message, cmsg)) {
        size_t nbytes;
        size_t nfd;
        size_t i;
        if (cmsg->cmsg_level != SOL_SOCKET || cmsg->cmsg_type != SCM_RIGHTS
            || cmsg->cmsg_len < CMSG_LEN(sizeof(int)))
            continue;
        nbytes = (size_t)cmsg->cmsg_len - CMSG_LEN(0);
        nfd = nbytes / sizeof(int);
        for (i = 0; i < nfd; i++) {
            int passed = -1;
            memcpy(&passed, CMSG_DATA(cmsg) + (i * sizeof(int)), sizeof(passed));
            if (passed >= 0)
                close(passed);
        }
    }
}

static inline __always_inline LorieRecordResult lorieRecordDecoderReceiveFd(
        struct LorieRecordDecoder *decoder, int fd) {
    char byte = 0;
    struct iovec iov = { .iov_base = &byte, .iov_len = sizeof(byte) };
    union {
        struct cmsghdr header;
        uint8_t bytes[CMSG_SPACE(sizeof(int))];
    } control = {0};
    struct msghdr message = {
        .msg_name = NULL,
        .msg_namelen = 0,
        .msg_iov = &iov,
        .msg_iovlen = 1,
        .msg_control = control.bytes,
        .msg_controllen = sizeof(control.bytes),
    };
    ssize_t n;
    struct cmsghdr *cmsg;
    int received;
    do {
        n = recvmsg(fd, &message, MSG_DONTWAIT);
    } while (n < 0 && errno == EINTR);
    if (n < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            decoder->error = EAGAIN;
            return LORIE_RECORD_WOULD_BLOCK;
        }
        decoder->error = errno;
        return LORIE_RECORD_IO_ERROR;
    }
    if (n == 0) {
        decoder->error = 0;
        return LORIE_RECORD_PEER_CLOSED;
    }
    cmsg = CMSG_FIRSTHDR(&message);
    received = -1;
    if (cmsg != NULL && cmsg->cmsg_level == SOL_SOCKET
        && cmsg->cmsg_type == SCM_RIGHTS
        && cmsg->cmsg_len >= CMSG_LEN(sizeof(int)))
        memcpy(&received, CMSG_DATA(cmsg), sizeof(received));
    if (n != 1 || (message.msg_flags & MSG_CTRUNC) != 0
        || cmsg == NULL || cmsg->cmsg_level != SOL_SOCKET
        || cmsg->cmsg_type != SCM_RIGHTS
        || cmsg->cmsg_len != CMSG_LEN(sizeof(int))
        || CMSG_NXTHDR(&message, cmsg) != NULL
        || received < 0) {
        lorieRecordDecoderCloseAncillaryFds(&message);
        decoder->error = received < 0 ? EBADF : EPROTO;
        return LORIE_RECORD_PROTOCOL_FATAL;
    }
    decoder->receivedFd = received;
    decoder->phase = LORIE_RECORD_PHASE_PREFIX;
    return LORIE_RECORD_PROGRESSED;
}

static inline __always_inline LorieRecordResult lorieRecordDecoderEmit(
        struct LorieRecordDecoder *decoder, struct LorieDecodedRecord *out,
        int isGate) {
    memset(out, 0, sizeof(*out));
    out->isGate = isGate;
    out->receivedFd = -1;
    out->error = decoder->error;
    if (isGate) {
        memcpy(&out->gate, decoder->gateHeader, sizeof(out->gate));
        memcpy(out->gateBody, decoder->gateBody, decoder->gateBodyNeed);
        out->gateBodyLen = decoder->gateBodyNeed;
    } else {
        memcpy(&out->event, decoder->legacyBase, sizeof(out->event));
        out->payload = decoder->payload;
        out->payloadLen = decoder->payloadLen;
        decoder->payload = NULL;
        if (out->event.type == EVENT_SCREEN_SIZE)
            out->event.screenSize.name = (char *)out->payload;
    }
    out->receivedFd = decoder->receivedFd;
    decoder->receivedFd = -1;
    lorieRecordDecoderReset(decoder);
    return LORIE_RECORD_PROGRESSED;
}

/* A record that needs no further socket bytes must PROGRESS in this call.
 * Returning INCOMPLETE here makes the waiter pump poll an empty socket and
 * age a complete UNREGISTER_ACK / GENERATION_CLOSED / fixed legacy event
 * into timeout. */
static inline __always_inline LorieRecordResult lorieRecordDecoderEmitIfComplete(
        struct LorieRecordDecoder *decoder, struct LorieDecodedRecord *out) {
    if (decoder->phase == LORIE_RECORD_PHASE_GATE_BODY
        && decoder->gateBodyNeed == 0)
        return lorieRecordDecoderEmit(decoder, out, 1);
    if (decoder->phase == LORIE_RECORD_PHASE_LEGACY_COMPLETE)
        return lorieRecordDecoderEmit(decoder, out, 0);
    return LORIE_RECORD_INCOMPLETE;
}

static inline __always_inline LorieRecordResult lorieRecordDecoderNext(
        int fd, struct LorieRecordDecoder *decoder,
        struct LorieDecodedRecord *out) {
    int readResult;
    if (fd < 0) {
        decoder->error = EBADF;
        return LORIE_RECORD_IO_ERROR;
    }
    switch (decoder->phase) {
    case LORIE_RECORD_PHASE_PREFIX:
        readResult = lorieRecordDecoderRead(fd, decoder->prefix,
                                            &decoder->prefixLen,
                                            sizeof(decoder->prefix),
                                            &decoder->error);
        if (readResult == 0)
            return LORIE_RECORD_PEER_CLOSED;
        if (readResult == 2)
            return LORIE_RECORD_WOULD_BLOCK;
        if (readResult < 0)
            return LORIE_RECORD_IO_ERROR;
        if (decoder->prefixLen != sizeof(decoder->prefix))
            return LORIE_RECORD_INCOMPLETE;
        {
            uint32_t magic;
            memcpy(&magic, decoder->prefix, sizeof(magic));
            if (magic == LORIE_GATEA_MAGIC) {
                memcpy(decoder->gateHeader, decoder->prefix,
                       sizeof(decoder->prefix));
                decoder->gateHeaderLen = sizeof(decoder->prefix);
                decoder->phase = LORIE_RECORD_PHASE_GATE_HEADER;
            } else {
                memcpy(decoder->legacyBase, decoder->prefix,
                       sizeof(decoder->prefix));
                decoder->legacyBaseLen = sizeof(decoder->prefix);
                decoder->phase = LORIE_RECORD_PHASE_LEGACY_BASE;
            }
        }
        return LORIE_RECORD_INCOMPLETE;
    case LORIE_RECORD_PHASE_GATE_HEADER:
        readResult = lorieRecordDecoderRead(fd, decoder->gateHeader,
                                            &decoder->gateHeaderLen,
                                            sizeof(decoder->gateHeader),
                                            &decoder->error);
        if (readResult == 0)
            return LORIE_RECORD_PEER_CLOSED;
        if (readResult == 2)
            return LORIE_RECORD_WOULD_BLOCK;
        if (readResult < 0)
            return LORIE_RECORD_IO_ERROR;
        if (decoder->gateHeaderLen != sizeof(decoder->gateHeader))
            return LORIE_RECORD_INCOMPLETE;
        if (lorieRecordDecoderPrepareGate(decoder) != 0)
            return LORIE_RECORD_PROTOCOL_FATAL;
        return lorieRecordDecoderEmitIfComplete(decoder, out);
    case LORIE_RECORD_PHASE_GATE_BODY:
        if (decoder->gateBodyNeed == 0)
            return lorieRecordDecoderEmit(decoder, out, 1);
        readResult = lorieRecordDecoderRead(fd, decoder->gateBody,
                                            &decoder->gateBodyLen,
                                            decoder->gateBodyNeed,
                                            &decoder->error);
        if (readResult == 0)
            return LORIE_RECORD_PEER_CLOSED;
        if (readResult == 2)
            return LORIE_RECORD_WOULD_BLOCK;
        if (readResult < 0)
            return LORIE_RECORD_IO_ERROR;
        if (decoder->gateBodyLen != decoder->gateBodyNeed)
            return LORIE_RECORD_INCOMPLETE;
        return lorieRecordDecoderEmit(decoder, out, 1);
    case LORIE_RECORD_PHASE_LEGACY_BASE:
        if (decoder->legacyBaseLen != sizeof(decoder->legacyBase)) {
            readResult = lorieRecordDecoderRead(fd, decoder->legacyBase,
                                                &decoder->legacyBaseLen,
                                                sizeof(decoder->legacyBase),
                                                &decoder->error);
            if (readResult == 0)
                return LORIE_RECORD_PEER_CLOSED;
            if (readResult == 2)
                return LORIE_RECORD_WOULD_BLOCK;
            if (readResult < 0)
                return LORIE_RECORD_IO_ERROR;
            if (decoder->legacyBaseLen != sizeof(decoder->legacyBase))
                return LORIE_RECORD_INCOMPLETE;
            if (lorieRecordDecoderPrepareLegacy(decoder) != 0)
                return LORIE_RECORD_PROTOCOL_FATAL;
            return lorieRecordDecoderEmitIfComplete(decoder, out);
        }
        if (lorieRecordDecoderPrepareLegacy(decoder) != 0)
            return LORIE_RECORD_PROTOCOL_FATAL;
        return lorieRecordDecoderEmitIfComplete(decoder, out);
    case LORIE_RECORD_PHASE_LEGACY_COMPLETE:
        return lorieRecordDecoderEmit(decoder, out, 0);
    case LORIE_RECORD_PHASE_LEGACY_PAYLOAD:
        readResult = lorieRecordDecoderRead(fd, decoder->payload,
                                            &decoder->payloadOffset,
                                            decoder->payloadLen,
                                            &decoder->error);
        if (readResult == 0)
            return LORIE_RECORD_PEER_CLOSED;
        if (readResult == 2)
            return LORIE_RECORD_WOULD_BLOCK;
        if (readResult < 0)
            return LORIE_RECORD_IO_ERROR;
        if (decoder->payloadOffset != decoder->payloadLen)
            return LORIE_RECORD_INCOMPLETE;
        return lorieRecordDecoderEmit(decoder, out, 0);
    case LORIE_RECORD_PHASE_LEGACY_FD:
        if (decoder->receivedFd < 0) {
            LorieRecordResult result = lorieRecordDecoderReceiveFd(decoder, fd);
            if (result != LORIE_RECORD_PROGRESSED)
                return result;
        }
        return lorieRecordDecoderEmit(decoder, out, 0);
    default:
        decoder->error = EPROTO;
        return LORIE_RECORD_PROTOCOL_FATAL;
    }
}

/* Deferred legacy ownership. The X pump adopts a decoded record, then either
 * executes it after the active request or cancels it on generation close.
 * Payload memory and received FDs stay owned here until dispose. */
struct LorieDeferredLegacyRecord {
    lorieEvent event;
    void *payload;
    size_t payloadLen;
    int receivedFd;
    uint64_t nonce;
    uint64_t generation;
    int cancelled;
    struct LorieDeferredLegacyRecord *next;
#ifdef LORIE_ENABLE_R8_TEST_SUPPORT
    uint64_t r8LocalId;
    uint8_t r8Type;
#endif
};

static inline __always_inline void lorieDeferredLegacyDisposeOwned(
        struct LorieDeferredLegacyRecord *record) {
    if (record == NULL)
        return;
    free(record->payload);
    record->payload = NULL;
    record->payloadLen = 0;
    if (record->receivedFd >= 0)
        close(record->receivedFd);
    record->receivedFd = -1;
}

static inline __always_inline void lorieDeferredLegacyAdoptDecoded(
        struct LorieDeferredLegacyRecord *queued,
        struct LorieDecodedRecord *record) {
    queued->event = record->event;
    queued->payload = record->payload;
    queued->payloadLen = record->payloadLen;
    queued->receivedFd = record->receivedFd;
    record->payload = NULL;
    record->receivedFd = -1;
    if (queued->event.type == EVENT_SCREEN_SIZE)
        queued->event.screenSize.name = (char *)queued->payload;
}

static inline __always_inline void lorieDeferredLegacyCancelIfMatch(
        struct LorieDeferredLegacyRecord *record,
        uint64_t nonce, uint64_t generation) {
    if (record->nonce == nonce && record->generation == generation) {
        record->cancelled = 1;
        lorieDeferredLegacyDisposeOwned(record);
    }
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
int lorieGateASendUnregister(uint64_t id, uint64_t nonce, uint64_t generation,
                             uint64_t lastSubmittedSerial);
int lorieGateASendGenerationClose(uint64_t nonce, uint64_t generation,
                                  uint64_t lastPublishedSerial);

/* X-side registry (cmdentrypoint.cpp). Pool-stable slots: waiter addresses
 * stay valid for process lifetime, so input-thread signaling needs no
 * refcounting. Insert arms nothing (P3 arms before send); mark updates state
 * and signals the entry waiter. */
int lorieGateARegistryInsert(uint64_t nonce, uint64_t generation, uint64_t id, uint64_t fingerprint);
int lorieGateARegistryFind(uint64_t id, struct LorieGateABufferMeta *out);
int lorieGateARegistryMarkChecked(uint64_t id, uint64_t nonce, uint64_t generation,
                                  uint64_t fingerprint, int ready, uint32_t code);
int lorieGateARegistryMarkPairReserved(uint64_t srcId, uint64_t dstId);
int lorieGateARegistryMarkSubmitted(uint64_t srcId, uint64_t dstId, uint64_t serial);
int lorieGateARegistryMarkPairReleased(uint64_t srcId, uint64_t dstId, uint64_t serial);
int lorieGateARegistryBeginRetire(uint64_t id, struct LorieGateABufferMeta *out);
int lorieGateARegistryHandleUnregisterAck(uint64_t id, uint64_t nonce, uint64_t generation);
int lorieGateARegistryRemoveAcked(uint64_t id);
int lorieGateARegistrySnapshot(uint64_t nonce, uint64_t generation,
                               uint64_t *ids, uint32_t capacity);
struct LorieGateAWaiter *lorieGateARegistryWaiter(uint64_t id);
struct LorieGateAWaiter *lorieGateAGenerationWaiter(void);
void lorieGateAGenerationWaiterArm(void);
void lorieGateAGenerationClosedSignal(void);
/* Tombstone every entry of an old generation (wake waiters FAILED). Called on
 * generation rotation; entries never resurrect (insert-replace rules apply). */
void lorieGateARegistryCloseGeneration(uint64_t oldNonce, uint64_t oldGeneration);

/* X-side shared-state telescope (InitOutput.c). */
struct LorieGateAProtocol *lorieGateAShared(void);
struct lorie_shared_server_state *lorieGateASharedState(void);
int lorieGateAActive(void);

/* X-side bounded connection pump. It returns the same explicit decoder
 * results; a zero-timeout WOULD_BLOCK is a deadline quantum, not a retry
 * sleep. poll(2) EINTR retries recompute remaining milliseconds from the
 * caller's absolute CLOCK_MONOTONIC deadline. */
#define LORIE_GATEA_LEGACY_NORMAL 0u
#define LORIE_GATEA_LEGACY_DEFER 1u
#define LORIE_GATEA_LEGACY_CANCEL 2u
int lorieGateAPumpConnection(const struct timespec *deadline,
                             uint32_t legacyPolicy);
void lorieGateACancelDeferred(uint64_t nonce, uint64_t generation);

/* Activity-process logical writer contract. The mutex also guards conn_fd
 * close/rebind identity; helpers recheck conn_fd after locking and never hold
 * it across Java callbacks, renderer waits, fences, or X replies. */
extern pthread_mutex_t lorieActivityWriterMutex;
int lorieActivitySendLegacyRecord(const lorieEvent *event);
int lorieActivitySendLegacyPayload(const lorieEvent *event,
                                    const void *payload, size_t payloadLen);
int lorieActivitySendLegacyFd(const lorieEvent *event, int fd);
int lorieActivitySendGateFrame(const struct LorieGateAFrame *frame,
                               const void *body, size_t bodyLen);

/* Renderer GL-thread control queue. */
int lorieGateAEnqueueControl(uint32_t type, uint64_t id, uint64_t nonce,
                             uint64_t generation, uint64_t lastSerial);
void lorieGateAWakeRenderer(void);

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
int lorieGateAUnbindTuple(uint64_t nonce, uint64_t generation);

#define LORIE_GATEA_TEST_MAGIC 0x47374146u /* 'G7AF' */
#define LORIE_GATEA_TEST_VERSION 1u
/* X-only getenv TERMUX_X11_GATEA_TEST_FAULT + TERMUX_X11_GATEA_TEST_ARM=1.
 * Renderer never getenv; it reads gateATestFault from the shared tail. */
#define LORIE_GATEA_SUMMARY_PATH "/data/data/com.termux/files/usr/tmp/gatea-summary.txt"
#define LORIE_GATEA_RING_PATH "/data/data/com.termux/files/usr/tmp/gatea-ring.txt"

enum {
    LORIE_GATEA_TEST_NONE = 0,
    LORIE_GATEA_TEST_SRC_READY_MISS = 1,
    LORIE_GATEA_TEST_DST_READY_MISS = 2,
    LORIE_GATEA_TEST_TUPLE_MISMATCH = 3,
    LORIE_GATEA_TEST_FBO_INCOMPLETE = 4,
    LORIE_GATEA_TEST_POST_DRAW_GL = 5,
    LORIE_GATEA_TEST_FENCE_CREATE_FAIL = 6,
    LORIE_GATEA_TEST_FENCE_TIMEOUT = 7,
    LORIE_GATEA_TEST_RENDERER_FATAL_PRE_FENCE = 8,
    LORIE_GATEA_TEST_WRONG_GENERATION_FRAME = 9,
    LORIE_GATEA_TEST_RENDERER_EXIT_AFTER_CONSUME = 10,
    LORIE_GATEA_TEST_SERIAL_WRAP = 11,
    LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE = 12,
    LORIE_GATEA_TEST_PRESENT_RENDERER_EXIT = 13,
    LORIE_GATEA_TEST_DESTROY_WHILE_GPU_OWNED = 14,
    LORIE_GATEA_TEST_CLOSE_WHILE_LEASE = 15,
    LORIE_GATEA_TEST_STALE_READY_REPLAY = 16,
};

struct LorieGateATestFault {
    uint32_t magic;
    uint32_t version;
    uint32_t cell;
    uint32_t armed;
    uint32_t consumed;
    /* D-02 (R9): whole-run finalization authority for the RENDERER observation
     * stream. Formerly an unnamed alignment pad at +20, never read by anything.
     * X publishes 1 from ProcLorieR8Terminate before GiveUp(0); the renderer
     * requires it before emitting its process-level END. Size, every offset and
     * every static assert below are unchanged by naming it. Test-support only:
     * both the publisher and the consumer are inside
     * LORIE_ENABLE_R8_TEST_SUPPORT, and Production Gate A never reads it. */
    uint32_t runFinalize;
    uint64_t targetGeneration;
    uint64_t targetOrdinal;
};

LORIE_GATEA_STATIC_ASSERT(sizeof(struct LorieGateATestFault) == 40, "r7 test-fault size");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateATestFault, magic) == 0, "r7 test-fault magic off");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateATestFault, targetGeneration) == 24, "r7 test-fault gen off");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateATestFault, targetGeneration) % 8) == 0, "r7 test-fault gen aligned");
LORIE_GATEA_STATIC_ASSERT((offsetof(struct LorieGateATestFault, consumed) % 4) == 0, "r7 test-fault consumed aligned");
LORIE_GATEA_STATIC_ASSERT(offsetof(struct LorieGateATestFault, runFinalize) == 20, "d02 run-finalize off");


#include "lorie_gatea_test_fault_class.h"

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
        /* P2: accessed ONLY via lorieGateA* release/acquire accessors (never
         * plain or volatile-only). Non-volatile so the accessors instantiate
         * without qualifier warnings; layout/offsets are unchanged. */
        uint32_t writeIndex;
        uint32_t readIndex;
        uint64_t completedSerial;
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

    /* Gate A P0 result sideband. Appended after all legacy fields: existing
     * offsets unchanged. The frozen object itself remains exactly 40 bytes. */
    struct LorieGateAProtocol gateA;

    /* P2 additions are separate from both the 168-byte queue entry and the
     * frozen 40-byte result sideband. */
    struct LorieGateADirectMeta gateADirect[LORIE_GPU_COPY_QUEUE_CAPACITY];
    struct LorieGateATelemetry gateATelemetry;
    /* Artifact B tail. Existing field offsets unchanged. */
    struct LorieGateATestFault gateATestFault;
};

static inline __always_inline bool lorieGateASharedAtomicsLockFree(
        const struct lorie_shared_server_state *state) {
    uint32_t i;
    if (state == NULL || !lorieGateAAtomicsLockFree(&state->gateA))
        return false;
    for (i = 0; i < LORIE_GPU_COPY_QUEUE_CAPACITY; i++)
        if (!__atomic_is_lock_free(sizeof(state->gateADirect[i].state),
                                   &state->gateADirect[i].state))
            return false;
    return __atomic_is_lock_free(sizeof(state->gateATelemetry.nextSequence),
                                 &state->gateATelemetry.nextSequence)
        && __atomic_is_lock_free(sizeof(state->gateATelemetry.counters[0]),
                                 &state->gateATelemetry.counters[0])
        && __atomic_is_lock_free(sizeof(state->gateATelemetry.overflow),
                                 &state->gateATelemetry.overflow)
        && __atomic_is_lock_free(sizeof(state->gateATelemetry.reserved),
                                 &state->gateATelemetry.reserved)
        && __atomic_is_lock_free(sizeof(state->gateATestFault.armed),
                                 &state->gateATestFault.armed)
        && __atomic_is_lock_free(sizeof(state->gateATestFault.consumed),
                                 &state->gateATestFault.consumed);
}

/* ---- D-02 (R9): renderer observation finalization authority ----
 * X publishes this from ProcLorieR8Terminate immediately BEFORE GiveUp(0).
 * The renderer requires it before emitting its process-level END, so END
 * authority is "the whole run was explicitly finalized" and NOT
 * "the first generation unbound" / "the surface quiesced" / "the first epoch
 * ended". Release/acquire: a renderer that observes 1 also observes every
 * earlier X-side store. Test-support only; Production Gate A never reads it. */
static inline __always_inline void lorieGateAPublishRunFinalize(
        struct lorie_shared_server_state *state) {
    if (state == NULL)
        return;
    lorieGateAStoreU32Release(&state->gateATestFault.runFinalize, 1u);
}

static inline __always_inline int lorieGateAObserveRunFinalize(
        const struct lorie_shared_server_state *state) {
    if (state == NULL)
        return 0;
    return lorieGateALoadU32Acquire(&state->gateATestFault.runFinalize) == 1u;
}

/* Renderer/Activity enable authority: bound generation + published word.
 * Never getenv: the APK process does not inherit the X launcher flag. */
static inline __always_inline int lorieGateATelemetryPublished(
        const struct lorie_shared_server_state *state) {
    if (state == NULL)
        return 0;
    if (lorieGateALoadU64Acquire(&state->gateA.sessionNonce) == 0
        || lorieGateALoadU64Acquire(&state->gateA.generation) == 0)
        return 0;
    return lorieGateALoadU32Acquire(&state->gateATelemetry.reserved) == 1u;
}

static inline __always_inline void lorieGateACounterAdd(
        struct lorie_shared_server_state *state, uint32_t counter, int64_t delta) {
    uint64_t amount;
    if (!lorieGateATelemetryPublished(state) || state == NULL
        || counter >= LORIE_GATEA_COUNTER_MAX || delta == 0)
        return;
    amount = delta > 0 ? (uint64_t)delta : (uint64_t)(-delta);
    if (delta > 0)
        __atomic_fetch_add(&state->gateATelemetry.counters[counter], amount, __ATOMIC_RELAXED);
    else
        __atomic_fetch_sub(&state->gateATelemetry.counters[counter], amount, __ATOMIC_RELAXED);
}

static inline __always_inline uint32_t lorieGateACounterForEvent(uint32_t event) {
    switch (event) {
    case LORIE_GATEA_EVENT_PUBLISH: return LORIE_GATEA_COUNTER_DIRECT_PUBLISH;
    case LORIE_GATEA_EVENT_CONSUME_DIRECT: return LORIE_GATEA_COUNTER_DIRECT_CONSUME;
    case LORIE_GATEA_EVENT_DIRECT_LOOKUP_OK: return LORIE_GATEA_COUNTER_DIRECT_LOOKUP;
    case LORIE_GATEA_EVENT_DIRECT_LOOKUP_FAIL: return LORIE_GATEA_COUNTER_DIRECT_LOOKUP_FAIL;
    case LORIE_GATEA_EVENT_DRAW_SUBMIT: return LORIE_GATEA_COUNTER_DIRECT_DRAW;
    case LORIE_GATEA_EVENT_FENCE_SATISFIED: return LORIE_GATEA_COUNTER_FENCE_SATISFIED;
    case LORIE_GATEA_EVENT_FENCE_TIMEOUT: return LORIE_GATEA_COUNTER_FENCE_TIMEOUT;
    case LORIE_GATEA_EVENT_FENCE_ERROR: return LORIE_GATEA_COUNTER_FENCE_ERROR;
    case LORIE_GATEA_EVENT_FIRST_FAILED_SERIAL: return LORIE_GATEA_COUNTER_FIRST_FAILED;
    case LORIE_GATEA_EVENT_GENERATION_FATAL: return LORIE_GATEA_COUNTER_GENERATION_FATAL;
    case LORIE_GATEA_EVENT_SEMANTIC_SUCCESS: return LORIE_GATEA_COUNTER_SEMANTIC_SUCCESS;
    case LORIE_GATEA_EVENT_RELOCK_SRC:
    case LORIE_GATEA_EVENT_RELOCK_DST: return LORIE_GATEA_COUNTER_RELOCK;
    case LORIE_GATEA_EVENT_REPAIR: return LORIE_GATEA_COUNTER_REPAIR;
    case LORIE_GATEA_EVENT_ACK: return LORIE_GATEA_COUNTER_ACK;
    case LORIE_GATEA_EVENT_PENDING_DEC: return LORIE_GATEA_COUNTER_PENDING_DEC;
    case LORIE_GATEA_EVENT_UNREGISTER_SEND: return LORIE_GATEA_COUNTER_UNREGISTER;
    case LORIE_GATEA_EVENT_RESOURCE_DESTROY: return LORIE_GATEA_COUNTER_RESOURCE_DESTROY;
    case LORIE_GATEA_EVENT_GENERATION_CLOSE: return LORIE_GATEA_COUNTER_GENERATION_CLOSE;
    default: return LORIE_GATEA_COUNTER_MAX;
    }
}

static inline __always_inline void lorieGateATrace(
        struct lorie_shared_server_state *state, uint32_t role, uint32_t event,
        uint64_t generation, uint64_t serial, uint64_t srcId, uint64_t dstId) {
    struct LorieGateATraceRecord *record;
    uint64_t sequence;
    uint32_t counter;
    if (!lorieGateATelemetryPublished(state) || state == NULL
        || event == LORIE_GATEA_EVENT_NONE || event >= LORIE_GATEA_EVENT_MAX)
        return;
    counter = lorieGateACounterForEvent(event);
    if (counter < LORIE_GATEA_COUNTER_MAX)
        lorieGateACounterAdd(state, counter, 1);
    sequence = __atomic_fetch_add(&state->gateATelemetry.nextSequence, 1, __ATOMIC_RELAXED);
    if (sequence >= LORIE_GATEA_TRACE_CAPACITY)
        lorieGateAStoreU32Release(&state->gateATelemetry.overflow, 1);
    record = &state->gateATelemetry.records[sequence % LORIE_GATEA_TRACE_CAPACITY];
    lorieGateAStoreU32Release(&record->event, LORIE_GATEA_EVENT_NONE);
    record->sequence = sequence;
    record->generation = generation;
    record->serial = serial;
    record->srcId = srcId;
    record->dstId = dstId;
    record->role = role;
    lorieGateAStoreU32Release(&record->event, event);
    __android_log_print(ANDROID_LOG_INFO, "gatea-telemetry",
        "GATEA_EVENT seq=%llu role=%u event=%u generation=%llu serial=%llu src=%llu dst=%llu",
        (unsigned long long)sequence, role, event,
        (unsigned long long)generation, (unsigned long long)serial,
        (unsigned long long)srcId, (unsigned long long)dstId);
}

/* Qualification-only. Unarmed returns after the armed load; no I/O. */
static inline __always_inline int lorieGateATestFaultArmed(
        const struct lorie_shared_server_state *st, uint32_t cell) {
    if (st == NULL)
        return 0;
    return lorieGateATestFaultClassArmed(&st->gateATestFault, cell);
}

/* One-shot: CAS consumed 0→1, then event 35, then the caller injects. */
static inline __always_inline int lorieGateATestFaultConsume(
        struct lorie_shared_server_state *st, uint32_t cell,
        uint32_t role, uint64_t serial, uint64_t generation) {
    if (st == NULL)
        return 0;
    if (!lorieGateATestFaultClassConsume(&st->gateATestFault, cell, serial,
                                         generation))
        return 0;
    lorieGateATrace(st, role, LORIE_GATEA_EVENT_TEST_FAULT_FIRED,
                    generation, serial, (uint64_t)cell, (uint64_t)role);
    return 1;
}

/* Test-only. Present-bound cells 12/13 only. No-op if unconfigured, consumed,
 * or already armed for a different serial. */
static inline __always_inline int lorieGateATestFaultArmPresentTarget(
        struct lorie_shared_server_state *st, uint64_t serial,
        uint64_t generation) {
    if (st == NULL)
        return 0;
    return lorieGateATestFaultClassArmPresentTarget(&st->gateATestFault, serial,
                                                    generation);
}

/* Test-only cell-12 hold. Unarmed/unconsumed/other-serial returns 0 so
 * lorieGpuCopyIsDone stays completedSerial >= serial. */
static inline __always_inline int lorieGateATestFaultHoldsIncomplete(
        const struct lorie_shared_server_state *st, uint64_t serial) {
    if (st == NULL)
        return 0;
    return lorieGateATestFaultClassHoldsIncomplete(&st->gateATestFault, serial);
}

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
    void wakeGateA();
    void addBuffer(LorieBuffer* buf);
    void removeBuffer(uint64_t id);
    void removeAllBuffers();
    void setWindow(JNIEnv* env, jobject jsfc);
    void setViewport(int x, int y, int w, int h, int ew, int eh, int hidden);
    void setZoom(int percent);
    void releaseWinAndSurface(ANativeWindow** anw, EGLSurface* esfc);
    void refreshContext();
    LorieBuffer* findBufferWithRetry(uint64_t id);
    struct LorieGateABatchOut applyPendingGpuCopiesLocked();
    /* P2 Gate A direct consume: persistent READY textures only. Returns 0 on
     * submit; nonzero on pre-draw setup failure (caller fail-stops). */
    int consumeGateAComposite(const LorieGpuCopyEntry *entry, bool *fboSetUp,
                              uint64_t *boundDstId, GLint prevViewport[4],
                              GLenum *glErrorOut);
    void applyPendingGpuCopies();
    void redrawLocked(bool* waitingForBuffers);
    bool shouldWait(bool* waitingForBuffers);
    void waitWhileIdle(bool* waitingForBuffers);
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
