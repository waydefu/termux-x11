/* Host CASE_LOOP wakeup policy test.
 *
 * Models: renderer waits → producer publish-release writeIndex → no
 * cond_signal → legacy infinite wait stays asleep → fixed monotonic
 * timedwait rechecks the sticky queue and exits.
 *
 * Queue indices use C11 acquire/release to match production
 * lorieGateAPublishWriteIndex / lorieGateAObserveWriteIndex
 * (X RELEASE-stores writeIndex; renderer ACQUIRE-loads it).
 * volatile is not used as synchronization.
 *
 * finished is an ancillary completion flag (not a production queue word).
 * release-store / acquire-load is enough: the waiter publishes it only after
 * the acquire load has observed wr, and the main thread only reads it.
 *
 * usage: test_renderer_gpu_copy_wakeup legacy|fixed
 *   legacy: cond_wait, no signal after publish → must NOT finish in 150 ms
 *   fixed:  8 ms CLOCK_MONOTONIC timedwait + sticky recheck, no signal
 *           → must finish well below 2000 ms
 */
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define FRAME_WAIT_NS 8000000L
#define JOIN_BUDGET_MS 150

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond;
static _Atomic uint64_t rd;
static _Atomic uint64_t wr;
static _Atomic int entered_wait;
static _Atomic int finished;
static int use_fixed;

static void add_ns(struct timespec *ts, long ns) {
    ts->tv_nsec += ns;
    if (ts->tv_nsec >= 1000000000L) {
        ts->tv_sec++;
        ts->tv_nsec -= 1000000000L;
    }
}

static void *waiter(void *arg) {
    (void)arg;
    pthread_mutex_lock(&lock);
    while (atomic_load_explicit(&rd, memory_order_acquire)
            == atomic_load_explicit(&wr, memory_order_acquire)) {
        /* Harness: we have decided the queue is empty and are about to
         * sleep. Main publishes after observing this, with no cond_signal. */
        atomic_store_explicit(&entered_wait, 1, memory_order_release);
        if (use_fixed) {
            struct timespec deadline;
            if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0)
                abort();
            add_ns(&deadline, FRAME_WAIT_NS);
            pthread_cond_timedwait(&cond, &lock, &deadline);
        } else {
            pthread_cond_wait(&cond, &lock);
        }
    }
    pthread_mutex_unlock(&lock);
    atomic_store_explicit(&finished, 1, memory_order_release);
    return NULL;
}

static long elapsed_ms(const struct timespec *t0, const struct timespec *t1) {
    return (t1->tv_sec - t0->tv_sec) * 1000L + (t1->tv_nsec - t0->tv_nsec) / 1000000L;
}

int main(int argc, char **argv) {
    pthread_t th;
    pthread_condattr_t attr;
    struct timespec t0, now;
    long waited;
    int i;
    int rc;

    if (argc != 2 || (strcmp(argv[1], "legacy") != 0 && strcmp(argv[1], "fixed") != 0)) {
        fprintf(stderr, "usage: %s legacy|fixed\n", argv[0]);
        return 2;
    }
    use_fixed = strcmp(argv[1], "fixed") == 0;

    atomic_init(&rd, 0);
    atomic_init(&wr, 0);
    atomic_init(&entered_wait, 0);
    atomic_init(&finished, 0);

    /* Match production: frame-gated timedwait uses CLOCK_MONOTONIC.
     * Signal is unused in this test (lost-wakeup analogue). */
    rc = pthread_condattr_init(&attr);
    if (rc != 0) {
        fprintf(stderr, "pthread_condattr_init: %s\n", strerror(rc));
        return 1;
    }
    rc = pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
    if (rc != 0) {
        fprintf(stderr, "pthread_condattr_setclock: %s\n", strerror(rc));
        return 1;
    }
    rc = pthread_cond_init(&cond, &attr);
    if (rc != 0) {
        fprintf(stderr, "pthread_cond_init: %s\n", strerror(rc));
        return 1;
    }
    pthread_condattr_destroy(&attr);

    if (pthread_create(&th, NULL, waiter, NULL) != 0) {
        perror("pthread_create");
        return 1;
    }
    for (i = 0; i < 200 && !atomic_load_explicit(&entered_wait, memory_order_acquire); i++)
        usleep(1000);
    if (!atomic_load_explicit(&entered_wait, memory_order_acquire)) {
        fprintf(stderr, "waiter never entered idle wait\n");
        return 1;
    }
    /* Publish sticky writeIndex. Deliberately no cond_signal. */
    atomic_store_explicit(&wr, 1, memory_order_release);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (i = 0; i < JOIN_BUDGET_MS / 5; i++) {
        if (atomic_load_explicit(&finished, memory_order_acquire))
            break;
        usleep(5000);
    }
    clock_gettime(CLOCK_MONOTONIC, &now);
    waited = elapsed_ms(&t0, &now);

    if (use_fixed) {
        if (!atomic_load_explicit(&finished, memory_order_acquire)) {
            fprintf(stderr, "FIXED=FAIL waiter still blocked after %ld ms without signal\n",
                    waited);
            return 1;
        }
        printf("FIXED=PASS waited_ms=%ld (sticky writeIndex, no cond_signal)\n", waited);
        pthread_join(th, NULL);
        pthread_cond_destroy(&cond);
        return 0;
    }

    if (atomic_load_explicit(&finished, memory_order_acquire)) {
        fprintf(stderr, "LEGACY=UNEXPECTED_WAKE waited_ms=%ld\n", waited);
        pthread_join(th, NULL);
        pthread_cond_destroy(&cond);
        return 1;
    }
    printf("LEGACY=HANG_AS_EXPECTED still_blocked after %ld ms without signal\n", waited);
    /* Process exit reaps the waiter. pthread_cancel of a cond_wait is
     * unnecessary and not used: the hang is the asserted legacy property,
     * and the thread's only shared objects are process-local. */
    (void)th;
    return 0;
}
