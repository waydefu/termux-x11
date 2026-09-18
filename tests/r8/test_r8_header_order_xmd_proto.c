/* Include-order: Xmd then protocol. */
#include <X11/Xmd.h>
#include "r8-test-protocol.h"
int main(void) {
    return sizeof(xLorieR8CheckpointReply) == 72 ? 0 : 1;
}
