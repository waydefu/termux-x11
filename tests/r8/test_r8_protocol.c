/* Host I02: freeze R8 test-protocol sizes and swapped field widths. */
#include "r8-test-protocol.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    assert(sz_xLorieR8QueryVersionReq == 4);
    assert(sz_xLorieR8QueryVersionReply == 32);
    assert(sz_xLorieR8RegisterBufferReq == 8);
    assert(sz_xLorieR8RegisterBufferReply == 72);
    assert(sz_xLorieR8CheckpointReq == 8);
    assert(sz_xLorieR8CheckpointReply == 64);
    assert(X_LorieR8QueryVersion == 0);
    assert(X_LorieR8RegisterBuffer == 1);
    assert(X_LorieR8Checkpoint == 2);
    assert(lorieR8CaseCode("R8-C1") == LORIE_R8_CASE_C1);
    assert(lorieR8CaseCode("R8-C3-window") == LORIE_R8_CASE_C3_WINDOW);
    assert(lorieR8CaseCode("nope") == 0);
    assert(lorieR8CaseAllowsRegister(LORIE_R8_CASE_C4));
    assert(!lorieR8CaseAllowsRegister(LORIE_R8_CASE_C1));
    assert(lorieR8PhaseValid(LORIE_R8_PHASE_BEGIN));
    assert(!lorieR8PhaseValid(0));
    assert(!lorieR8PhaseValid(99));
    printf("PASS test_r8_protocol\n");
    return 0;
}
