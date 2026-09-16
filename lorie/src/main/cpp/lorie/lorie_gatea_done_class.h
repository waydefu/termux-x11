/* X-side Done classification after lorieGateADeriveResult.
 *
 * Requires LorieGateAResult and LorieGateAFailCode. Kept as a sibling header
 * so host tests can compile the production decision without Android/X11.
 *
 * RESULT_FATAL with a published generationFatal is already authoritative:
 * do not synthesize FAIL_TIMEOUT. RESULT_FATAL with published==0 is a genuine
 * wait/loss timeout. RESULT_FAILED_QUIESCED keeps firstFailureCode (or
 * TIMEOUT if the code word is missing). SUCCESS stays SUCCESS. Never maps
 * FATAL onto SUCCESS. */
#ifndef LORIE_GATEA_DONE_CLASS_H
#define LORIE_GATEA_DONE_CLASS_H

#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif

typedef enum {
    LORIE_GATEA_DONE_SUCCESS = 0,
    LORIE_GATEA_DONE_PRESERVE_FATAL = 1,
    LORIE_GATEA_DONE_QUIESCED = 2,
    LORIE_GATEA_DONE_TIMEOUT = 3,
} LorieGateADoneClass;

static inline __always_inline LorieGateADoneClass lorieGateAClassifyDirectDone(
        LorieGateAResult result, uint32_t publishedFatal,
        uint32_t firstFailureCode, uint32_t *outReason) {
    if (outReason)
        *outReason = 0;
    if (result == LORIE_GATEA_RESULT_SUCCESS)
        return LORIE_GATEA_DONE_SUCCESS;
    if (result == LORIE_GATEA_RESULT_FAILED_QUIESCED) {
        uint32_t code = firstFailureCode != 0
            ? firstFailureCode : (uint32_t)LORIE_GATEA_FAIL_TIMEOUT;
        if (outReason)
            *outReason = code;
        return LORIE_GATEA_DONE_QUIESCED;
    }
    if (publishedFatal != 0) {
        if (outReason)
            *outReason = publishedFatal;
        return LORIE_GATEA_DONE_PRESERVE_FATAL;
    }
    if (outReason)
        *outReason = (uint32_t)LORIE_GATEA_FAIL_TIMEOUT;
    return LORIE_GATEA_DONE_TIMEOUT;
}

#endif /* LORIE_GATEA_DONE_CLASS_H */
