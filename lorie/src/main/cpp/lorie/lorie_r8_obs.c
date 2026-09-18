#ifdef LORIE_ENABLE_R8_TEST_SUPPORT

#include "lorie_r8_obs.h"
#include "r8-test-protocol.h"

#include <android/log.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>

static pthread_mutex_t r8ObsMu = PTHREAD_MUTEX_INITIALIZER;
static int r8Armed = -1;
static int r8EnvFatal = 0;
static char r8Case[64];
static uint32_t r8CaseCode;
static uint64_t r8Nonce;
static uint64_t r8Generation;
static uint64_t r8Seq[2];
static uint64_t r8Fnv[2];
static int r8Began[2];
static int r8Ended[2];
static uint64_t r8WakeSend;
static uint64_t r8WakeRecv;
static uint64_t r8DeferIds;

static int roleIx(const char *role) {
    if (role && role[0] == 'r')
        return 1;
    return 0;
}

static uint64_t fnv64(uint64_t h, const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    if (h == 0)
        h = 1469598103934665603ull;
    while (p && *p) {
        h ^= *p++;
        h *= 1099511628211ull;
    }
    return h;
}

static long r8Tid(void) {
    return (long)syscall(SYS_gettid);
}

static int parseArm(void) {
    const char *arm;
    const char *cas;
    const char *fault;
    const char *tarm;
    const char *r6;
    uint32_t code;

    if (r8Armed >= 0)
        return r8Armed;

    arm = getenv("TERMUX_X11_R8_ARM");
    cas = getenv("TERMUX_X11_R8_CASE");
    fault = getenv("TERMUX_X11_GATEA_TEST_FAULT");
    tarm = getenv("TERMUX_X11_GATEA_TEST_ARM");
    r6 = getenv("TERMUX_X11_GATEA_R6_PRESENT_REQUEUE_FAIL");
    r8Case[0] = 0;
    r8CaseCode = 0;
    r8Armed = 0;
    if (arm == NULL || arm[0] != '1' || arm[1] != '\0')
        return 0;
    if (cas == NULL || cas[0] == '\0' || strlen(cas) >= sizeof(r8Case)) {
        r8EnvFatal = 1;
        return 0;
    }
    memcpy(r8Case, cas, strlen(cas) + 1);
    code = lorieR8CaseCode(cas);
    r8CaseCode = code;
    if (code == 0 || r6 != NULL) {
        r8EnvFatal = 1;
        return 0;
    }
    if (code == LORIE_R8_CASE_P1) {
        if (fault == NULL || strcmp(fault, "destroy-while-gpu-owned") != 0
            || tarm == NULL || tarm[0] != '1' || tarm[1] != '\0')
            r8EnvFatal = 1;
    } else if (code == LORIE_R8_CASE_P2) {
        if (fault == NULL || strcmp(fault, "close-while-lease") != 0
            || tarm == NULL || tarm[0] != '1' || tarm[1] != '\0')
            r8EnvFatal = 1;
    } else if (fault != NULL || tarm != NULL) {
        r8EnvFatal = 1;
    }
    if (r8EnvFatal)
        return 0;
    r8Armed = 1;
    return 1;
}

int lorieR8Armed(void) {
    return parseArm();
}

const char *lorieR8CaseName(void) {
    parseArm();
    return r8Case[0] ? r8Case : NULL;
}

uint32_t lorieR8CaseCodeCached(void) {
    parseArm();
    return r8CaseCode;
}

void lorieR8BindTuple(uint64_t nonce, uint64_t generation) {
    if (r8Nonce == 0 && nonce != 0) {
        r8Nonce = nonce;
        r8Generation = generation;
    }
}

void lorieR8OriginalTuple(uint64_t *nonce, uint64_t *generation) {
    if (nonce)
        *nonce = r8Nonce;
    if (generation)
        *generation = r8Generation;
}

int lorieR8ValidateStartupEnv(int proto, int telemetry) {
    parseArm();
    if (r8Armed != 1)
        return r8EnvFatal ? -1 : 0;
    if (!proto || !telemetry)
        return -1;
    return 1;
}

void lorieR8ObsBegin(const char *role) {
    int ix = roleIx(role);
    pthread_mutex_lock(&r8ObsMu);
    if (!r8Began[ix]) {
        r8Began[ix] = 1;
        r8Seq[ix] = 0;
        r8Fnv[ix] = 1469598103934665603ull;
        __android_log_print(ANDROID_LOG_INFO, "R8_OBS",
            "R8_OBS {\"v\":1,\"role\":\"%s\",\"pid\":%d,\"tid\":%ld,\"producer_seq\":0,"
            "\"case\":%s%s%s,\"phase\":\"BEGIN\",\"original_nonce\":%llu,"
            "\"original_generation\":%llu,\"expected_count\":null,\"expected_digest\":null}",
            role ? role : "x", (int)getpid(), r8Tid(),
            r8Case[0] ? "\"" : "", r8Case[0] ? r8Case : "null", r8Case[0] ? "\"" : "",
            (unsigned long long)r8Nonce, (unsigned long long)r8Generation);
    }
    pthread_mutex_unlock(&r8ObsMu);
}

