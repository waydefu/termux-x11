/* Host test for lorieGateAClassifyDirectDone.
 *
 * Enums must match lorie.h. verify_r7_fatal_propagation.py proves that
 * binding before this binary is treated as PASS.
 *
 * usage: test_gatea_direct_done_class
 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

typedef enum {
    LORIE_GATEA_RESULT_NONE = 0,
    LORIE_GATEA_RESULT_SUCCESS = 1,
    LORIE_GATEA_RESULT_FAILED_QUIESCED = 2,
    LORIE_GATEA_RESULT_FATAL = 3,
} LorieGateAResult;

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

#include "../lorie/src/main/cpp/lorie/lorie_gatea_done_class.h"

int main(void) {
    uint32_t reason;

    /* Case A — success remains available. */
    reason = 0xdeadu;
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_SUCCESS, 0, 0,
                                        &reason) == LORIE_GATEA_DONE_SUCCESS);
    assert(reason == 0);
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_SUCCESS,
                                        LORIE_GATEA_FAIL_DRAW, 0,
                                        &reason) == LORIE_GATEA_DONE_SUCCESS);

    /* Case B — authoritative fatal reason=2 is preserved, not timeout. */
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FATAL,
                                        LORIE_GATEA_FAIL_DRAW, 0,
                                        &reason) == LORIE_GATEA_DONE_PRESERVE_FATAL);
    assert(reason == LORIE_GATEA_FAIL_DRAW);
    assert(reason != LORIE_GATEA_FAIL_TIMEOUT);

    /* Case C — genuine timeout / no published fatal stays reason=4. */
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FATAL, 0, 0,
                                        &reason) == LORIE_GATEA_DONE_TIMEOUT);
    assert(reason == LORIE_GATEA_FAIL_TIMEOUT);
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_NONE, 0, 0,
                                        &reason) == LORIE_GATEA_DONE_TIMEOUT);
    assert(reason == LORIE_GATEA_FAIL_TIMEOUT);

    /* Case D — published fatal wins over a leftover timeout code word. */
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FATAL,
                                        LORIE_GATEA_FAIL_DRAW,
                                        LORIE_GATEA_FAIL_TIMEOUT,
                                        &reason) == LORIE_GATEA_DONE_PRESERVE_FATAL);
    assert(reason == LORIE_GATEA_FAIL_DRAW);

    /* QUIESCED keeps firstFailureCode; missing code falls back to timeout. */
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FAILED_QUIESCED,
                                        0, LORIE_GATEA_FAIL_DRAW,
                                        &reason) == LORIE_GATEA_DONE_QUIESCED);
    assert(reason == LORIE_GATEA_FAIL_DRAW);
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FAILED_QUIESCED,
                                        0, 0,
                                        &reason) == LORIE_GATEA_DONE_QUIESCED);
    assert(reason == LORIE_GATEA_FAIL_TIMEOUT);

    /* FATAL must never classify as SUCCESS. */
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FATAL,
                                        LORIE_GATEA_FAIL_DRAW, 0,
                                        &reason) != LORIE_GATEA_DONE_SUCCESS);
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FATAL, 0, 0,
                                        &reason) != LORIE_GATEA_DONE_SUCCESS);
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FAILED_QUIESCED,
                                        0, LORIE_GATEA_FAIL_DRAW,
                                        &reason) != LORIE_GATEA_DONE_SUCCESS);

    puts("GATEA_DIRECT_DONE_CLASS=PASS");
    return 0;
}
