/* Host test for Present-target fault arming (cells 12/13).
 *
 * Enums and LorieGateATestFault layout must match lorie.h.
 * verify_r7_p1_present_target_arm.py binds this binary to production
 * headers. Does not change wait/timeout/judge/ABI.
 *
 * usage: test_gatea_test_fault_class
 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct LorieGateATestFault {
    uint32_t magic;
    uint32_t version;
    uint32_t cell;
    uint32_t armed;
    uint32_t consumed;
    uint32_t pad;
    uint64_t targetGeneration;
    uint64_t targetOrdinal;
};

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

#include "../lorie/src/main/cpp/lorie/lorie_gatea_test_fault_class.h"
#include "../lorie/src/main/cpp/lorie/lorie_gatea_wait_wake_class.h"

static void publish_cell(struct LorieGateATestFault *f, uint32_t cell) {
    memset(f, 0, sizeof(*f));
    f->magic = LORIE_GATEA_TEST_MAGIC;
    f->version = LORIE_GATEA_TEST_VERSION;
    f->cell = cell;
    f->consumed = 0;
    f->targetGeneration = 0;
    f->targetOrdinal = 0;
    f->armed = lorieGateATestFaultIsPresentBoundCell(cell) ? 0u : 1u;
}

int main(void) {
    struct LorieGateATestFault f;
    uint32_t reason;

    assert(sizeof(struct LorieGateATestFault) == 40);
    assert(LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE == 12);
    assert(LORIE_GATEA_TEST_PRESENT_RENDERER_EXIT == 13);

    /* OFF / unconfigured: consume never fires. */
    memset(&f, 0, sizeof(f));
    assert(!lorieGateATestFaultClassArmed(&f, 12));
    assert(!lorieGateATestFaultClassConsume(&f, 12, 1ull, 1ull));
    assert(f.consumed == 0);
    assert(!lorieGateATestFaultClassArmPresentTarget(&f, 7ull, 1ull));

    /* Early CopyArea: cell 12 configured, CopyArea serial=1, target not armed. */
    publish_cell(&f, 12);
    assert(f.armed == 0);
    assert(!lorieGateATestFaultClassArmed(&f, 12));
    assert(!lorieGateATestFaultClassConsume(&f, 12, 1ull, 1ull));
    assert(f.consumed == 0);
    assert(f.armed == 0);
    assert(f.targetOrdinal == 0);

    /* Armed=1 with wildcard targets still cannot consume Present-bound cells. */
    f.armed = 1;
    assert(!lorieGateATestFaultClassConsume(&f, 12, 1ull, 1ull));
    assert(f.consumed == 0);
    f.armed = 0;

    /* Arm refuses serial 0 / generation 0. */
    assert(!lorieGateATestFaultClassArmPresentTarget(&f, 0ull, 1ull));
    assert(!lorieGateATestFaultClassArmPresentTarget(&f, 7ull, 0ull));
    assert(f.armed == 0);

    /* Target Present established: serial 7 / generation 1. */
    assert(lorieGateATestFaultClassArmPresentTarget(&f, 7ull, 1ull) == 1);
    assert(f.armed == 1);
    assert(f.targetOrdinal == 7ull);
    assert(f.targetGeneration == 1ull);
    assert(lorieGateATestFaultClassArmed(&f, 12));

    /* Unrelated COPY after arm (different serial) must not consume. */
    assert(!lorieGateATestFaultClassConsume(&f, 12, 1ull, 1ull));
    assert(!lorieGateATestFaultClassConsume(&f, 12, 8ull, 1ull));
    assert(f.consumed == 0);

    /* Wrong generation must not consume. */
    assert(!lorieGateATestFaultClassConsume(&f, 12, 7ull, 2ull));
    assert(f.consumed == 0);

    /* Matching Present COPY consumes exactly once. */
    assert(lorieGateATestFaultClassConsume(&f, 12, 7ull, 1ull) == 1);
    assert(f.consumed == 1);

    /* Repeated target callback / consume must not fire again. */
    assert(!lorieGateATestFaultClassConsume(&f, 12, 7ull, 1ull));
    assert(lorieGateATestFaultClassArmPresentTarget(&f, 7ull, 1ull) == 0);
    assert(lorieGateATestFaultClassArmPresentTarget(&f, 9ull, 1ull) == 0);
    assert(f.consumed == 1);
    assert(f.targetOrdinal == 7ull);

    /* Idempotent same-target arm before consume keeps identity. */
    publish_cell(&f, 12);
    assert(lorieGateATestFaultClassArmPresentTarget(&f, 7ull, 1ull) == 1);
    assert(lorieGateATestFaultClassArmPresentTarget(&f, 7ull, 1ull) == 1);
    assert(f.targetOrdinal == 7ull);
    assert(!lorieGateATestFaultClassArmPresentTarget(&f, 8ull, 1ull));
    assert(f.targetOrdinal == 7ull);
    assert(f.consumed == 0);
    assert(lorieGateATestFaultClassConsume(&f, 12, 7ull, 1ull) == 1);

    /* Cell 13 shares Present-bound arming (P2 impact coverage; no P2 device). */
    publish_cell(&f, 13);
    assert(f.armed == 0);
    assert(!lorieGateATestFaultClassConsume(&f, 13, 1ull, 1ull));
    assert(lorieGateATestFaultClassArmPresentTarget(&f, 7ull, 1ull) == 1);
    assert(lorieGateATestFaultClassConsume(&f, 13, 7ull, 1ull) == 1);
    assert(!lorieGateATestFaultClassConsume(&f, 13, 7ull, 1ull));

    /* Non-Present cell 10 keeps startup wildcard consume (R7-10 unchanged). */
    publish_cell(&f, 10);
    assert(f.armed == 1);
    assert(!lorieGateATestFaultIsPresentBoundCell(10));
    assert(lorieGateATestFaultClassConsume(&f, 10, 1ull, 1ull) == 1);
    assert(!lorieGateATestFaultClassArmPresentTarget(&f, 7ull, 1ull));

    /* R7-10 wait-wake regression: peer death is x-hup/6, not timeout/4. */
    reason = 0xdeadu;
    assert(lorieGateAClassifyWaitWake(0, 1, 0, 0, &reason)
           == LORIE_GATEA_WAIT_WAKE_X_HUP);
    assert(reason == LORIE_GATEA_FAIL_GENERATION);
    assert(reason != LORIE_GATEA_FAIL_TIMEOUT);
    assert(lorieGateAClassifyWaitWake(1, 1, 1, 0, &reason)
           == LORIE_GATEA_WAIT_WAKE_TIMEOUT);
    assert(reason == LORIE_GATEA_FAIL_TIMEOUT);

    puts("GATEA_TEST_FAULT_CLASS=PASS");
    return 0;
}
