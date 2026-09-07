#ifdef HAVE_DIX_CONFIG_H
#include <dix-config.h>
#endif

#include <android/log.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "dixstruct.h"
#include "registry.h"
#include "lorie.h"

#define log(prio, ...) __android_log_print(ANDROID_LOG_ ## prio, "LorieNative", __VA_ARGS__)

#define LORIE_FLIGHT_MAGIC 0x46524C31u
#define LORIE_FLIGHT_CAP 256

typedef struct {
    uint32_t seq;
    uint32_t req_len;
    uint16_t minor;
    uint16_t client;
    uint8_t major;
    uint8_t phase;              /* 1 = enter, 2 = leave */
    int16_t result;
    uint8_t head[16];
} LorieFlightEnt;

typedef struct {
    uint32_t magic;
    uint32_t cap;
    volatile uint32_t w;
    uint32_t pad;
    LorieFlightEnt ents[LORIE_FLIGHT_CAP];
} LorieFlightRing;

static LorieFlightRing *flightRing;
static int (*savedProc[256]) (ClientPtr);
static int (*savedSwap[256]) (ClientPtr);
static char flightPath[256];
static int dumpFd = -1;

static void lorieFlightOpenRing(void) {
    const char *tmp;
    int fd;
    size_t sz = sizeof(LorieFlightRing);

    if (flightRing)
        return;

    tmp = getenv("TMPDIR");
    if (!tmp || !tmp[0])
        tmp = "/tmp";
    snprintf(flightPath, sizeof(flightPath), "%s/x11gpu-flight.ring", tmp);

    fd = open(flightPath, O_RDWR | O_CREAT | O_CLOEXEC, 0666);
    if (fd < 0) {
        snprintf(flightPath, sizeof(flightPath), "/data/data/com.termux/files/usr/tmp/x11gpu-flight.ring");
        fd = open(flightPath, O_RDWR | O_CREAT | O_CLOEXEC, 0666);
    }
    if (fd < 0) {
        log(ERROR, "flight recorder: open failed: %s", strerror(errno));
        return;
    }
    if (ftruncate(fd, (off_t) sz) != 0) {
        log(ERROR, "flight recorder: ftruncate failed: %s", strerror(errno));
        close(fd);
        return;
    }
    flightRing = mmap(NULL, sz, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (flightRing == MAP_FAILED) {
        flightRing = NULL;
        log(ERROR, "flight recorder: mmap failed: %s", strerror(errno));
        return;
    }
    memset(flightRing, 0, sz);
    flightRing->magic = LORIE_FLIGHT_MAGIC;
    flightRing->cap = LORIE_FLIGHT_CAP;
    {
        char txt[256];
        snprintf(txt, sizeof(txt), "%s.txt", flightPath);
        dumpFd = open(txt, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0666);
    }
    log(INFO, "flight recorder installed path=%s cap=%u", flightPath, LORIE_FLIGHT_CAP);
}

static int lorieFlightProc(ClientPtr client) {
    int (*fn) (ClientPtr);
    LorieFlightEnt *e;
    uint32_t slot, n;
    int rc;

    fn = client->swapped ? savedSwap[client->majorOp] : savedProc[client->majorOp];
    if (!fn)
        return ProcBadRequest(client);

    if (flightRing) {
        slot = flightRing->w & (LORIE_FLIGHT_CAP - 1);
        e = &flightRing->ents[slot];
        e->seq = (uint32_t) client->sequence;
        e->req_len = client->req_len;
        e->minor = client->minorOp;
        e->client = (uint16_t) client->index;
        e->major = client->majorOp;
        e->phase = 1;
        e->result = 0;
        memset(e->head, 0, sizeof(e->head));
        n = client->req_len * 4;
        if (n > sizeof(e->head))
            n = sizeof(e->head);
        if (n && client->requestBuffer)
            memcpy(e->head, client->requestBuffer, n);
        __sync_synchronize();
        flightRing->w = flightRing->w + 1;
    }

    rc = fn(client);

    if (flightRing) {
        e->result = (int16_t) rc;
        e->phase = 2;
        __sync_synchronize();
    }
    return rc;
}

void lorieDumpFlightRecorder(const char *why) {
    uint32_t w, i, n, start;
    char line[256];
    int len;

    if (!flightRing) {
        log(ERROR, "flight dump (%s): ring missing", why ? why : "?");
        return;
    }
    w = flightRing->w;
    n = w < LORIE_FLIGHT_CAP ? w : LORIE_FLIGHT_CAP;
    start = w - n;
    log(ERROR, "flight dump why=%s path=%s w=%u showing=%u (oldest→newest; phase1=in-handler)",
        why ? why : "?", flightPath, w, n);
    if (dumpFd >= 0) {
        dprintf(dumpFd, "why=%s w=%u n=%u\n", why ? why : "?", w, n);
        dprintf(dumpFd, "# idx seq client major minor len phase result name head\n");
    }
    for (i = 0; i < n; i++) {
        LorieFlightEnt *e = &flightRing->ents[(start + i) & (LORIE_FLIGHT_CAP - 1)];
        const char *name = LookupMajorName(e->major);
        int last = (i + 1 == n);
        log(ERROR,
            "flight %s%03u seq=%u cli=%u op=%u.%u len=%u ph=%u r=%d %s head=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x",
            last ? "LAST " : "", i, e->seq, e->client, e->major, e->minor, e->req_len,
            e->phase, e->result, name ? name : "?",
            e->head[0], e->head[1], e->head[2], e->head[3], e->head[4], e->head[5],
            e->head[6], e->head[7], e->head[8], e->head[9], e->head[10], e->head[11],
            e->head[12], e->head[13], e->head[14], e->head[15]);
        if (dumpFd >= 0) {
            len = snprintf(line, sizeof(line),
                           "%s%u %u %u %u %u %u %u %d %s %02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
                           last ? "LAST " : "", i, e->seq, e->client, e->major, e->minor,
                           e->req_len, e->phase, e->result, name ? name : "?",
                           e->head[0], e->head[1], e->head[2], e->head[3], e->head[4], e->head[5],
                           e->head[6], e->head[7], e->head[8], e->head[9], e->head[10], e->head[11],
                           e->head[12], e->head[13], e->head[14], e->head[15]);
            if (len > 0)
                write(dumpFd, line, (size_t) len);
        }
    }
    if (dumpFd >= 0)
        fsync(dumpFd);
    msync(flightRing, sizeof(*flightRing), MS_SYNC);
}

void lorieInstallFlightRecorder(void) {
    int i;

    lorieFlightOpenRing();
    for (i = 0; i < 256; i++) {
        if (ProcVector[i] && ProcVector[i] != lorieFlightProc) {
            savedProc[i] = ProcVector[i];
            ProcVector[i] = lorieFlightProc;
        }
        if (SwappedProcVector[i] && SwappedProcVector[i] != lorieFlightProc) {
            savedSwap[i] = SwappedProcVector[i];
            SwappedProcVector[i] = lorieFlightProc;
        }
    }
}
