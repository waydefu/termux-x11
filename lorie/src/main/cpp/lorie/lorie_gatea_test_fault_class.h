/* Test-only Artifact-B tail helpers (struct LorieGateATestFault).
 *
 * Host tests compile this production header without Android/X11.
 * Does not change LorieGateAProtocol, EVENT_MAX, COUNTER_MAX, or
 * LorieGpuCopyEntry.
 *
 * Present-bound cells (12/13) may be published with armed=0. Eligibility
 * requires a later ArmPresentTarget with nonzero serial+generation before
 * consume. Other cells keep startup armed=1.
 *
 * Arm stores target identity first, then armed=1 with release. Consume
 * acquire-loads armed, then targets. Zero target fields remain wildcards
 * for non-Present cells.
 */
#ifndef LORIE_GATEA_TEST_FAULT_CLASS_H
#define LORIE_GATEA_TEST_FAULT_CLASS_H

#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif

#ifndef LORIE_GATEA_TEST_MAGIC
#define LORIE_GATEA_TEST_MAGIC 0x47374146u
#endif
#ifndef LORIE_GATEA_TEST_VERSION
#define LORIE_GATEA_TEST_VERSION 1u
#endif
#ifndef LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE
#define LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE 12
#endif
#ifndef LORIE_GATEA_TEST_PRESENT_RENDERER_EXIT
#define LORIE_GATEA_TEST_PRESENT_RENDERER_EXIT 13
#endif

static inline __always_inline int
lorieGateATestFaultIsPresentBoundCell(uint32_t cell) {
    return cell == (uint32_t)LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE
        || cell == (uint32_t)LORIE_GATEA_TEST_PRESENT_RENDERER_EXIT;
}

static inline __always_inline int
lorieGateATestFaultClassArmed(const struct LorieGateATestFault *f, uint32_t cell) {
    uint32_t armed, magic, version, got;
    if (f == NULL)
        return 0;
    armed = __atomic_load_n(&f->armed, __ATOMIC_ACQUIRE);
    if (armed == 0)
        return 0;
    magic = __atomic_load_n(&f->magic, __ATOMIC_ACQUIRE);
    version = __atomic_load_n(&f->version, __ATOMIC_ACQUIRE);
    got = __atomic_load_n(&f->cell, __ATOMIC_ACQUIRE);
    return magic == LORIE_GATEA_TEST_MAGIC
        && version == LORIE_GATEA_TEST_VERSION
        && got == cell;
}

static inline __always_inline int
lorieGateATestFaultClassConsume(struct LorieGateATestFault *f, uint32_t cell,
                                uint64_t serial, uint64_t generation) {
    uint32_t expected;
    uint64_t targetGen, targetOrd;
    if (!lorieGateATestFaultClassArmed(f, cell))
        return 0;
    targetGen = __atomic_load_n(&f->targetGeneration, __ATOMIC_ACQUIRE);
    targetOrd = __atomic_load_n(&f->targetOrdinal, __ATOMIC_ACQUIRE);
    /* Present-bound cells never use wildcard targets. armed=1 with
     * serial/generation still 0 is ineligible (startup CopyArea). */
    if (lorieGateATestFaultIsPresentBoundCell(cell)
        && (targetGen == 0 || targetOrd == 0))
        return 0;
    if (targetGen != 0 && targetGen != generation)
        return 0;
    if (targetOrd != 0 && targetOrd != serial)
        return 0;
    expected = 0;
    if (!__atomic_compare_exchange_n(&f->consumed, &expected, 1u,
                                     0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
        return 0;
    return 1;
}

/* Returns 1 if this cell is now eligible for serial/generation.
 * Never re-arms after consume. Never steals a different armed target.
 * Stores targets before armed=1. serial==0 or generation==0 refused. */
static inline __always_inline int
lorieGateATestFaultClassArmPresentTarget(struct LorieGateATestFault *f,
                                         uint64_t serial, uint64_t generation) {
    uint32_t magic, version, cell, consumed, armed;
    uint64_t gotGen, gotOrd;
    if (f == NULL || serial == 0 || generation == 0)
        return 0;
    magic = __atomic_load_n(&f->magic, __ATOMIC_ACQUIRE);
    version = __atomic_load_n(&f->version, __ATOMIC_ACQUIRE);
    cell = __atomic_load_n(&f->cell, __ATOMIC_ACQUIRE);
    if (magic != LORIE_GATEA_TEST_MAGIC
        || version != LORIE_GATEA_TEST_VERSION
        || !lorieGateATestFaultIsPresentBoundCell(cell))
        return 0;
    consumed = __atomic_load_n(&f->consumed, __ATOMIC_ACQUIRE);
    if (consumed != 0)
        return 0;
    armed = __atomic_load_n(&f->armed, __ATOMIC_ACQUIRE);
    if (armed != 0) {
        gotGen = __atomic_load_n(&f->targetGeneration, __ATOMIC_ACQUIRE);
        gotOrd = __atomic_load_n(&f->targetOrdinal, __ATOMIC_ACQUIRE);
        return gotGen == generation && gotOrd == serial;
    }
    __atomic_store_n(&f->targetGeneration, generation, __ATOMIC_RELAXED);
    __atomic_store_n(&f->targetOrdinal, serial, __ATOMIC_RELAXED);
    __atomic_store_n(&f->armed, 1u, __ATOMIC_RELEASE);
    return 1;
}

/* Test-only Present-hold (cell 12). After the matching target is consumed,
 * lorieGpuCopyIsDone(S) must stay false even if a later unrelated T>S
 * stores completedSerial=T. Production unarmed/unconsumed path is a no-op.
 * Cell 13 (renderer-exit) does not hold: the process dies instead.
 * Does not change completedSerial publication or ObserveCompleted. */
static inline __always_inline int
lorieGateATestFaultClassHoldsIncomplete(const struct LorieGateATestFault *f,
                                        uint64_t serial) {
    uint32_t magic, version, cell, consumed;
    uint64_t targetOrd;
    if (f == NULL || serial == 0)
        return 0;
    consumed = __atomic_load_n(&f->consumed, __ATOMIC_ACQUIRE);
    if (consumed == 0)
        return 0;
    magic = __atomic_load_n(&f->magic, __ATOMIC_ACQUIRE);
    version = __atomic_load_n(&f->version, __ATOMIC_ACQUIRE);
    cell = __atomic_load_n(&f->cell, __ATOMIC_ACQUIRE);
    if (magic != LORIE_GATEA_TEST_MAGIC
        || version != LORIE_GATEA_TEST_VERSION
        || cell != (uint32_t)LORIE_GATEA_TEST_PRESENT_HOLD_COMPLETE)
        return 0;
    targetOrd = __atomic_load_n(&f->targetOrdinal, __ATOMIC_ACQUIRE);
    return targetOrd != 0 && targetOrd == serial;
}

#endif /* LORIE_GATEA_TEST_FAULT_CLASS_H */
