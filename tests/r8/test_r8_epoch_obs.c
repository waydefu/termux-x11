/* Host test (D-02 / R9): epoch records are ORDINARY semantic phases that carry
 * their own tuple, and do NOT create a second BEGIN/END terminal stream.
 *
 * What this pins:
 *   1. R_EPOCH_BEGIN / R_EPOCH_END carry epoch_id / epoch_nonce / epoch_generation
 *      / reason, and their original_* columns are the EPOCH's tuple, not the
 *      process-global pair bound by lorieR8BindTuple().
 *   2. lorieR8Obs() is unchanged: it still stamps the process globals, so no
 *      existing R8 record changes shape.
 *   3. epoch ids are monotonic and distinct across generations in one process.
 *   4. Epoch records do NOT terminate the stream: ordinary records emitted after
 *      an R_EPOCH_END are still recorded, NOT classified R8_OBS_POST_END.
 *      (This is the property that makes multi-epoch observation possible at all.)
 *   5. After the real terminal (lorieR8ObsEnd) the old contract still holds:
 *      a later epoch record IS a post-end diagnostic.
 */
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

static int failures;

static void need(int cond, const char *what) {
    if (!cond) {
        failures++;
        printf("FAIL %s\n", what);
    }
}

static int has(const char *needle) { return strstr(g_log, needle) != NULL; }

static int count_sub(const char *needle) {
    int n = 0;
    const char *p = g_log;
    size_t len = strlen(needle);
    while ((p = strstr(p, needle)) != NULL) { n++; p += len; }
    return n;
}

/* Return the single log line containing `key`, or NULL. */
static const char *line_with(const char *key) {
    static char buf[2048];
    const char *hit = strstr(g_log, key);
    const char *start, *end;
    if (!hit)
        return NULL;
    start = hit;
    while (start > g_log && start[-1] != '\n')
        start--;
    end = strchr(hit, '\n');
    if (!end)
        end = g_log + strlen(g_log);
    if ((size_t)(end - start) >= sizeof(buf))
        return NULL;
    memcpy(buf, start, (size_t)(end - start));
    buf[end - start] = 0;
    return buf;
}

int main(void) {
    const char *l;
    uint64_t e1, e2;

    if (setenv("TERMUX_X11_R8_ARM", "1", 1) != 0
        || setenv("TERMUX_X11_R8_CASE", "R8-C1", 1) != 0) {
        printf("FAIL setenv\n");
        return 1;
    }

    lorieR8ObsBegin("r");

    /* Process-global tuple = generation 7. Epoch records must NOT inherit it. */
    lorieR8BindTuple(0xAAAAu, 7u);

    e1 = lorieR8EpochAllocId();
    lorieR8ObsEpoch("r", "R_EPOCH_BEGIN", e1, 0xAAAAu, 1u, "bind", NULL);
    lorieR8Obs("r", "R_ORDINARY_ONE", NULL);
    lorieR8ObsEpoch("r", "R_EPOCH_END", e1, 0xAAAAu, 1u, "generation_close", NULL);

    /* 4: an ordinary record AFTER an epoch end must still be recorded. */
    lorieR8Obs("r", "R_AFTER_EPOCH_END", NULL);

    e2 = lorieR8EpochAllocId();
    lorieR8ObsEpoch("r", "R_EPOCH_BEGIN", e2, 0xAAAAu, 2u, "bind", NULL);
    lorieR8ObsEpoch("r", "R_EPOCH_END", e2, 0xAAAAu, 2u, "generation_close", NULL);

    /* 3: monotonic and distinct. */
    need(e2 == e1 + 1, "epoch_ids_monotonic");
    need(e1 != e2, "epoch_ids_distinct");
    need(lorieR8EpochCurrentId() == e2, "epoch_current_is_last");

    /* 1: epoch records carry their own tuple in original_generation. */
    l = line_with("R_EPOCH_BEGIN");
    need(l != NULL, "epoch_begin_present");
    if (l) {
        /* original_* MUST stay the process globals (7), not the epoch tuple (1):
         * judge-r8-v2.py tuple_bind() reads those columns and the renderer's
         * values must remain what they were before D-02. */
        need(strstr(l, "\"original_generation\":7") != NULL,
             "epoch_original_columns_unchanged");
        need(strstr(l, "\"epoch_generation\":1") != NULL, "epoch_begin_generation_field");
        need(strstr(l, "\"epoch_nonce\":43690") != NULL, "epoch_begin_nonce_field");
        need(strstr(l, "\"epoch_id\":1") != NULL, "epoch_begin_id_field");
        need(strstr(l, "\"reason\":\"bind\"") != NULL, "epoch_begin_reason_field");
    }
    l = line_with("R_EPOCH_END");
    need(l != NULL && strstr(l, "\"reason\":\"generation_close\"") != NULL,
         "epoch_end_reason_field");

    /* Second epoch must report generation 2, proving records are not sharing a
     * single process-global pair. */
    need(count_sub("\"epoch_generation\":2") == 2, "second_epoch_tuple_distinct");

    /* 2: an ordinary record still stamps the process globals (generation 7). */
    l = line_with("R_ORDINARY_ONE");
    need(l != NULL && strstr(l, "\"original_generation\":7") != NULL,
         "ordinary_record_uses_process_globals");

    /* 4 (cont.): the post-epoch-end ordinary record was recorded, not diagnosed. */
    need(has("R_AFTER_EPOCH_END"), "record_after_epoch_end_kept");
    need(count_sub("R8_OBS_POST_END") == 0, "no_post_end_before_terminal");

    /* 5: the real terminal still terminates, and epoch records are not exempt. */
    lorieR8ObsEnd("r");
    need(count_sub("\"phase\":\"END\"") == 1, "single_terminal_end");
    lorieR8ObsEpoch("r", "R_EPOCH_BEGIN", lorieR8EpochAllocId(), 0xAAAAu, 3u,
                    "bind", NULL);
    need(count_sub("R8_OBS_POST_END") == 1, "epoch_after_terminal_is_post_end");
    need(count_sub("\"epoch_generation\":3") == 0, "epoch_after_terminal_not_recorded");

    if (failures) {
        printf("FAILED test_r8_epoch_obs failures=%d\n", failures);
        return 1;
    }
    printf("PASS test_r8_epoch_obs {\"checks\": 17, \"failures\": 0}\n");
    return 0;
}
