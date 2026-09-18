/* X-only header after a local PixmapPtr typedef (no pixmap.h). */
#define LORIE_ENABLE_R8_TEST_SUPPORT 1
typedef struct _Pixmap *PixmapPtr;
#include "lorie_r8_test_x.h"
int main(void) {
    PixmapPtr p = 0;
    (void)p;
    return 0;
}
