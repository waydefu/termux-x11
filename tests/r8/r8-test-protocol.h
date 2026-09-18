/* SPDX-License-Identifier: MIT
 * LORIE-R8-TEST protocol v1. Test-only. Not Gate A wire/shared ABI.
 * Frozen request/reply layouts, opcodes, and byte lengths.
 */
#ifndef LORIE_R8_TEST_PROTOCOL_H
#define LORIE_R8_TEST_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define LORIE_R8_TEST_NAME "LORIE-R8-TEST"
#define LORIE_R8_TEST_MAJOR_VERSION 1
#define LORIE_R8_TEST_MINOR_VERSION 0

#define X_LorieR8QueryVersion 0
#define X_LorieR8RegisterBuffer 1
#define X_LorieR8Checkpoint 2
#define X_LorieR8LastRequest X_LorieR8Checkpoint

#define LORIE_R8_PHASE_INVALID 0
#define LORIE_R8_PHASE_BEGIN 1
#define LORIE_R8_PHASE_MID 2
#define LORIE_R8_PHASE_PRE_FREE 3
#define LORIE_R8_PHASE_POST_FREE 4
#define LORIE_R8_PHASE_PRE_TERM 5
#define LORIE_R8_PHASE_POST_REFUSAL 6
#define LORIE_R8_PHASE_POST_RECOVERY 7
#define LORIE_R8_PHASE_MAX 7

#define LORIE_R8_REFUSE_NONE 0
#define LORIE_R8_REFUSE_NOT_ARMED 1
#define LORIE_R8_REFUSE_BAD_XID 2
#define LORIE_R8_REFUSE_NOT_OWNED 3
#define LORIE_R8_REFUSE_NOT_OFFSCREEN 4
#define LORIE_R8_REFUSE_BAD_FORMAT 5
#define LORIE_R8_REFUSE_IMPORTED 6
#define LORIE_R8_REFUSE_ROOT_OR_WINDOW 7
#define LORIE_R8_REFUSE_NOT_SAMPLEABLE 8
#define LORIE_R8_REFUSE_READY_FALSE 9
#define LORIE_R8_REFUSE_WRONG_CASE 10
#define LORIE_R8_REFUSE_BUDGET 11

#define LORIE_R8_CASE_C1 1
#define LORIE_R8_CASE_C2 2
#define LORIE_R8_CASE_C3_WINDOW 3
#define LORIE_R8_CASE_C3_DISCONNECT 4
#define LORIE_R8_CASE_C4 5
#define LORIE_R8_CASE_C5_FULL 6
#define LORIE_R8_CASE_C5_OVERFLOW 7
#define LORIE_R8_CASE_D 8
#define LORIE_R8_CASE_P1 9
#define LORIE_R8_CASE_P2 10

#if defined(__X11_XMD_H) || defined(CARD8)
typedef struct {
    CARD8 reqType;
    CARD8 r8ReqType;
    CARD16 length; /* 1 */
} xLorieR8QueryVersionReq;

typedef struct {
    BYTE type;
    CARD8 unused;
    CARD16 sequenceNumber;
    CARD32 length; /* 0 */
    CARD16 majorVersion;
    CARD16 minorVersion;
    CARD32 caseCode;
    CARD32 occupancy;
    CARD32 pad0;
    CARD32 pad1;
    CARD32 pad2;
    CARD32 pad3;
} xLorieR8QueryVersionReply;

typedef struct {
    CARD8 reqType;
    CARD8 r8ReqType;
    CARD16 length; /* 2 */
    CARD32 xid;
} xLorieR8RegisterBufferReq;

typedef struct {
    BYTE type;
    CARD8 accepted;
    CARD16 sequenceNumber;
    CARD32 length; /* 10 extra dwords */
    CARD32 xid;
    CARD32 occupancy;
    CARD32 state;
    CARD32 refuseCode;
    CARD32 pendingCount;
    CARD32 lastSerialLo;
    CARD32 lastSerialHi;
    CARD32 bufferIdLo;
    CARD32 bufferIdHi;
    CARD32 nonceLo;
    CARD32 nonceHi;
    CARD32 generationLo;
    CARD32 generationHi;
    CARD32 fingerprintLo;
    CARD32 fingerprintHi;
    CARD32 pad;
} xLorieR8RegisterBufferReply;

typedef struct {
    CARD8 reqType;
    CARD8 r8ReqType;
    CARD16 length; /* 2 */
    CARD32 phase;
} xLorieR8CheckpointReq;

typedef struct {
    BYTE type;
    CARD8 unused;
    CARD16 sequenceNumber;
    CARD32 length; /* 8 extra dwords */
    CARD32 phase;
    CARD32 occupancy;
    CARD32 registryCount;
    CARD32 pairState;
    CARD32 rootPending;
    CARD32 readIndex;
    CARD32 writeIndex;
    CARD32 completedLo;
    CARD32 completedHi;
    CARD32 firstFailedLo;
    CARD32 firstFailedHi;
    CARD32 generationFatal;
    CARD32 nonceLo;
    CARD32 nonceHi;
    CARD32 generationLo;
    CARD32 generationHi;
} xLorieR8CheckpointReply;
#endif

#define sz_xLorieR8QueryVersionReq 4
#define sz_xLorieR8QueryVersionReply 32
#define sz_xLorieR8RegisterBufferReq 8
#define sz_xLorieR8RegisterBufferReply 72
#define sz_xLorieR8CheckpointReq 8
#define sz_xLorieR8CheckpointReply 64

static inline uint32_t lorieR8CaseCode(const char *name) {
    if (name == NULL)
        return 0;
    if (name[0] == 'R' && name[1] == '8' && name[2] == '-') {
        if (name[3] == 'C' && name[4] == '1' && name[5] == '\0')
            return LORIE_R8_CASE_C1;
        if (name[3] == 'C' && name[4] == '2' && name[5] == '\0')
            return LORIE_R8_CASE_C2;
        if (name[3] == 'C' && name[4] == '3' && name[5] == '-' &&
            name[6] == 'w')
            return LORIE_R8_CASE_C3_WINDOW;
        if (name[3] == 'C' && name[4] == '3' && name[5] == '-' &&
            name[6] == 'd')
            return LORIE_R8_CASE_C3_DISCONNECT;
        if (name[3] == 'C' && name[4] == '4' && name[5] == '\0')
            return LORIE_R8_CASE_C4;
        if (name[3] == 'C' && name[4] == '5' && name[5] == '-' &&
            name[6] == 'f')
            return LORIE_R8_CASE_C5_FULL;
        if (name[3] == 'C' && name[4] == '5' && name[5] == '-' &&
            name[6] == 'o')
            return LORIE_R8_CASE_C5_OVERFLOW;
        if (name[3] == 'D' && name[4] == '\0')
            return LORIE_R8_CASE_D;
        if (name[3] == 'P' && name[4] == '1' && name[5] == '\0')
            return LORIE_R8_CASE_P1;
        if (name[3] == 'P' && name[4] == '2' && name[5] == '\0')
            return LORIE_R8_CASE_P2;
    }
    return 0;
}

static inline int lorieR8CaseAllowsRegister(uint32_t code) {
    return code == LORIE_R8_CASE_C4 || code == LORIE_R8_CASE_C5_FULL
        || code == LORIE_R8_CASE_C5_OVERFLOW;
}

static inline int lorieR8PhaseValid(uint32_t phase) {
    return phase >= LORIE_R8_PHASE_BEGIN && phase <= LORIE_R8_PHASE_MAX;
}

#endif
