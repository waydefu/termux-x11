/* Renderer/Activity peer-HUP classification.
 *
 * Requires no Android/X11 types. Host tests compile this production header
 * directly. Decisions MUST be made from (bound, publishedFatal) only —
 * do not invent a new shared ABI word to distinguish HUP origin.
 *
 * bound==0: legacy disconnect. No Gate A fatal classification.
 * bound!=0 && publishedFatal!=0: peer already published authoritative
 *   generationFatal. Preserve that identity; fail-stop without a second
 *   conflicting halt log. Caller MUST terminate without normal cleanup.
 * bound!=0 && publishedFatal==0: genuine unexpected HUP of an active
 *   generation. Existing containment: r-hup / FAIL_GENERATION.
 */
#ifndef LORIE_GATEA_HUP_CLASS_H
#define LORIE_GATEA_HUP_CLASS_H

#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif

typedef enum {
    LORIE_GATEA_HUP_UNBOUND = 0,
    LORIE_GATEA_HUP_PRESERVE = 1,
    LORIE_GATEA_HUP_R_HUP = 2,
} LorieGateAHupClass;

static inline __always_inline LorieGateAHupClass
lorieGateAClassifyPeerHup(int bound, uint32_t publishedFatal) {
    if (!bound)
        return LORIE_GATEA_HUP_UNBOUND;
    if (publishedFatal != 0)
        return LORIE_GATEA_HUP_PRESERVE;
    return LORIE_GATEA_HUP_R_HUP;
}

#endif /* LORIE_GATEA_HUP_CLASS_H */
