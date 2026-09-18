#ifndef LORIE_R8_OBS_H
#define LORIE_R8_OBS_H

#ifdef LORIE_ENABLE_R8_TEST_SUPPORT

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int lorieR8Armed(void);
const char *lorieR8CaseName(void);
uint32_t lorieR8CaseCodeCached(void);
void lorieR8BindTuple(uint64_t nonce, uint64_t generation);
void lorieR8OriginalTuple(uint64_t *nonce, uint64_t *generation);

void lorieR8ObsBegin(const char *role);
void lorieR8ObsEnd(const char *role);
void lorieR8Obs(const char *role, const char *phase, const char *fields);

uint64_t lorieR8WakeSent(const char *cause, int send_ok, uint64_t completed);
uint64_t lorieR8WakeReceived(uint64_t ordinal_hint);
uint64_t lorieR8DeferAllocId(void);

int lorieR8ValidateStartupEnv(int proto, int telemetry);

#ifdef __cplusplus
}
#endif

#endif /* LORIE_ENABLE_R8_TEST_SUPPORT */
#endif
