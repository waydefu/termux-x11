/* Host test for lorieGateAClassifyPeerHup.
 *
 * Enums must match lorie.h FailCode values used by R7-05 / genuine HUP.
 * verify_r7_hup_preserve.py proves that binding before this binary is
 * treated as PASS.
 *
 * usage: test_gatea_hup_class
 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "../lorie/src/main/cpp/lorie/lorie_gatea_hup_class.h"

enum {
    LORIE_GATEA_FAIL_DRAW = 2,
    LORIE_GATEA_FAIL_GENERATION = 6,
};

int main(void) {
    /* Case A — bound + published DRAW: preserve, not r-hup. */
    assert(lorieGateAClassifyPeerHup(1, LORIE_GATEA_FAIL_DRAW)
           == LORIE_GATEA_HUP_PRESERVE);
    assert(lorieGateAClassifyPeerHup(1, LORIE_GATEA_FAIL_DRAW)
           != LORIE_GATEA_HUP_R_HUP);
    assert(lorieGateAClassifyPeerHup(1, LORIE_GATEA_FAIL_DRAW)
           != LORIE_GATEA_HUP_UNBOUND);

    /* Case B — bound + published GENERATION: still preserve, no duplicate
     * r-hup classification. */
    assert(lorieGateAClassifyPeerHup(1, LORIE_GATEA_FAIL_GENERATION)
           == LORIE_GATEA_HUP_PRESERVE);
    assert(lorieGateAClassifyPeerHup(1, LORIE_GATEA_FAIL_GENERATION)
           != LORIE_GATEA_HUP_R_HUP);

    /* Case C — bound + published==0: genuine unexpected HUP stays r-hup. */
    assert(lorieGateAClassifyPeerHup(1, 0) == LORIE_GATEA_HUP_R_HUP);
    assert(lorieGateAClassifyPeerHup(1, 0) != LORIE_GATEA_HUP_PRESERVE);
    assert(lorieGateAClassifyPeerHup(1, 0) != LORIE_GATEA_HUP_UNBOUND);

    /* Case D — unbound: legacy disconnect, no Gate A fatal class. */
    assert(lorieGateAClassifyPeerHup(0, 0) == LORIE_GATEA_HUP_UNBOUND);
    assert(lorieGateAClassifyPeerHup(0, LORIE_GATEA_FAIL_DRAW)
           == LORIE_GATEA_HUP_UNBOUND);
    assert(lorieGateAClassifyPeerHup(0, LORIE_GATEA_FAIL_GENERATION)
           == LORIE_GATEA_HUP_UNBOUND);
    assert(lorieGateAClassifyPeerHup(0, 0) != LORIE_GATEA_HUP_R_HUP);
    assert(lorieGateAClassifyPeerHup(0, 0) != LORIE_GATEA_HUP_PRESERVE);

    /* Fail-stop vs cleanup: only UNBOUND may reach normal cleanup.
     * PRESERVE and R_HUP are both fail-stop dispositions. */
    assert(lorieGateAClassifyPeerHup(1, LORIE_GATEA_FAIL_DRAW)
           != LORIE_GATEA_HUP_UNBOUND);
    assert(lorieGateAClassifyPeerHup(1, 0) != LORIE_GATEA_HUP_UNBOUND);

    puts("GATEA_HUP_CLASS=PASS");
    return 0;
}
