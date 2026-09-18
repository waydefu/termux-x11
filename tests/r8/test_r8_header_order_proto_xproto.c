/* Include-order: protocol then Xproto (legal on this host). */
#include "r8-test-protocol.h"
#include <X11/Xproto.h>
int main(void) {
    return sizeof(xLorieR8QueryVersionReply) == 32 ? 0 : 1;
}
