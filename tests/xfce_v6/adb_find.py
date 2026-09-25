#!/usr/bin/env python3
"""Find the phone's wireless-debugging endpoint without mDNS.
Under proot-fast, Python ifaddr/zeroconf fails (ctypes bypasses the getifaddrs shim), so:
  1. local IP = the source address of a UDP socket "connected" to a public address (no packet is sent);
  2. TCP-connect scan of that IP, ports 30000-50000 (Android picks the wireless-debugging port there).
Prints every open port as  OPEN <ip>:<port>  and nothing is connected to adb here; the caller runs
`adb connect` on the candidates (the pairing port is only open while the pairing dialog is shown).
    adb_find.py [--lo 30000] [--hi 50000] [--timeout 0.3]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import socket


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    finally:
        s.close()


def is_open(ip: str, port: int, timeout: float) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((ip, port)) == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", type=int, default=30000)
    ap.add_argument("--hi", type=int, default=50000)
    ap.add_argument("--timeout", type=float, default=0.3)
    a = ap.parse_args()
    ip = local_ip()
    print(f"LOCAL_IP {ip}")
    with cf.ThreadPoolExecutor(max_workers=256) as ex:
        futs = {ex.submit(is_open, ip, p, a.timeout): p for p in range(a.lo, a.hi + 1)}
        found = sorted(futs[f] for f in cf.as_completed(futs) if f.result())
    for p in found:
        print(f"OPEN {ip}:{p}")
    print(f"SCANNED {a.lo}-{a.hi} open={len(found)}")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
