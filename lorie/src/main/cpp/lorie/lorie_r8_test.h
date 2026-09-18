#ifndef LORIE_R8_TEST_H
#define LORIE_R8_TEST_H

#ifdef LORIE_ENABLE_R8_TEST_SUPPORT

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct LorieBuffer;
struct LorieGateABufferMeta;

void LorieR8TestExtensionInit(void);

int lorieGateAR8EnsureReadyForBuffer(struct LorieBuffer *buf);
int lorieGateAR8PairSnapshot(int *state, uint64_t *srcId, uint64_t *dstId,
                             uint64_t *nonce, uint64_t *generation);
int32_t lorieGateAR8RootPending(void);
int lorieGateAR8CopyRegistry(struct LorieGateABufferMeta *out, uint32_t cap);

#if defined(LORIE_ENABLE_R8_TEST_SUPPORT) && !defined(__ANDROID__)
int lorieR8HostInjectGpuCopyDone(void);
#endif

#ifdef __cplusplus
}
#endif

#endif
#endif
