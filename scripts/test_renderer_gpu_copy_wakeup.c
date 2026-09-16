/* Host CASE_LOOP wakeup policy test.
 *
 * X publishes writeIndex without Activity stateLock, then cond_signal.
 * If GLES already decided to wait, the signal is lost. Infinite cond_wait
 * then sleeps through the 2000 ms EXA budget because lorieGpuCopyWait does
 * not pump AChoreographer (waitForNextFrame stays true).
 *
 * usage: test_renderer_gpu_copy_wakeup legacy|fixed
 *   legacy: cond_wait, no signal after publish → must NOT finish in 150 ms
 *   fixed:  8 ms timedwait + sticky recheck, no signal → must finish < 150 ms
 */
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define FRAME_WAIT_NS 8000000L
#define JOIN_BUDGET_MS 150

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond = PTHREAD_COND_INITIALIZER;
static volatile uint64_t rd = 0;
static volatile uint64_t wr = 0;
static volatile int finished = 0;
static int use_fixed;

static void *waiter(void *arg) {
    (void)arg;
    pthread_mutex_lock(&lock);
    while (rd == wr) {
        if (use_fixed) {
            struct timespec deadline;
            clock_gettime(CLOCK_REALTIME, &deadline);
            deadline.tv_nsec += FRAME_WAIT_NS;
            if (deadline.tv_nsec >= 1000000000L) {
                deadline.tv_sec++;
                deadline.tv_nsec -= 1000000000L;
            }
            pthread_cond_timedwait(&cond, &lock, &deadline);
        } else {
            pthread_cond_wait(&cond, &lock);
        }
    }
    pthread_mutex_unlock(&lock);
    finished = 1;
    return NULL;
}

static long elapsed_ms(const struct timespec *t0, const struct timespec *t1) {
    return (t1->tv_sec - t0->tv_sec) * 1000L + (t1->tv_nsec - t0->tv_nsec) / 1000000L;
}

int main(int argc, char **argv) {
    pthread_t th;
    struct timespec t0, now;
    long waited;
    int i;

    if (argc != 2 || (strcmp(argv[1], "legacy") != 0 && strcmp(argv[1], "fixed") != 0)) {
        fprintf(stderr, "usage: %s legacy|fixed\n", argv[0]);
        return 2;
    }
    use_fixed = strcmp(argv[1], "fixed") == 0;

    if (pthread_create(&th, NULL, waiter, NULL) != 0) {
        perror("pthread_create");
        return 1;
    }
    /* Let the waiter reach cond_wait / timedwait. */
    usleep(30000);
    wr = 1; /* publish, deliberately no cond_signal — lost-wakeup analogue */

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (i = 0; i < JOIN_BUDGET_MS / 5; i++) {
        if (finished)
            break;
        usleep(5000);
    }
    clock_gettime(CLOCK_MONOTONIC, &now);
    waited = elapsed_ms(&t0, &now);

    if (use_fixed) {
        if (!finished) {
            fprintf(stderr, "FIXED=FAIL waiter still blocked after %ld ms without signal\n",
                    waited);
            return 1;
        }
        printf("FIXED=PASS waited_ms=%ld (sticky writeIndex, no cond_signal)\n", waited);
        pthread_join(th, NULL);
        return 0;
    }

    if (finished) {
        fprintf(stderr, "LEGACY=UNEXPECTED_WAKE waited_ms=%ld\n", waited);
        pthread_join(th, NULL);
        return 1;
    }
    printf("LEGACY=HANG_AS_EXPECTED still_blocked after %ld ms without signal\n", waited);
    /* Process exit reaps the waiter; do not pthread_cancel a cond_wait. */
    (void)th;
    return 0;
}