void lorieR8ObsEnd(const char *role) {
    int ix = roleIx(role);
    pthread_mutex_lock(&r8ObsMu);
    if (r8Began[ix] && !r8Ended[ix]) {
        r8Ended[ix] = 1;
        __android_log_print(ANDROID_LOG_INFO, "R8_OBS",
            "R8_OBS {\"v\":1,\"role\":\"%s\",\"pid\":%d,\"tid\":%ld,\"producer_seq\":%llu,"
            "\"case\":%s%s%s,\"phase\":\"END\",\"original_nonce\":%llu,"
            "\"original_generation\":%llu,\"actual_count\":%llu,\"actual_digest\":\"%016llx\"}",
            role ? role : "x", (int)getpid(), r8Tid(),
            (unsigned long long)r8Seq[ix],
            r8Case[0] ? "\"" : "", r8Case[0] ? r8Case : "null", r8Case[0] ? "\"" : "",
            (unsigned long long)r8Nonce, (unsigned long long)r8Generation,
            (unsigned long long)r8Seq[ix],
            (unsigned long long)r8Fnv[ix]);
    }
    pthread_mutex_unlock(&r8ObsMu);
}

void lorieR8Obs(const char *role, const char *phase, const char *fields) {
    int ix = roleIx(role);
    char line[2048];
    int n;

    if (role && role[0] == 'x' && !lorieR8Armed())
        return;
    pthread_mutex_lock(&r8ObsMu);
    if (!r8Began[ix]) {
        pthread_mutex_unlock(&r8ObsMu);
        lorieR8ObsBegin(role);
        pthread_mutex_lock(&r8ObsMu);
    }
    r8Seq[ix]++;
    n = snprintf(line, sizeof(line),
        "{\"v\":1,\"role\":\"%s\",\"pid\":%d,\"tid\":%ld,\"producer_seq\":%llu,"
        "\"case\":%s%s%s,\"phase\":\"%s\",\"original_nonce\":%llu,"
        "\"original_generation\":%llu%s%s}",
        role ? role : "x", (int)getpid(), r8Tid(),
        (unsigned long long)r8Seq[ix],
        r8Case[0] ? "\"" : "", r8Case[0] ? r8Case : "null", r8Case[0] ? "\"" : "",
        phase ? phase : "UNKNOWN",
        (unsigned long long)r8Nonce, (unsigned long long)r8Generation,
        (fields && fields[0]) ? "," : "",
        (fields && fields[0]) ? fields : "");
    if (n > 0)
        r8Fnv[ix] = fnv64(r8Fnv[ix], line);
    __android_log_print(ANDROID_LOG_INFO, "R8_OBS", "R8_OBS %s", line);
    pthread_mutex_unlock(&r8ObsMu);
}

uint64_t lorieR8WakeSent(const char *cause, int send_ok, uint64_t completed) {
    uint64_t ord = 0;
    char fields[256];
    if (!send_ok) {
        snprintf(fields, sizeof(fields),
                 "\"send_ok\":false,\"cause\":\"%s\",\"completedSerial\":%llu",
                 cause ? cause : "unknown", (unsigned long long)completed);
        lorieR8Obs("r", "R_WAKE_SENT", fields);
        return 0;
    }
    pthread_mutex_lock(&r8ObsMu);
    r8WakeSend++;
    ord = r8WakeSend;
    pthread_mutex_unlock(&r8ObsMu);
    snprintf(fields, sizeof(fields),
             "\"send_ok\":true,\"ordinal\":%llu,\"cause\":\"%s\",\"completedSerial\":%llu",
             (unsigned long long)ord, cause ? cause : "unknown",
             (unsigned long long)completed);
    lorieR8Obs("r", "R_WAKE_SENT", fields);
    return ord;
}

uint64_t lorieR8WakeReceived(uint64_t ordinal_hint) {
    uint64_t ord;
    char fields[160];
    pthread_mutex_lock(&r8ObsMu);
    r8WakeRecv++;
    ord = r8WakeRecv;
    pthread_mutex_unlock(&r8ObsMu);
    (void)ordinal_hint;
    snprintf(fields, sizeof(fields), "\"ordinal\":%llu", (unsigned long long)ord);
    lorieR8Obs("x", "X_WAKE_RECEIVED", fields);
    return ord;
}

uint64_t lorieR8DeferAllocId(void) {
    uint64_t id;
    pthread_mutex_lock(&r8ObsMu);
    r8DeferIds++;
    id = r8DeferIds;
    pthread_mutex_unlock(&r8ObsMu);
    return id;
}

#endif /* LORIE_ENABLE_R8_TEST_SUPPORT */
