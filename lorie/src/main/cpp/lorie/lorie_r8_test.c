#ifdef LORIE_ENABLE_R8_TEST_SUPPORT

#ifdef HAVE_DIX_CONFIG_H
#include <dix-config.h>
#endif

#include <X11/X.h>
#include <X11/Xproto.h>
#include "r8-test-protocol.h"
#include "lorie_r8_obs.h"
#include "lorie_r8_test.h"
#include "lorie.h"

#include <X11/X.h>
#include <X11/Xproto.h>
#include "dix.h"
#include "dixstruct.h"
#include "extnsionst.h"
#include "pixmapstr.h"
#include "scrnintstr.h"
#include "windowstr.h"
#include "resource.h"
#include "os.h"
#include "lorie_r8_test_x.h"
#include <string.h>
#include <stdio.h>

extern ScreenPtr pScreenPtr;

static int r8RegisterBudget;
static int r8CheckpointBudget;
static int r8ExtReady;
static int r8MajorOpcode;

static int r8WindowUsesPixmap(WindowPtr w, void *data) {
    if (w && w->drawable.pScreen &&
        w->drawable.pScreen->GetWindowPixmap(w) == data)
        return WT_STOPWALKING;
    return WT_WALKCHILDREN;
}

static int r8IsWindowBacking(PixmapPtr pixmap) {
    if (!pScreenPtr || !pixmap)
        return 1;
    if (pScreenPtr->GetScreenPixmap &&
        pScreenPtr->GetScreenPixmap(pScreenPtr) == pixmap)
        return 1;
    if (pScreenPtr->devPrivate == pixmap)
        return 1;
    if (pScreenPtr->root &&
        WalkTree(pScreenPtr, r8WindowUsesPixmap, pixmap) == WT_STOPWALKING)
        return 1;
    return 0;
}

static void r8Split64(uint64_t v, CARD32 *lo, CARD32 *hi) {
    *lo = (CARD32)(v & 0xffffffffu);
    *hi = (CARD32)(v >> 32);
}

static void r8EmitRegistryRows(void) {
    struct LorieGateABufferMeta rows[16];
    int n, i;
    char fields[384];

    n = lorieGateAR8CopyRegistry(rows, 16);
    if (n < 0)
        n = 0;
    for (i = 0; i < n; i++) {
        int32_t actualPending = -1;
        snprintf(fields, sizeof(fields),
                 "\"bufferId\":%llu,\"fingerprint\":%llu,\"state\":%u,"
                 "\"lastSubmittedSerial\":%llu,\"pendingCount\":%u,"
                 "\"cpuLocked\":%u,\"actual_buffer_pending\":%s",
                 (unsigned long long)rows[i].bufferId,
                 (unsigned long long)rows[i].fingerprint,
                 (unsigned)rows[i].state,
                 (unsigned long long)rows[i].lastSubmittedSerial,
                 (unsigned)rows[i].pendingCount,
                 (unsigned)rows[i].cpuLocked,
                 actualPending < 0 ? "null" : "0");
        /* actual pending is filled by checkpoint using owner buffers; row
         * pendingCount is registry metadata and must not be silently used
         * as live GPU pending. */
        lorieR8Obs("x", "X_REG_ROW", fields);
    }
}

static int ProcLorieR8QueryVersion(ClientPtr client) {
    xLorieR8QueryVersionReply rep;
    struct LorieGateAProtocol *shared;
    uint64_t nonce = 0, gen = 0;
    struct LorieGateABufferMeta rows[16];
    int occ;

    REQUEST_SIZE_MATCH(xLorieR8QueryVersionReq);
    memset(&rep, 0, sizeof(rep));
    rep.type = X_Reply;
    rep.sequenceNumber = client->sequence;
    rep.length = 0;
    rep.majorVersion = LORIE_R8_TEST_MAJOR_VERSION;
    rep.minorVersion = LORIE_R8_TEST_MINOR_VERSION;
    rep.caseCode = lorieR8CaseCodeCached();
    occ = lorieGateAR8CopyRegistry(rows, 16);
    rep.occupancy = occ < 0 ? 0 : (CARD32)occ;
    shared = lorieGateAShared();
    if (shared) {
        nonce = lorieGateALoadU64Acquire(&shared->sessionNonce);
        gen = lorieGateALoadU64Acquire(&shared->generation);
        lorieR8BindTuple(nonce, gen);
    }
    lorieR8Obs("x", "TEST_CONTROL",
               "\"op\":\"QUERY_VERSION\"");
    if (client->swapped) {
        swaps(&rep.sequenceNumber);
        swapl(&rep.length);
        swaps(&rep.majorVersion);
        swaps(&rep.minorVersion);
        swapl(&rep.caseCode);
        swapl(&rep.occupancy);
    }
    WriteToClient(client, sizeof(rep), &rep);
    return Success;
}

