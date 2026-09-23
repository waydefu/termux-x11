/* emit_cost.c - PGA-GAP-1 RCA: what does ONE p2a2_emit() cost in this PRoot?
 *
 * Replays the exact syscall sequence of p2a2_emit (patches/dix-config.h.in:222) minus
 * the liblog call, which a glibc process cannot make:
 *     write(2, msg) ; write(2, "\n") ;
 *     open(snap, O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC) ; write ; write ; fsync ; close
 * stderr is a regular file, as it is for X3 (the launcher log). Variants isolate the
 * parts: A = full sequence, B = without fsync, C = stderr writes only, D = nothing
 * (loop + clock overhead). The liblog write is NOT included, so A is a LOWER bound
 * on the real per-emit cost.
 *
 *   cc -O2 -o emit_cost emit_cost.c && ./emit_cost <snap> <stderr-file> <n>
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static int cmp(const void *a, const void *b) {
    double x = *(const double *) a, y = *(const double *) b;
    return (x > y) - (x < y);
}

int main(int argc, char **argv) {
    if (argc < 4) return 64;
    const char *snap = argv[1];
    int errfd = open(argv[2], O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    int n = atoi(argv[3]);
    if (errfd < 0 || n <= 0) return 65;
    /* the real emit: an Sprep line from xfce-c1-03, 150 bytes */
    const char *msg = "Sprep pix=0xb400007cfc65cd10 index=0 has_gpu_copy=1 fb_ptr=0x0 "
                      "sys_ptr=0x0 sys_pitch=3328 fb_pitch=3208 dev_ptr_before=0x0 devKind_before=3328";
    size_t len = strlen(msg);
    double *lat = malloc(sizeof(double) * n);
    const char *name[] = {"A_full", "B_no_fsync", "C_stderr_only", "D_empty"};
    for (int v = 0; v < 4; v++) {
        double t0 = now();
        for (int i = 0; i < n; i++) {
            double s = now();
            if (v <= 2) {
                (void) !write(errfd, msg, len);
                (void) !write(errfd, "\n", 1);
            }
            if (v <= 1) {
                int fd = open(snap, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0666);
                if (fd >= 0) {
                    (void) !write(fd, msg, len);
                    (void) !write(fd, "\n", 1);
                    if (v == 0) fsync(fd);
                    close(fd);
                }
            }
            lat[i] = now() - s;
        }
        double total = now() - t0;
        qsort(lat, n, sizeof(double), cmp);
        printf("%-14s n=%d total_s=%.3f per_emit_us mean=%.1f p50=%.1f p95=%.1f p99=%.1f max=%.1f "
               "max_emits_per_s=%.0f\n", name[v], n, total, total / n * 1e6, lat[n / 2] * 1e6,
               lat[(int) (n * 0.95)] * 1e6, lat[(int) (n * 0.99)] * 1e6, lat[n - 1] * 1e6,
               n / total);
    }
    return 0;
}
