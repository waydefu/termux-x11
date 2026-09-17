/* X-side wait-loop wake classification after lorieGateADeriveResult
 * returned NONE.
 *
 * Requires LorieGateAFailCode. Host tests compile this production header
 * without Android/X11.
 *
 * Observation order in the caller MUST be:
 *   1. derive completed/firstFailed/fatal for the target serial
 *   2. if result != NONE, return that result (SUCCESS / QUIESCED / FATAL)
 *   3. only then classify wait-wake
 *
 * Peer death (connectionAlive==0) is not a timeout. With publishedFatal==0
 * it is X-side HUP containment (x-hup / FAIL_GENERATION). With a published
 * fatal it preserves that identity. Genuine elapsed timeout with a live
 * peer remains FAIL_TIMEOUT. Surface loss without socket HUP is not x-hup.
 */
#ifndef LORIE_GATEA_WAIT_WAKE_CLASS_H
#define LORIE_GATEA_WAIT_WAKE_CLASS_H

#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif

typedef enum {
    LORIE_GATEA_WAIT_WAKE_CONTINUE = 0,
    LORIE_GATEA_WAIT_WAKE_TIMEOUT = 1,
    LORIE_GATEA_WAIT_WAKE_X_HUP = 2,
    LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL = 3,
    LORIE_GATEA_WAIT_WAKE_SURFACE_LOSS = 4,
} LorieGateAWaitWakeClass;

static inline __always_inline LorieGateAWaitWakeClass
lorieGateAClassifyWaitWake(int connectionAlive, int rendererAvailable,
                           int timeoutExpired, uint32_t publishedFatal,
                           uint32_t *outReason) {
    if (outReason)
        *outReason = 0;
    if (!connectionAlive) {
        if (publishedFatal != 0) {
            if (outReason)
                *outReason = publishedFatal;
            return LORIE_GATEA_WAIT_WAKE_PRESERVE_FATAL;
        }
        if (outReason)
            *outReason = (uint32_t)LORIE_GATEA_FAIL_GENERATION;
        return LORIE_GATEA_WAIT_WAKE_X_HUP;
    }
    if (!rendererAvailable)
        return LORIE_GATEA_WAIT_WAKE_SURFACE_LOSS;
    if (timeoutExpired) {
        if (outReason)
            *outReason = (uint32_t)LORIE_GATEA_FAIL_TIMEOUT;
        return LORIE_GATEA_WAIT_WAKE_TIMEOUT;
    }
    return LORIE_GATEA_WAIT_WAKE_CONTINUE;
}

#endif /* LORIE_GATEA_WAIT_WAKE_CLASS_H */