static int ProcLorieR8RegisterBuffer(ClientPtr client) {
    REQUEST(xLorieR8RegisterBufferReq);
    PixmapPtr pixmap = NULL;
    LorieBuffer *buf = NULL;
    const LorieBuffer_Desc *desc = NULL;
    struct LorieGateABufferMeta meta;
    struct LorieGateAProtocol *shared;
    xLorieR8RegisterBufferReply rep;
    int rc, occ, privRefuse;
    uint32_t refuse = LORIE_R8_REFUSE_NONE;
    Bool accepted = FALSE;
    char fields[512];
    struct LorieGateABufferMeta rows[16];

    REQUEST_SIZE_MATCH(xLorieR8RegisterBufferReq);
    memset(&rep, 0, sizeof(rep));
    rep.type = X_Reply;
    rep.sequenceNumber = client->sequence;
    rep.length = (sz_xLorieR8RegisterBufferReply - 32) / 4;
    rep.xid = stuff->xid;

    if (!lorieR8CaseAllowsRegister(lorieR8CaseCodeCached()))
        refuse = LORIE_R8_REFUSE_WRONG_CASE;
    else if (r8RegisterBudget <= 0)
        refuse = LORIE_R8_REFUSE_BUDGET;
    else {
        rc = dixLookupResourceByType((void **)&pixmap, stuff->xid, RT_PIXMAP,
                                     client, DixWriteAccess);
        if (rc != Success || pixmap == NULL)
            refuse = LORIE_R8_REFUSE_BAD_XID;
        else if (CLIENT_ID(pixmap->drawable.id) != client->index)
            refuse = LORIE_R8_REFUSE_NOT_OWNED;
        else {
            privRefuse = lorieGateAR8PixmapReject(pixmap);
            if (privRefuse)
                refuse = (uint32_t)privRefuse;
            else if (r8IsWindowBacking(pixmap))
                refuse = LORIE_R8_REFUSE_ROOT_OR_WINDOW;
            else if (pixmap->drawable.depth != 24 && pixmap->drawable.depth != 32)
                refuse = LORIE_R8_REFUSE_BAD_FORMAT;
            else {
                buf = lorieGateAR8EnsureGpuSampleableAhb(pixmap);
                if (!buf)
                    refuse = LORIE_R8_REFUSE_NOT_SAMPLEABLE;
                else if (!lorieGateAR8EnsureReadyForBuffer(buf))
                    refuse = LORIE_R8_REFUSE_READY_FALSE;
                else {
                    desc = LorieBuffer_description(buf);
                    accepted = TRUE;
                    r8RegisterBudget--;
                    if (desc) {
                        r8Split64(desc->id, &rep.bufferIdLo, &rep.bufferIdHi);
                        r8Split64(lorieGateAFingerprint((uint32_t)desc->width,
                                                        (uint32_t)desc->height,
                                                        (uint32_t)desc->stride,
                                                        (uint32_t)desc->format),
                                  &rep.fingerprintLo, &rep.fingerprintHi);
                    }
                    if (lorieGateARegistryFind(desc ? desc->id : 0, &meta) == 0) {
                        rep.state = meta.state;
                        r8Split64(meta.lastSubmittedSerial, &rep.lastSerialLo,
                                  &rep.lastSerialHi);
                        rep.pendingCount = meta.pendingCount;
                    }
                }
            }
        }
    }

    shared = lorieGateAShared();
    if (shared) {
        r8Split64(lorieGateALoadU64Acquire(&shared->sessionNonce),
                  &rep.nonceLo, &rep.nonceHi);
        r8Split64(lorieGateALoadU64Acquire(&shared->generation),
                  &rep.generationLo, &rep.generationHi);
        lorieR8BindTuple(lorieGateALoadU64Acquire(&shared->sessionNonce),
                         lorieGateALoadU64Acquire(&shared->generation));
    }
    occ = lorieGateAR8CopyRegistry(rows, 16);
    rep.occupancy = occ < 0 ? 0 : (CARD32)occ;
    rep.accepted = accepted ? 1 : 0;
    rep.refuseCode = refuse;
    snprintf(fields, sizeof(fields),
             "\"op\":\"REGISTER_BUFFER\",\"xid\":%u,\"accepted\":%s,\"refuse\":%u,"
             "\"occupancy\":%u",
             (unsigned)stuff->xid, accepted ? "true" : "false",
             (unsigned)refuse, (unsigned)rep.occupancy);
    lorieR8Obs("x", "TEST_CONTROL", fields);
    if (client->swapped) {
        swaps(&rep.sequenceNumber);
        swapl(&rep.length);
        swapl(&rep.xid);
        swapl(&rep.occupancy);
        swapl(&rep.state);
        swapl(&rep.refuseCode);
        swapl(&rep.pendingCount);
        swapl(&rep.lastSerialLo);
        swapl(&rep.lastSerialHi);
        swapl(&rep.bufferIdLo);
        swapl(&rep.bufferIdHi);
        swapl(&rep.nonceLo);
        swapl(&rep.nonceHi);
        swapl(&rep.generationLo);
        swapl(&rep.generationHi);
        swapl(&rep.fingerprintLo);
        swapl(&rep.fingerprintHi);
    }
    WriteToClient(client, sizeof(rep), &rep);
    return Success;
}

