/* Host test for lorieGateAClassifyWaitWake.
 *
 * Enums must match lorie.h. verify_r7_10_hup_containment.py proves that
 * binding before this binary is treated as PASS.
 *
 * usage: test_gatea_wait_wake_class
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

#include "../lorie/src/main/cpp/lorie/lorie_gatea_wait_wake_class.h"
#include "../lorie/src/main/cpp/lorie/lorie_gatea_done_class.h"

int main(void) {
    uint32_t reason;

    /* R7-10: renderer exits while direct wait is active. Peer HUP with no
     * published fatal is x-hup/6, never reason=4. */
    reason = 0xdeadu;
    assert(lorieGateAClassifyWaitWake(0, 1, 0, 0, &reason)
           == LORIE_GATEA_WAIT_WAKE_X_HUP);
    assert(reason == LORIE_GATEA_FAIL_GENERATION);
    assert(reason != LORIE_GATEA_FAIL_TIMEOUT);
    assert(lorieGateAClassifyWaitWake(0, 1, 1, 0, &reason)
           == LORIE_GATEA_WAIT_WAKE_X_HUP);
    assert(reason == LORIE_GATEA_FAIL_GENERATION);

    /* Published renderer fatal then peer closes: preserve, no x-hup. */
    assert(lorieGateAClassifyWaitWake(0, 1, 0, LORIE_GATEA_FAIL_DRAW, &reason)
           == LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL);
    assert(reason == LORIE_GATEA_FAIL_DRAW);
    assert(reason != LORIE_GATEA_FAIL_GENERATION);
    assert(lorieGateAClassifyWaitWake(0, 1, 0, LORIE_GATEA_FAIL_FENCE, &reason)
           == LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL);
    assert(reason == LORIE_GATEA_FAIL_FENCE);

    /* Live peer, no completion: continue until timeout. */
    assert(lorieGateAClassifyWaitWake(1, 1, 0, 0, &reason)
           == LORIE_GATEA_WAIT_WAKE_CONTINUE);
    assert(reason == 0);

    /* Genuine timeout: live connection, budget expired, no published fatal.
     * Must remain FAIL_TIMEOUT so real timeouts are not relabeled HUP. */
    assert(lorieGateAClassifyWaitWake(1, 1, 1, 0, &reason)
           == LORIE_GATEA_WAIT_WAKE_TIMEOUT);
    assert(reason == LORIE_GATEA_FAIL_TIMEOUT);
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_FATAL, 0, 0,
                                        &reason) == LORIE_GATEA_DONE_TIMEOUT);
    assert(reason == LORIE_GATEA_FAIL_TIMEOUT);

    /* Surface gone without socket HUP is not x-hup. */
    assert(lorieGateAClassifyWaitWake(1, 0, 0, 0, &reason)
           == LORIE_GATEA_WAIT_WAKE_SURFACE_LOSS);
    assert(reason == 0);

    /* Derive SUCCESS is decided before wait-wake. If a caller wrongly
     * consulted wait-wake after SUCCESS, peer death would still classify
     * as HUP; production must return SUCCESS first. */
    assert(lorieGateAClassifyDirectDone(LORIE_GATEA_RESULT_SUCCESS, 0, 0,
                                        &reason) == LORIE_GATEA_DONE_SUCCESS);
    assert(lorieGateAClassifyWaitWake(0, 1, 0, 0, &reason)
           != LORIE_GATEA_WAIT_WAKE_CONTINUE);

    puts("GATEA_WAIT_WAKE_CLASS=PASS");
    return 0;
}
