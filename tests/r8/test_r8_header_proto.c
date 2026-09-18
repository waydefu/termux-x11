/* Protocol header alone, C11. */
#include "r8-test-protocol.h"
int main(void) {
    return (int)(sizeof(xLorieR8QueryVersionReply) != 32);
}