static int ProcLorieR8Checkpoint(ClientPtr client) {
    REQUEST(xLorieR8CheckpointReq);
    xLorieR8CheckpointReply rep;
    struct LorieGateAProtocol *shared;
    struct lorie_shared_server_state *st;
    struct LorieGateABufferMeta rows[16];
    int occ, pairState = 0;
    uint64_t srcId = 0, dstId = 0, nonce = 0, gen = 0;
    char fields[768];

    REQUEST_SIZE_MATCH(xLorieR8CheckpointReq);
    if (!lorieR8PhaseValid(stuff->phase))
        return BadValue;
    if (r8CheckpointBudget <= 0)
        return BadAccess;
    r8CheckpointBudget--;

    memset(&rep, 0, sizeof(rep));
    rep.type = X_Reply;
    rep.sequenceNumber = client->sequence;
    rep.length = (sz_xLorieR8CheckpointReply - 32) / 4;
    rep.phase = stuff->phase;
    occ = lorieGateAR8CopyRegistry(rows, 16);
    if (occ < 0)
        occ = 0;
    lorieGateAR8PairSnapshot(&pairState, &srcId, &dstId, &nonce, &gen);
    shared = lorieGateAShared();
    st = lorieGateASharedState();
    rep.occupancy = (CARD32)occ;
    rep.registryCount = (CARD32)occ;
    rep.pairState = (CARD32)pairState;
    rep.rootPending = (CARD32)lorieGateAR8RootPending();
    if (st) {
        rep.readIndex = lorieGateAObserveReadIndex(&st->gpuCopyQueue.readIndex);
        rep.writeIndex = lorieGateAObserveWriteIndex(&st->gpuCopyQueue.writeIndex);
        r8Split64(lorieGateAObserveCompleted(&st->gpuCopyQueue.completedSerial),
                  &rep.completedLo, &rep.completedHi);
        r8Split64(lorieGateAObserveFirstFailed(&st->gateA),
                  &rep.firstFailedLo, &rep.firstFailedHi);
        rep.generationFatal = lorieGateAObserveFatal(&st->gateA);
    }
    if (shared) {
        r8Split64(lorieGateALoadU64Acquire(&shared->sessionNonce),
                  &rep.nonceLo, &rep.nonceHi);
        r8Split64(lorieGateALoadU64Acquire(&shared->generation),
                  &rep.generationLo, &rep.generationHi);
        lorieR8BindTuple(lorieGateALoadU64Acquire(&shared->sessionNonce),
                         lorieGateALoadU64Acquire(&shared->generation));
    }
    /* Observation only: no pump, retire, dispatch, or ownership change. */
    snprintf(fields, sizeof(fields),
             "\"registry_count\":%u,\"total_actual_buffer_pending\":null,"
             "\"root_pending\":%u,\"pair_state\":%u,\"pair_src\":%llu,"
             "\"pair_dst\":%llu,\"readIndex\":%u,\"writeIndex\":%u,"
             "\"completedSerial\":%llu,\"firstFailed\":%llu,"
             "\"generationFatal\":%u,\"phase\":%u",
             (unsigned)rep.registryCount, (unsigned)rep.rootPending,
             (unsigned)rep.pairState, (unsigned long long)srcId,
             (unsigned long long)dstId, (unsigned)rep.readIndex,
             (unsigned)rep.writeIndex,
             (unsigned long long)(((uint64_t)rep.completedHi << 32) | rep.completedLo),
             (unsigned long long)(((uint64_t)rep.firstFailedHi << 32) | rep.firstFailedLo),
             (unsigned)rep.generationFatal, (unsigned)stuff->phase);
    lorieR8Obs("x", "X_CHECKPOINT", fields);
    r8EmitRegistryRows();
    lorieR8Obs("x", "TEST_CONTROL", "\"op\":\"CHECKPOINT\"");
    if (client->swapped) {
        swaps(&rep.sequenceNumber);
        swapl(&rep.length);
        swapl(&rep.phase);
        swapl(&rep.occupancy);
        swapl(&rep.registryCount);
        swapl(&rep.pairState);
        swapl(&rep.rootPending);
        swapl(&rep.readIndex);
        swapl(&rep.writeIndex);
        swapl(&rep.completedLo);
        swapl(&rep.completedHi);
        swapl(&rep.firstFailedLo);
        swapl(&rep.firstFailedHi);
        swapl(&rep.generationFatal);
        swapl(&rep.nonceLo);
        swapl(&rep.nonceHi);
        swapl(&rep.generationLo);
        swapl(&rep.generationHi);
    }
    WriteToClient(client, sizeof(rep), &rep);
    return Success;
}

