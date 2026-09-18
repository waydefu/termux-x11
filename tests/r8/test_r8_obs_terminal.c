/* Host test: R8 observation END is terminal; post-END is a diagnostic. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#define LORIE_ENABLE_R8_TEST_SUPPORT 1

#include "lorie_r8_obs.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>

#define CAP 65536
static char g_log[CAP];
static size_t g_log_n;

int __android_log_print(int prio, const char *tag, const char *fmt, ...) {
    va_list ap;
    int n;
    (void)prio;
    (void)tag;
    if (g_log_n >= CAP - 1)
        return 0;
    va_start(ap, fmt);
    n = vsnprintf(g_log + g_log_n, CAP - g_log_n, fmt, ap);
    va_end(ap);
    if (n < 0)
        return n;
    g_log_n += (size_t)n < (CAP - g_log_n) ? (size_t)n : (CAP - g_log_n - 1);
    if (g_log_n < CAP - 1) {
        g_log[g_log_n++] = '\n';
        g_log[g_log_n] = 0;
    }
    return n;
}

static int count_sub(const char *hay, const char *needle) {
    int n = 0;
    const char *p = hay;
    size_t len = strlen(needle);
    while ((p = strstr(p, needle)) != NULL) {
        n++;
        p += len;
    }
    return n;
}

static void fail(const char *m) {
    fprintf(stderr, "FAIL %s\nlog:\n%s\n", m, g_log);
    exit(1);
}

int main(void) {
    setenv("TERMUX_X11_R8_ARM", "1", 1);
    setenv("TERMUX_X11_R8_CASE", "R8-C1", 1);
    unsetenv("TERMUX_X11_GATEA_TEST_FAULT");
    unsetenv("TERMUX_X11_GATEA_TEST_ARM");
    unsetenv("TERMUX_X11_GATEA_R6_PRESENT_REQUEUE_FAIL");

    /* X: normal records then END; later obs is POST_END diagnostic. */
    lorieR8ObsBegin("x");
    lorieR8Obs("x", "X_CLOSE_ENTER", "\"path\":\"lorieCloseScreen\"");
    lorieR8Obs("x", "X_CLOSE_RESULT", "\"generation_close\":\"invoked\"");
    lorieR8Obs("x", "X_DESTRUCTOR_ENTER", "\"bufferId\":1");
    lorieR8Obs("x", "X_DESTRUCTOR_EXIT", "\"released\":true");
    lorieR8ObsEnd("x");
    lorieR8Obs("x", "X_DESTRUCTOR_ENTER", "\"bufferId\":99");

    if (count_sub(g_log, "\"phase\":\"BEGIN\"") < 1)
        fail("x_begin");
    if (count_sub(g_log, "\"phase\":\"END\"") < 1)
        fail("x_end");
    if (count_sub(g_log, "R8_OBS_POST_END role=x phase=X_DESTRUCTOR_ENTER") != 1)
        fail("x_post_end_diag");
    if (count_sub(g_log, "\"phase\":\"X_DESTRUCTOR_ENTER\"") != 1)
        fail("x_post_end_not_record");

    /* Renderer: same detector, independent role. */
    lorieR8ObsBegin("r");
    lorieR8Obs("r", "R_UNBOUND_FINAL", "\"ready\":0");
    lorieR8Obs("r", "R_WAKE_SENT", "\"cause\":\"surface_loss\"");
    lorieR8Obs("r", "R_SURFACE_QUIESCED", "\"cause\":\"surface_loss\"");
    lorieR8ObsEnd("r");
    lorieR8Obs("r", "R_WAKE_SENT", "\"cause\":\"surface_loss\"");
    if (count_sub(g_log, "R8_OBS_POST_END role=r phase=R_WAKE_SENT") != 1)
        fail("r_post_end_diag");
    /* One legal R_WAKE_SENT record (pre-END) plus POST_END diagnostic. */
    if (count_sub(g_log, "\"phase\":\"R_WAKE_SENT\"") != 1)
        fail("r_post_end_not_record");

    /* Second END is idempotent: still one END record per role. */
    lorieR8ObsEnd("x");
    lorieR8ObsEnd("r");
    if (count_sub(g_log, "\"phase\":\"END\"") != 2)
        fail("end_idempotent");

    printf("PASS test_r8_obs_terminal\n");
    return 0;
}
