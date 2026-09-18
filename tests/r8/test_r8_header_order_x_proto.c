/* Include-order: X.h then protocol. */
#include <X11/X.h>
#include "r8-test-protocol.h"
int main(void) {
    return sizeof(xLorieR8RegisterBufferReply) == 72 ? 0 : 1;
}
