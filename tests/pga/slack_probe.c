/* RCA-XFCE-3 probe (NOT a fixture, NOT product code).
 *
 *   slack_probe [n]
 *
 * lorieGpuCopyWait (InitOutput.c:1946) polls completion with usleep(200). How long that
 * really sleeps depends on the calling thread's timer slack, which Android sets per
 * process group and which fork/exec inherits. X3 cannot be inspected from PRoot
 * (timerslack_ns of another pid is EPERM), so this probe is launched through the SAME
 * path as X3 (start-x3-untraced.sh -> TermuxService) and reports what a thread there
 * gets: its inherited slack, then usleep(200) with that slack, then (control) with the
 * slack forced to 1 ns. If the inherited slack does not matter, both phases match.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sched.h>
#include <time.h>
#include <unistd.h>

static double now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}

static int cmp(const void *a, const void *b) {
    double x = *(const double *) a, y = *(const double *) b;
    return x < y ? -1 : x > y;
}

static void phase(const char *name, int n) {
    double *d = malloc(sizeof(double) * n), sum = 0;
    for (int i = 0; i < n; i++) {
        double t = now_ms();
        usleep(200);
        d[i] = now_ms() - t;
        sum += d[i];
    }
    qsort(d, n, sizeof(double), cmp);
    printf("PHASE %s slack_ns=%d n=%d usleep200_ms mean=%.3f p50=%.3f p90=%.3f p99=%.3f max=%.3f\n",
           name, prctl(PR_GET_TIMERSLACK, 0, 0, 0, 0), n, sum / n, d[n / 2], d[n * 9 / 10],
           d[n * 99 / 100], d[n - 1]);
    free(d);
}

int main(int argc, char **argv) {
    int n = argc > 1 ? atoi(argv[1]) : 3000;
    char buf[4096];
    FILE *f;
    printf("PID %d inherited_timerslack_ns=%d sched_policy=%d\n", getpid(),
           prctl(PR_GET_TIMERSLACK, 0, 0, 0, 0), sched_getscheduler(0));
    if ((f = fopen("/proc/self/cgroup", "r"))) {
        while (fgets(buf, sizeof buf, f)) printf("CGROUP %s", buf);
        fclose(f);
    }
    if ((f = fopen("/proc/self/status", "r"))) {
        while (fgets(buf, sizeof buf, f))
            if (!strncmp(buf, "TracerPid", 9) || !strncmp(buf, "Cpus_allowed_list", 17)) printf("STATUS %s", buf);
        fclose(f);
    }
    phase("inherited", n);
    prctl(PR_SET_TIMERSLACK, 1, 0, 0, 0);
    phase("slack_1ns", n);
    printf("DONE\n");
    return 0;
}
