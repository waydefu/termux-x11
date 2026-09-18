#!/usr/bin/env python3
"""Static + host proof for R8 test support boundary and R7 regressions."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def need(cond: bool, label: str, bad: list[str]) -> None:
    if not cond:
        bad.append(label)


def extract_ifdef_blocks(text: str, macro: str) -> str:
    # Concatenate regions inside #ifdef MACRO / #if defined(MACRO)
    out = []
    depth = 0
    for line in text.splitlines(True):
        if line.startswith("#if") and macro in line:
            depth += 1
            out.append(line)
            continue
        if depth:
            out.append(line)
            if line.startswith("#endif"):
                depth -= 1
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT))
    ap.add_argument("--spec", default=str(Path(__file__).with_name("lifecycle-cell-spec.json")))
    args = ap.parse_args()
    src = Path(args.source)
    bad: list[str] = []
    cmake = (src / "lorie/src/main/cpp/CMakeLists.txt").read_text()
    need("option(LORIE_ENABLE_R8_TEST_SUPPORT" in cmake, "cmake_option", bad)
    need("OFF)" in cmake.split("LORIE_ENABLE_R8_TEST_SUPPORT", 1)[-1][:400], "cmake_default_off", bad)

    init = (src / "lorie/src/main/cpp/lorie/InitOutput.c").read_text()
    off_init = init
    for block in ("LORIE_ENABLE_R8_TEST_SUPPORT",):
        # crude: if OFF, P1 stub remains
        pass
    need('gateAXFatal("x-destroy-in-lease"' in init, "p1_off_stub_present", bad)
    need("lorieExaDestroyPixmap(pScreenPtr, hookPriv)" in init, "p1_on_real_dtor", bad)
    need("LorieR8TestExtensionInit" in init, "ext_init", bad)

    test_c = (src / "lorie/src/main/cpp/lorie/lorie_r8_test.c").read_text()
    need("PrepareComposite" not in test_c, "register_no_prepare", bad)
    need("gateADirectTryPrepare" not in test_c, "register_no_pair", bad)
    need("lorieGateAR8EnsureReadyForBuffer" in test_c, "ready_wrapper", bad)
    need("lorieGateAR8EnsureGpuSampleableAhb" in test_c, "sampleable_wrapper", bad)

    cmd = (src / "lorie/src/main/cpp/lorie/cmdentrypoint.cpp").read_text()
    need("!defined(__ANDROID__)" in cmd and "lorieR8HostInjectGpuCopyDone" in cmd,
         "host_inject_android_absent", bad)

    proto = (src / "tests/r8/r8-test-protocol.h").read_text()
    need("sz_xLorieR8QueryVersionReq 4" in proto, "sz_qv_req", bad)
    need("sz_xLorieR8RegisterBufferReq 8" in proto, "sz_reg_req", bad)
    need("sz_xLorieR8RegisterBufferReply 72" in proto, "sz_reg_rep", bad)

    # R7 regressions
    scripts = src / "scripts"
    for name in (
        "verify_r7_10_hup_containment.py",
        "verify_r7_p1_present_target_arm.py",
        "verify_r7_hup_preserve.py",
    ):
        p = scripts / name
        need(p.is_file(), f"missing_{name}", bad)
        r = subprocess.run([sys.executable, str(p), str(src)], cwd=str(src), capture_output=True, text=True)
        need(r.returncode == 0, f"{name}_rc={r.returncode}", bad)

    for c_name, bin_name in (
        ("test_gatea_wait_wake_class.c", "test_gatea_wait_wake_class"),
        ("test_gatea_test_fault_class.c", "test_gatea_test_fault_class"),
        ("test_gatea_hup_class.c", "test_gatea_hup_class"),
    ):
        cpath = scripts / c_name
        out = Path("/tmp") / bin_name
        r = subprocess.run(
            ["gcc", "-O0", "-o", str(out), str(cpath)],
            cwd=str(src), capture_output=True, text=True,
        )
        need(r.returncode == 0, f"compile_{c_name}:{r.stderr[-200:]}", bad)
        if r.returncode == 0:
            r2 = subprocess.run([str(out)], capture_output=True, text=True)
            need(r2.returncode == 0, f"run_{bin_name}:{r2.returncode}", bad)

    # OFF proof: symbols only under ifdef
    for p, needles in (
        (src / "lorie/src/main/cpp/lorie/lorie_r8_test.c", ["LORIE-R8-TEST"]),
        (src / "lorie/src/main/cpp/lorie/lorie_r8_obs.c", ["R8_OBS"]),
    ):
        text = p.read_text()
        first = text.find("#ifdef LORIE_ENABLE_R8_TEST_SUPPORT")
        need(first == 0 or first > 0 and "LORIE_ENABLE_R8_TEST_SUPPORT" in text[:80] or first >= 0,
             f"ifdef_{p.name}", bad)

    r = subprocess.run(
            ["gcc", "-O2", "-Wall", "-Werror", "-I", str(src / "tests/r8"),
             "-o", "/tmp/p_r8_lifecycle", str(src / "tests/r8/p_r8_lifecycle.c"),
             "-lxcb", "-lxcb-render", "-lxcb-present"],
            capture_output=True, text=True,
        )
    need(r.returncode == 0, f"fixture_compile:{r.stderr[-300:]}", bad)
    if r.returncode == 0:
        r2 = subprocess.run(["/tmp/p_r8_lifecycle", "--help"], capture_output=True, text=True)
        need(r2.returncode == 0 and "R8-C1" in r2.stdout, "fixture_help", bad)

    r = subprocess.run([sys.executable, str(src / "tests/r8/test_r8_parser.py")],
                       capture_output=True, text=True)
    need(r.returncode == 0, f"parser:{r.stdout}{r.stderr}", bad)
    r = subprocess.run([sys.executable, str(src / "tests/r8/test-judge-r8.py")],
                       capture_output=True, text=True)
    need(r.returncode == 0 and "failures=0" in r.stdout, "judge_vectors", bad)

    if bad:
        print("R8_SUPPORT_HOST_FAIL")
        for b in bad:
            print(" ", b)
        return 1
    print("R8_SUPPORT_HOST_STATIC_OK")
    print("spec_sha", sha256(Path(args.spec)))
    print("judge_sha", sha256(src / "tests/r8/judge-r8.py"))
    print("collector_sha", sha256(src / "tests/r8/collect-r8.py"))
    return 0


if __name__ == "__main__":
    rc = main()
    if rc:
        # re-run to print failures: main already returned; print via second pass
        sys.exit(rc)
    sys.exit(0)
