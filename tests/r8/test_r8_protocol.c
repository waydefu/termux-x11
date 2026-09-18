/* Host I02: freeze R8 test-protocol sizes, sizeof==sz_*, swapped CARD32. */
#include "r8-test-protocol.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static CARD32 bswap32(CARD32 v) {
    return (CARD32)(((v & 0xff000000u) >> 24) | ((v & 0x00ff0000u) >> 8) |
                    ((v & 0x0000ff00u) << 8) | ((v & 0x000000ffu) << 24));
}

int main(void) {
    xLorieR8CheckpointReply ck;
    xLorieR8QueryVersionReply qv;
    CARD32 gen_lo, gen_hi;

    assert(sz_xLorieR8QueryVersionReq == 4);
    assert(sz_xLorieR8QueryVersionReply == 32);
    assert(sz_xLorieR8RegisterBufferReq == 8);
    assert(sz_xLorieR8RegisterBufferReply == 72);
    assert(sz_xLorieR8CheckpointReq == 8);
    assert(sz_xLorieR8CheckpointReply == 72);
    assert(sz_xLorieR8TerminateReq == 4);
    assert(sz_xLorieR8TerminateReply == 32);
    assert(sizeof(xLorieR8QueryVersionReq) == sz_xLorieR8QueryVersionReq);
    assert(sizeof(xLorieR8QueryVersionReply) == sz_xLorieR8QueryVersionReply);
    assert(sizeof(xLorieR8RegisterBufferReq) == sz_xLorieR8RegisterBufferReq);
    assert(sizeof(xLorieR8RegisterBufferReply) == sz_xLorieR8RegisterBufferReply);
    assert(sizeof(xLorieR8CheckpointReq) == sz_xLorieR8CheckpointReq);
    assert(sizeof(xLorieR8CheckpointReply) == sz_xLorieR8CheckpointReply);
    assert(sizeof(xLorieR8TerminateReq) == sz_xLorieR8TerminateReq);
    assert(sizeof(xLorieR8TerminateReply) == sz_xLorieR8TerminateReply);
    assert(X_LorieR8QueryVersion == 0);
    assert(X_LorieR8RegisterBuffer == 1);
    assert(X_LorieR8Checkpoint == 2);
    assert(X_LorieR8Terminate == 3);
    assert(X_LorieR8LastRequest == X_LorieR8Terminate);
    assert(lorieR8CaseCode("R8-C1") == LORIE_R8_CASE_C1);
    assert(lorieR8CaseCode("R8-C3-window") == LORIE_R8_CASE_C3_WINDOW);
    assert(lorieR8CaseCode("nope") == 0);
    assert(lorieR8CaseAllowsRegister(LORIE_R8_CASE_C4));
    assert(!lorieR8CaseAllowsRegister(LORIE_R8_CASE_C1));
    assert(lorieR8PhaseValid(LORIE_R8_PHASE_BEGIN));
    assert(!lorieR8PhaseValid(0));
    assert(!lorieR8PhaseValid(99));
    assert((sizeof(xLorieR8QueryVersionReply) - 32) / 4 == 0);
    assert((sizeof(xLorieR8RegisterBufferReply) - 32) / 4 == 10);
    assert((sizeof(xLorieR8CheckpointReply) - 32) / 4 == 10);
    assert((sizeof(xLorieR8TerminateReply) - 32) / 4 == 0);

    memset(&qv, 0, sizeof(qv));
    qv.sequenceNumber = 0x1122;
    qv.length = 0;
    qv.majorVersion = 1;
    qv.minorVersion = 1;
    qv.caseCode = 0x01020304u;
    qv.occupancy = 0xaabbccddu;
    qv.sequenceNumber = (CARD16)((qv.sequenceNumber >> 8) | (qv.sequenceNumber << 8));
    qv.majorVersion = (CARD16)((qv.majorVersion >> 8) | (qv.majorVersion << 8));
    qv.minorVersion = (CARD16)((qv.minorVersion >> 8) | (qv.minorVersion << 8));
    qv.caseCode = bswap32(qv.caseCode);
    qv.occupancy = bswap32(qv.occupancy);
    assert(qv.sequenceNumber == 0x2211);
    assert(qv.majorVersion == 0x0100);
    assert(qv.minorVersion == 0x0100);
    assert(qv.caseCode == 0x04030201u);
    assert(qv.occupancy == 0xddccbbaau);
    assert(qv.pad0 == 0 && qv.pad1 == 0 && qv.pad2 == 0);

    memset(&ck, 0, sizeof(ck));
    ck.generationLo = 0x11223344u;
    ck.generationHi = 0x55667788u;
    gen_lo = ck.generationLo;
    gen_hi = ck.generationHi;
    ck.phase = bswap32(1);
    ck.occupancy = bswap32(2);
    ck.registryCount = bswap32(3);
    ck.pairState = bswap32(4);
    ck.rootPending = bswap32(5);
    ck.readIndex = bswap32(6);
    ck.writeIndex = bswap32(7);
    ck.completedLo = bswap32(8);
    ck.completedHi = bswap32(9);
    ck.firstFailedLo = bswap32(10);
    ck.firstFailedHi = bswap32(11);
    ck.generationFatal = bswap32(12);
    ck.nonceLo = bswap32(13);
    ck.nonceHi = bswap32(14);
    ck.generationLo = bswap32(gen_lo);
    ck.generationHi = bswap32(gen_hi);
    assert(ck.generationLo == 0x44332211u);
    assert(ck.generationHi == 0x88776655u);
    assert(sizeof(ck) == 72);

    printf("PASS test_r8_protocol\n");
    return 0;
}
