#ifndef LORIE_R8_TEST_H
#define LORIE_R8_TEST_H

#ifdef LORIE_ENABLE_R8_TEST_SUPPORT

#include "lorie.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void LorieR8TestExtensionInit(void);

Bool lorieGateAR8EnsureReadyForBuffer(LorieBuffer *buf);
LorieBuffer *lorieGateAR8EnsureGpuSampleableAhb(PixmapPtr pixmap);
int lorieGateAR8PairSnapshot(int *state, uint64_t *srcId, uint64_t *dstId,
                             uint64_t *nonce, uint64_t *generation);
int32_t lorieGateAR8RootPending(void);
int lorieGateAR8CopyRegistry(struct LorieGateABufferMeta *out, uint32_t cap);
int lorieGateAR8PixmapReject(PixmapPtr pixmap);

#if defined(LORIE_ENABLE_R8_TEST_SUPPORT) && !defined(__ANDROID__)
int lorieR8HostInjectGpuCopyDone(void);
#endif

#ifdef __cplusplus
}
#endif

#endif
#endif