static int ProcLorieR8Terminate(ClientPtr client) {
    xLorieR8TerminateReply rep;

    REQUEST_SIZE_MATCH(xLorieR8TerminateReq);
    memset(&rep, 0, sizeof(rep));
    rep.type = X_Reply;
    rep.sequenceNumber = client->sequence;
    rep.length = 0;
    lorieR8Obs("x", "TEST_CONTROL", "\"op\":\"TERMINATE\"");
    if (client->swapped) {
        swaps(&rep.sequenceNumber);
        swapl(&rep.length);
    }
    WriteToClient(client, sizeof(rep), &rep);
    /* D-02: this request IS the whole-run finalization authority. Publish it for
     * the renderer BEFORE GiveUp(0), so the renderer's process-level END is gated
     * on an explicit end-of-run and not on first-generation quiescence.
     * Ordering is guaranteed, not assumed: GiveUp only sets DE_TERMINATE, and X
     * then unwinds through lorieCloseScreen -> gateACloseGeneration(), which
     * BLOCKS in gateAWaitCleanAck() until the renderer sends GENERATION_CLOSED
     * (InitOutput.c gateACloseGeneration). The renderer therefore runs, and reads
     * this word, before X is gone. X's own END still comes from ddxGiveUp. */
    lorieGateAPublishRunFinalize(lorieGateASharedState());
    /* Same DE_TERMINATE flag as os/utils.c GiveUp. ddxGiveUp still emits END. */
    GiveUp(0);
    return Success;
}

static int ProcLorieR8Dispatch(ClientPtr client) {
    REQUEST(xReq);
    if (!r8ExtReady || !lorieR8Armed())
        return BadRequest;
    switch (stuff->data) {
    case X_LorieR8QueryVersion:
        return ProcLorieR8QueryVersion(client);
    case X_LorieR8RegisterBuffer:
        return ProcLorieR8RegisterBuffer(client);
    case X_LorieR8Checkpoint:
        return ProcLorieR8Checkpoint(client);
    case X_LorieR8Terminate:
        return ProcLorieR8Terminate(client);
    default:
        return BadRequest;
    }
}

static int SProcLorieR8Dispatch(ClientPtr client) {
    REQUEST(xReq);
    swaps(&stuff->length);
    switch (stuff->data) {
    case X_LorieR8QueryVersion:
        break;
    case X_LorieR8RegisterBuffer: {
        REQUEST(xLorieR8RegisterBufferReq);
        swapl(&stuff->xid);
        break;
    }
    case X_LorieR8Checkpoint: {
        REQUEST(xLorieR8CheckpointReq);
        swapl(&stuff->phase);
        break;
    }
    case X_LorieR8Terminate:
        break;
    default:
        return BadRequest;
    }
    return ProcLorieR8Dispatch(client);
}

void LorieR8TestExtensionInit(void) {
    ExtensionEntry *ext;
    uint32_t code;
    int v;

    if (r8ExtReady)
        return;
    v = lorieR8ValidateStartupEnv(lorieGateAProtoEnabled(),
                                  lorieGateATelemetryRequested());
    if (v < 0)
        lorieGateAFatalHalt("x-r8-env", LORIE_GATEA_FAIL_PROTOCOL);
    if (v != 1)
        return;
    if (!pScreenPtr)
        return;
    code = lorieR8CaseCodeCached();
    if (code == LORIE_R8_CASE_C5_FULL || code == LORIE_R8_CASE_C5_OVERFLOW)
        r8RegisterBudget = 16;
    else if (code == LORIE_R8_CASE_C4)
        r8RegisterBudget = 2;
    else
        r8RegisterBudget = 0;
    r8CheckpointBudget = 16;
    ext = AddExtension(LORIE_R8_TEST_NAME, 0, 0,
                       ProcLorieR8Dispatch, SProcLorieR8Dispatch,
                       NULL, StandardMinorOpcode);
    if (!ext)
        lorieGateAFatalHalt("x-r8-ext", LORIE_GATEA_FAIL_PROTOCOL);
    r8MajorOpcode = ext->base;
    r8ExtReady = 1;
    lorieR8ObsBegin("x");
    lorieR8Obs("x", "TEST_CONTROL", "\"op\":\"EXT_INIT\"");
}

#endif /* LORIE_ENABLE_R8_TEST_SUPPORT */
