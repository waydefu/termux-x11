#ifndef LORIE_R8_TEST_X_H
#define LORIE_R8_TEST_X_H

#ifdef LORIE_ENABLE_R8_TEST_SUPPORT

#include "lorie_r8_test.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Opaque X pixmap. Not pixmap.h / pixmapstr.h. */
struct _Pixmap;

struct LorieBuffer *lorieGateAR8EnsureGpuSampleableAhb(struct _Pixmap *pixmap);
int lorieGateAR8PixmapReject(struct _Pixmap *pixmap);

#ifdef __cplusplus
}
#endif

#endif
#endif
