/*
 * proftrace: LD_PRELOAD into the (bionic) proot tracer to count and time the libc calls it makes per ptrace stop
 * (2026-09-25, PROOT-STAT-COST-01). Wrapped: ptrace (split by request), waitpid/wait4, process_vm_readv/writev,
 * readlink(at), lstat/stat/fstatat, open(at), close, read, pread64, write. Dumped at exit to $PROFTRACE_OUT
 * (append) or stderr.
 *   clang -O2 -shared -fPIC -o libproftrace.so proftrace.c
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

enum { F_PTRACE, F_WAIT, F_VMREAD, F_VMWRITE, F_READLINK, F_LSTAT, F_STAT, F_FSTATAT, F_OPEN, F_CLOSE, F_READ, F_PREAD,
       F_WRITE, F_N };
static const char *names[F_N] = { "ptrace", "waitpid/wait4", "process_vm_readv", "process_vm_writev", "readlink(at)",
	"lstat", "stat", "fstatat", "open(at)", "close", "read", "pread64", "write" };
static unsigned long cnt[F_N], ns[F_N];
static unsigned long pt_cnt[0x4300], pt_ns[0x4300];   /* ptrace requests are < 0x4300 */

static inline unsigned long now(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (unsigned long) t.tv_sec * 1000000000UL + (unsigned long) t.tv_nsec;
}
#define REAL(ret, name, ...) static ret (*real_##name)(__VA_ARGS__); if (!real_##name) real_##name = dlsym(RTLD_NEXT, #name)
#define TIMED(idx, call) ({ unsigned long _t = now(); __typeof__(call) _r = (call); cnt[idx]++; ns[idx] += now() - _t; _r; })

long ptrace(int req, ...)
{
	va_list ap; pid_t pid; void *addr, *data; unsigned long t; long r;
	va_start(ap, req); pid = va_arg(ap, pid_t); addr = va_arg(ap, void *); data = va_arg(ap, void *); va_end(ap);
	REAL(long, ptrace, int, ...);
	t = now(); r = real_ptrace(req, pid, addr, data); t = now() - t;
	cnt[F_PTRACE]++; ns[F_PTRACE] += t;
	if (req >= 0 && req < 0x4300) { pt_cnt[req]++; pt_ns[req] += t; }
	return r;
}
pid_t waitpid(pid_t p, int *s, int o) { REAL(pid_t, waitpid, pid_t, int *, int); return TIMED(F_WAIT, real_waitpid(p, s, o)); }
pid_t wait4(pid_t p, int *s, int o, struct rusage *u) { REAL(pid_t, wait4, pid_t, int *, int, struct rusage *); return TIMED(F_WAIT, real_wait4(p, s, o, u)); }
ssize_t process_vm_readv(pid_t p, const struct iovec *l, unsigned long ln, const struct iovec *r, unsigned long rn, unsigned long f)
{ REAL(ssize_t, process_vm_readv, pid_t, const struct iovec *, unsigned long, const struct iovec *, unsigned long, unsigned long);
  return TIMED(F_VMREAD, real_process_vm_readv(p, l, ln, r, rn, f)); }
ssize_t process_vm_writev(pid_t p, const struct iovec *l, unsigned long ln, const struct iovec *r, unsigned long rn, unsigned long f)
{ REAL(ssize_t, process_vm_writev, pid_t, const struct iovec *, unsigned long, const struct iovec *, unsigned long, unsigned long);
  return TIMED(F_VMWRITE, real_process_vm_writev(p, l, ln, r, rn, f)); }
ssize_t readlink(const char *p, char *b, size_t n) { REAL(ssize_t, readlink, const char *, char *, size_t); return TIMED(F_READLINK, real_readlink(p, b, n)); }
ssize_t readlinkat(int d, const char *p, char *b, size_t n) { REAL(ssize_t, readlinkat, int, const char *, char *, size_t); return TIMED(F_READLINK, real_readlinkat(d, p, b, n)); }
int lstat(const char *p, struct stat *s) { REAL(int, lstat, const char *, struct stat *); return TIMED(F_LSTAT, real_lstat(p, s)); }
int stat(const char *p, struct stat *s) { REAL(int, stat, const char *, struct stat *); return TIMED(F_STAT, real_stat(p, s)); }
int fstatat(int d, const char *p, struct stat *s, int f) { REAL(int, fstatat, int, const char *, struct stat *, int); return TIMED(F_FSTATAT, real_fstatat(d, p, s, f)); }
int open(const char *p, int f, ...) { va_list ap; mode_t m; va_start(ap, f); m = (mode_t) va_arg(ap, int); va_end(ap);
  REAL(int, open, const char *, int, ...); return TIMED(F_OPEN, real_open(p, f, m)); }
int openat(int d, const char *p, int f, ...) { va_list ap; mode_t m; va_start(ap, f); m = (mode_t) va_arg(ap, int); va_end(ap);
  REAL(int, openat, int, const char *, int, ...); return TIMED(F_OPEN, real_openat(d, p, f, m)); }
int close(int fd) { REAL(int, close, int); return TIMED(F_CLOSE, real_close(fd)); }
ssize_t read(int fd, void *b, size_t n) { REAL(ssize_t, read, int, void *, size_t); return TIMED(F_READ, real_read(fd, b, n)); }
ssize_t pread64(int fd, void *b, size_t n, off64_t o) { REAL(ssize_t, pread64, int, void *, size_t, off64_t); return TIMED(F_PREAD, real_pread64(fd, b, n, o)); }
ssize_t write(int fd, const void *b, size_t n) { REAL(ssize_t, write, int, const void *, size_t); return TIMED(F_WRITE, real_write(fd, b, n)); }

/* the tracer passes its environment to the tracee: drop LD_PRELOAD once loaded so the glibc guest never sees this
 * bionic library (its libdl.so dependency does not exist there) */
static void dump(void);
static int dumped;
__attribute__((constructor)) static void init(void) { unsetenv("LD_PRELOAD"); fprintf(stderr, "PROFTRACE_LOADED pid=%d\n", getpid()); }
/* proot may leave through _exit(2): dump first */
void _exit(int status) { static void (*real__exit)(int); if (!real__exit) real__exit = dlsym(RTLD_NEXT, "_exit"); dump(); real__exit(status); __builtin_unreachable(); }

__attribute__((destructor)) static void dump(void)
{
	if (dumped++)
		return;
	const char *path = getenv("PROFTRACE_OUT");
	FILE *f = path ? fopen(path, "a") : NULL;
	if (f == NULL)
		f = stderr;
	int i;
	fprintf(f, "PROFTRACE pid=%d\n", getpid());
	for (i = 0; i < F_N; i++)
		if (cnt[i])
			fprintf(f, "  %-18s n=%-9lu total_ms=%-9.1f avg_us=%.2f\n", names[i], cnt[i], ns[i] / 1e6, ns[i] / 1e3 / cnt[i]);
	for (i = 0; i < 0x4300; i++)
		if (pt_cnt[i])
			fprintf(f, "  ptrace[0x%04x]     n=%-9lu total_ms=%-9.1f avg_us=%.2f\n", i, pt_cnt[i], pt_ns[i] / 1e6, pt_ns[i] / 1e3 / pt_cnt[i]);
	if (f != stderr)
		fclose(f);
}
