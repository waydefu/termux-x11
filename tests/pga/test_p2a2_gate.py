#!/usr/bin/env python3
"""PGA-GAP-1 host test: p2a2_emit() must emit NOTHING unless TERMUX_X11_P2A_DIAG is
exactly "1", and must behave exactly as before when it is.

The function is extracted verbatim from lorie/src/main/cpp/patches/dix-config.h.in and
compiled on the host with counting stubs for every side effect (liblog, open, write,
fsync, close). No product code is re-implemented here.

    python3 tests/pga/test_p2a2_gate.py              the working tree
    python3 tests/pga/test_p2a2_gate.py --rev dc94485 a git revision (the red control:
                                                     dc94485 has no gate and MUST fail)
exit 0 = every case as expected.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REL = "lorie/src/main/cpp/patches/dix-config.h.in"

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
static int n_log, n_open, n_write, n_fsync, n_close;
#define ANDROID_LOG_INFO 4
static int __android_log_print(int p, const char *t, const char *f, ...) { (void)p; (void)t; (void)f; n_log++; return 0; }
static int stub_open(const char *p, int fl, ...) { (void)p; (void)fl; n_open++; return 99; }
static ssize_t stub_write(int fd, const void *b, size_t n) { (void)fd; (void)b; n_write++; return (ssize_t)n; }
static int stub_fsync(int fd) { (void)fd; n_fsync++; return 0; }
static int stub_close(int fd) { (void)fd; n_close++; return 0; }
#define open stub_open
#define write stub_write
#define fsync stub_fsync
#define close stub_close
/* ---- extracted from dix-config.h.in ---- */
@@FUNCS@@
/* ---- end ---- */
int main(int argc, char **argv) {
    const char *msg = argc > 1 && strcmp(argv[1], "NULL") == 0 ? NULL : "Sprep pix=0x1 index=0";
    p2a2_emit(msg);
    p2a2_emit(msg);
    printf("%d %d %d %d %d\n", n_log, n_open, n_write, n_fsync, n_close);
    return 0;
}
'''


def extract(text: str) -> str:
    """Every static helper from the P2-A.2/A.3 block through the end of p2a2_emit."""
    start = text.find("P2-A.2/A.3 observe-only")
    if start < 0:
        raise SystemExit("anchor 'P2-A.2/A.3 observe-only' not found")
    body = text[start:]
    m = re.search(r"static void p2a2_emit\(const char \*msg\)\n\{.*?\n\}\n", body, re.S)
    if not m:
        raise SystemExit("p2a2_emit definition not found")
    block = body[:m.end()]
    # drop the preprocessor lines and the setuid/setgid macros: the harness supplies
    # its own includes, and the macros would break the host libc headers
    keep = [ln for ln in block.splitlines()
            if not ln.startswith("#include") and not ln.startswith("#define set")]
    return "\n".join(keep[1:])      # first line is the tail of the comment


def run_case(binary: Path, env_val, null_msg=False):
    env = {k: v for k, v in os.environ.items() if k != "TERMUX_X11_P2A_DIAG"}
    if env_val is not None:
        env["TERMUX_X11_P2A_DIAG"] = env_val
    out = subprocess.run([str(binary)] + (["NULL"] if null_msg else []), env=env,
                         capture_output=True, text=True, check=True).stdout.split()
    return tuple(int(x) for x in out)


def main() -> int:
    rev = sys.argv[sys.argv.index("--rev") + 1] if "--rev" in sys.argv else None
    if rev:
        text = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{REL}"],
                              capture_output=True, text=True, check=True).stdout
    else:
        text = (REPO / REL).read_text()
    src = HARNESS.replace("@@FUNCS@@", extract(text))
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "h.c"
        b = Path(td) / "h"
        c.write_text(src)
        r = subprocess.run(["cc", "-O0", "-Wall", "-o", str(b), str(c)], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr)
            return 2
        OFF = (0, 0, 0, 0, 0)
        ON2 = (2, 2, 8, 2, 2)      # two emits: 2 liblog, 2 open, (2 stderr + 2 snap) x2, 2 fsync, 2 close
        cases = [("unset", None, False, OFF), ("0", "0", False, OFF), ("empty", "", False, OFF),
                 ("true", "true", False, OFF), ("11", "11", False, OFF), ("space1", " 1", False, OFF),
                 ("1space", "1 ", False, OFF), ("1", "1", False, ON2), ("1+NULL", "1", True, OFF)]
        bad = 0
        for name, val, nul, want in cases:
            got = run_case(b, val, nul)
            ok = got == want
            bad += not ok
            print(f"{'OK  ' if ok else 'FAIL'} {name:8s} got={got} want={want}")
    print(f"P2A2_GATE {'PASS' if not bad else 'FAIL'} rev={rev or 'worktree'} failed={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
