/*
 * f8ifaddrs: getifaddrs(3) fallback for the Ubuntu guest under proot-fast (2026-09-25).
 *
 * Android denies this app's rtnetlink sockets; stock termux/proot emulates them, but that needs every
 * send/recv to stop for the tracer, which proot-fast stops doing (PROOT_BWRAP_COMPAT off). Without it
 * glibc getifaddrs() fails (EINVAL / EACCES) and so do Node's os.networkInterfaces(), Python ifaddr,
 * zeroconf, ...  This preload first calls the real getifaddrs(); only when that fails it builds the
 * list from ioctl(SIOCGIFCONF / SIOCGIFFLAGS / SIOCGIFNETMASK / SIOCGIFBRDADDR), which Android allows.
 * IPv4 only (no AF_INET6 / AF_PACKET entries). The whole list is one malloc block with the first
 * entry at its start, so glibc's freeifaddrs() (a plain free()) releases it.
 *
 *   cc -O2 -shared -fPIC -o libf8ifaddrs.so f8ifaddrs.c -ldl
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

struct entry {
	struct ifaddrs ifa;
	struct sockaddr_in addr, mask, brd;
	char name[IFNAMSIZ];
};

static int fallback(struct ifaddrs **ifap)
{
	struct ifreq reqs[64];
	struct ifconf ifc = { .ifc_len = sizeof(reqs), .ifc_req = reqs };
	struct entry *e;
	int fd, n, i;

	fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return -1;
	if (ioctl(fd, SIOCGIFCONF, &ifc) < 0) {
		int saved = errno;
		close(fd);
		errno = saved;
		return -1;
	}
	n = ifc.ifc_len / (int) sizeof(struct ifreq);
	if (n <= 0) {
		close(fd);
		*ifap = NULL;
		return 0;
	}
	e = calloc((size_t) n, sizeof(*e));
	if (e == NULL) {
		close(fd);
		errno = ENOMEM;
		return -1;
	}
	for (i = 0; i < n; i++) {
		struct ifreq r;

		memcpy(e[i].name, reqs[i].ifr_name, IFNAMSIZ);
		e[i].name[IFNAMSIZ - 1] = '\0';
		memcpy(&e[i].addr, &reqs[i].ifr_addr, sizeof(e[i].addr));
		e[i].ifa.ifa_name = e[i].name;
		e[i].ifa.ifa_addr = (struct sockaddr *) &e[i].addr;
		e[i].ifa.ifa_next = (i + 1 < n) ? &e[i + 1].ifa : NULL;

		memset(&r, 0, sizeof(r));
		memcpy(r.ifr_name, e[i].name, IFNAMSIZ);
		if (ioctl(fd, SIOCGIFFLAGS, &r) == 0)
			e[i].ifa.ifa_flags = (unsigned short) r.ifr_flags;
		memcpy(r.ifr_name, e[i].name, IFNAMSIZ);
		if (ioctl(fd, SIOCGIFNETMASK, &r) == 0) {
			memcpy(&e[i].mask, &r.ifr_netmask, sizeof(e[i].mask));
			e[i].mask.sin_family = AF_INET;
			e[i].ifa.ifa_netmask = (struct sockaddr *) &e[i].mask;
		}
		if (e[i].ifa.ifa_flags & IFF_BROADCAST) {
			memcpy(r.ifr_name, e[i].name, IFNAMSIZ);
			if (ioctl(fd, SIOCGIFBRDADDR, &r) == 0) {
				memcpy(&e[i].brd, &r.ifr_broadaddr, sizeof(e[i].brd));
				e[i].brd.sin_family = AF_INET;
				e[i].ifa.ifa_broadaddr = (struct sockaddr *) &e[i].brd;
			}
		}
	}
	close(fd);
	*ifap = &e[0].ifa;
	return 0;
}

int getifaddrs(struct ifaddrs **ifap)
{
	static int (*real)(struct ifaddrs **);
	int saved;

	if (real == NULL)
		real = (int (*)(struct ifaddrs **)) dlsym(RTLD_NEXT, "getifaddrs");
	if (real != NULL && real(ifap) == 0)
		return 0;
	saved = errno;
	if (fallback(ifap) == 0)
		return 0;
	errno = saved;
	return -1;
}
