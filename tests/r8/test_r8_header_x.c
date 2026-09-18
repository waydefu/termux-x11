/* X-only header: opaque struct _Pixmap * without pixmap.h. */
#define LORIE_ENABLE_R8_TEST_SUPPORT 1
#include "lorie_r8_test_x.h"

static struct LorieBuffer *call_sample(struct _Pixmap *p) {
    return lorieGateAR8EnsureGpuSampleableAhb(p);
}

static int call_reject(struct _Pixmap *p) {
    return lorieGateAR8PixmapReject(p);
}

int main(void) {
    (void)call_sample;
    (void)call_reject;
    return 0;
}
