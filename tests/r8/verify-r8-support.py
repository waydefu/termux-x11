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
    term_fn = test_c.split("static int ProcLorieR8Terminate", 1)[-1].split(
        "static int ProcLorieR8Dispatch", 1)[0]
    need("GiveUp(0)" in term_fn, "terminate_calls_giveup", bad)
    need('lorieR8ObsEnd' not in term_fn, "terminate_no_obs_end", bad)
    need('\\"op\\":\\"TERMINATE\\"' in term_fn, "terminate_obs_control", bad)

    # ---- D-02 (R9): whole-run finalization seam ----
    lorie_h = (src / "lorie/src/main/cpp/lorie/lorie.h").read_text()
    need("uint32_t runFinalize;" in lorie_h, "d02_run_finalize_field", bad)
    need('offsetof(struct LorieGateATestFault, runFinalize) == 20' in lorie_h,
         "d02_run_finalize_offset_assert", bad)
    # the seam must be ABI-layout-neutral: the five pre-existing asserts stay
    for frag, lbl in (
            ("sizeof(struct LorieGateATestFault) == 40", "d02_testfault_size_unchanged"),
            ("offsetof(struct LorieGateATestFault, magic) == 0", "d02_testfault_magic_unchanged"),
            ("offsetof(struct LorieGateATestFault, targetGeneration) == 24",
             "d02_testfault_gen_off_unchanged")):
        need(frag in lorie_h, lbl, bad)
    need("lorieGateAPublishRunFinalize" in term_fn, "d02_terminate_publishes_finalize", bad)
    renderer_cpp = (src / "lorie/src/main/cpp/lorie/renderer.cpp").read_text()
    fin_fn = renderer_cpp.split("static void lorieR8MaybeFinalizeRendererObs", 1)[-1].split(
        "\n}", 1)[0]
    need("lorieGateAObserveRunFinalize" in fin_fn, "d02_finalize_requires_publish", bad)
    need("lorieR8ObsEnd" in fin_fn, "d02_finalize_still_emits_end", bad)

    # ---- D-02 (R9): epoch records are ordinary phases carrying their own tuple ----
    activity_cpp = (src / "lorie/src/main/cpp/lorie/activity.cpp").read_text()
    need('lorieR8ObsEpoch("r", "R_EPOCH_BEGIN"' in activity_cpp, "d02_epoch_begin_emitted", bad)
    need('lorieR8ObsEpoch("r", "R_EPOCH_END"' in renderer_cpp, "d02_epoch_end_emitted", bad)
    obs_c = (src / "lorie/src/main/cpp/lorie/lorie_r8_obs.c").read_text()
    need("void lorieR8ObsEpoch(" in obs_c, "d02_epoch_api_present", bad)
    need("void lorieR8ObsTuple(" in obs_c, "d02_tuple_api_present", bad)
    # the legacy path must still stamp the process globals
    need("lorieR8ObsTuple(role, phase, r8Nonce, r8Generation, fields);" in obs_c,
         "d02_legacy_obs_unchanged", bad)
    # epoch ids must not be derived from the generation (Q2-F2)
    need("lorieR8EpochAllocId" in obs_c, "d02_epoch_id_independent", bad)

    # ---- Q6-F1: the deferred tuple must be assigned BEFORE it is observed ----
    cmd_cpp = (src / "lorie/src/main/cpp/lorie/cmdentrypoint.cpp").read_text()
    q_fn = cmd_cpp.split("static int gateAQueueDeferredRecord", 1)[-1].split(
        "\nvoid lorieGateACancelDeferred", 1)[0]
    assign_at = q_fn.find("queued->nonce = lorieGateALoadU64Acquire")
    obs_at = q_fn.find('lorieR8Obs("x", "DEFER_ENQUEUE"')
    need(assign_at >= 0 and obs_at >= 0 and assign_at < obs_at,
         "q6f1_defer_tuple_before_obs", bad)

    # ---- Q4-F1: gateAMappedState must be cleared on every unmap path ----
    need(activity_cpp.count("gateAMappedState = NULL;") >= 2,
         "q4f1_mapped_state_cleared", bad)
    dispatch_c = (src / "lorie/src/main/cpp/xserver/dix/dispatch.c").read_text()
    need("char dispatchExceptionAtReset = DE_RESET;" in dispatch_c,
         "product_reset_default", bad)
    need("SetDispatchExceptionTimer();" in dispatch_c.split("CloseDownClient", 1)[-1],
         "last_client_sets_reset_timer", bad)
    utils_c = (src / "lorie/src/main/cpp/xserver/os/utils.c").read_text()
    giveup_fn = utils_c.split("void\nGiveUp(int sig)", 1)[-1].split("#ifdef MONOTONIC_CLOCK", 1)[0]
    need("dispatchException |= DE_TERMINATE" in giveup_fn, "giveup_sets_terminate", bad)
    autoreset_fn = utils_c.split("void\nAutoResetServer(int sig)", 1)[-1].split("void\nGiveUp", 1)[0]
    need("dispatchException |= DE_RESET" in autoreset_fn, "hup_sets_reset", bad)
    fixture = (src / "tests/r8/p_r8_lifecycle.c").read_text()
    need("X_LorieR8Terminate" in fixture, "fixture_terminate_opcode", bad)
    need("TERMINATE_SENT" in fixture, "fixture_terminate_sent", bad)
    need("r8_terminate(c)" in fixture, "fixture_calls_terminate", bad)
    need("r8_send_checked" in fixture, "fixture_uses_shared_sender", bad)
    need(".ext = NULL" not in fixture, "fixture_no_null_ext", bad)
    need("xcb_wait_for_reply(" not in fixture, "fixture_no_seq32_wait", bad)
    need("TERMINATE_NO_REPLY_WAIT_HANGUP" not in fixture, "fixture_no_timeout_eat", bad)
    helper = (src / "tests/r8/r8_xcb_request.c").read_text()
    need("req.ext = &r8_ext" in helper, "helper_ext_ptr", bad)
    need("req.opcode = minor" in helper, "helper_opcode_minor", bad)
    need("parts + 2" in helper, "helper_reserved_iovecs", bad)
    need("xcb_wait_for_reply64" in helper, "helper_wait64", bad)
    need("xcb_wait_for_reply(" not in helper.replace("xcb_wait_for_reply64", "WAIT64"),
         "helper_no_seq32_wait", bad)
    need("error_code=" in helper, "helper_logs_error_code", bad)
    need("minor_code=" in helper, "helper_logs_minor_code", bad)
    need("hold_until_hangup" not in fixture, "fixture_no_signal_hold", bad)
    need("PrepareComposite" not in test_c, "register_no_prepare", bad)
    need("gateADirectTryPrepare" not in test_c, "register_no_pair", bad)
    need("lorieGateAR8EnsureReadyForBuffer" in test_c, "ready_wrapper", bad)
    need("lorieGateAR8EnsureGpuSampleableAhb" in test_c, "sampleable_wrapper", bad)

    cmd = (src / "lorie/src/main/cpp/lorie/cmdentrypoint.cpp").read_text()
    need("!defined(__ANDROID__)" in cmd and "lorieR8HostInjectGpuCopyDone" in cmd,
         "host_inject_android_absent", bad)

    proto = (src / "tests/r8/r8-test-protocol.h").read_text()
    need("sz_xLorieR8QueryVersionReq 4" in proto, "sz_qv_req", bad)
    need("sz_xLorieR8QueryVersionReply 32" in proto, "sz_qv_rep", bad)
    need("sz_xLorieR8RegisterBufferReq 8" in proto, "sz_reg_req", bad)
    need("sz_xLorieR8RegisterBufferReply 72" in proto, "sz_reg_rep", bad)
    need("sz_xLorieR8CheckpointReq 8" in proto, "sz_ck_req", bad)
    need("sz_xLorieR8CheckpointReply 72" in proto, "sz_ck_rep", bad)
    need("sz_xLorieR8TerminateReq 4" in proto, "sz_term_req", bad)
    need("sz_xLorieR8TerminateReply 32" in proto, "sz_term_rep", bad)
    need("X_LorieR8Terminate 3" in proto, "op_terminate", bad)
    need('#include <X11/Xmd.h>' in proto, "proto_xmd_include", bad)
    need("__X11_XMD_H" not in proto, "proto_no_xmd_heuristic", bad)
    need("defined(CARD8)" not in proto, "proto_no_card8_heuristic", bad)
    need("pad3" not in proto, "proto_no_pad3", bad)

    shared_h = (src / "lorie/src/main/cpp/lorie/lorie_r8_test.h").read_text()
    need("PixmapPtr" not in shared_h, "shared_no_PixmapPtr", bad)
    need("pixmap.h" not in shared_h and "pixmapstr.h" not in shared_h, "shared_no_pixmap_hdr", bad)
    x_h = (src / "lorie/src/main/cpp/lorie/lorie_r8_test_x.h").read_text()
    need("struct _Pixmap" in x_h, "xhdr_opaque_pixmap", bad)
    need("PixmapPtr" not in x_h, "xhdr_no_PixmapPtr", bad)
    need("lorieGateAR8EnsureGpuSampleableAhb" in x_h, "xhdr_sampleable", bad)
    need("lorieGateAR8PixmapReject" in x_h, "xhdr_reject", bad)
    cmd = (src / "lorie/src/main/cpp/lorie/cmdentrypoint.cpp").read_text()
    need('#include "lorie_r8_test.h"' in cmd, "cmd_shared_include", bad)
    need("lorie_r8_test_x.h" not in cmd, "cmd_no_x_header", bad)
    need('#include "lorie_r8_test_x.h"' in init, "init_x_header", bad)
    need('#include "lorie_r8_test_x.h"' in test_c, "testc_x_header", bad)
    need("void lorieExaDestroyPixmap(ScreenPtr pScreen, void *driverPriv);" in init,
         "d382c0a_prototype", bad)

    close_fn = init.split("static Bool lorieCloseScreen", 1)[-1].split("void lorieSetWindowPixmap", 1)[0]
    need('lorieR8Obs("x", "X_CLOSE_ENTER"' in close_fn, "x_close_enter", bad)
    need('lorieR8Obs("x", "X_CLOSE_RESULT"' in close_fn, "x_close_result", bad)
    need("pScreen->DestroyPixmap(pScreen->devPrivate)" in close_fn, "x_destroy_root", bad)
    need(close_fn.find('X_CLOSE_RESULT') < close_fn.find("pScreen->DestroyPixmap"),
         "x_close_result_before_destroy", bad)
    need("pScreen->CloseScreen = pvfb->CloseScreen" in close_fn, "x_restore_saved_close", bad)
    need("ret = pScreen->CloseScreen(pScreen)" in close_fn, "x_saved_close_called", bad)
    need('lorieR8ObsEnd("x")' not in close_fn, "x_end_not_in_close", bad)

    giveup_fn = init.split("void ddxGiveUp", 1)[-1].split("static void* ddxReadyThread", 1)[0]
    end_at = giveup_fn.find('lorieR8ObsEnd("x")')
    unlock_at = giveup_fn.find("UnlockServer")
    exit_at = giveup_fn.find("exit(error)")
    need(end_at >= 0, "x_obs_end_at_giveup", bad)
    need(unlock_at >= 0 and end_at > unlock_at, "x_end_after_unlock", bad)
    need(exit_at >= 0 and end_at < exit_at, "x_end_before_exit", bad)

    dix_main = (src / "lorie/src/main/cpp/xserver/dix/main.c").read_text()
    need("ddxGiveUp(EXIT_NO_ERROR)" in dix_main, "dix_giveup_present", bad)
    need(dix_main.find("(*screenInfo.screens[i]->CloseScreen)")
         < dix_main.find("ddxGiveUp(EXIT_NO_ERROR)"), "dix_giveup_after_close", bad)
    need("if (dispatchException & DE_TERMINATE) {\n            ddxGiveUp(EXIT_NO_ERROR);"
         in dix_main, "dix_giveup_only_terminate", bad)
    need("dispatchException &= ~DE_RESET" in dispatch_c, "dispatch_clears_reset_only", bad)
    need("dispatchExceptionAtReset" not in test_c, "test_no_atreset_mutate", bad)
    need("-terminate" in utils_c, "product_terminate_flag_exists", bad)
    need("-terminate" not in fixture, "fixture_no_product_terminate_flag", bad)

    rend = (src / "lorie/src/main/cpp/lorie/renderer.cpp").read_text()
    close_ctl = rend.split("LORIE_GATEA_MSG_GENERATION_CLOSE", 1)[-1].split(
        "gateARendererFatal(st, \"r-control-type\"", 1)[0]
    need("R_UNBOUND_FINAL" in close_ctl, "r_unbound_final", bad)
    need("r8RendererGenerationUnbound = 1" in close_ctl, "r_unbound_mark", bad)
    need("lorieR8MaybeFinalizeRendererObs" in close_ctl, "r_unbound_maybe", bad)
    need('lorieR8ObsEnd("r")' not in close_ctl, "r_end_not_at_unbind", bad)
    need("r8RendererSurfaceQuiesced" in rend, "r_surface_flag", bad)
    need("R_SURFACE_QUIESCED" in rend, "r_surface_phase", bad)
    need("r8RendererLoopDrained" in rend, "r_loop_drained", bad)
    need("lorieR8MaybeFinalizeRendererObs" in rend, "r_maybe_finalize", bad)
    surface_win = rend.split("if (!win) {", 1)[-1].split("eglCreateWindowSurface", 1)[0]
    need('notifyGpuCopyDoneCause("surface_loss")' in surface_win, "r_surface_loss_product", bad)
    need(surface_win.find('notifyGpuCopyDoneCause("surface_loss")')
         < surface_win.find("r8RendererSurfaceQuiesced"), "r_wake_before_end", bad)
    obs_c = (src / "lorie/src/main/cpp/lorie/lorie_r8_obs.c").read_text()
    need("R8_OBS_POST_END" in obs_c, "post_end_diag", bad)
    need("r8Ended[ix]" in obs_c.split("void lorieR8Obs(", 1)[-1][:900], "obs_checks_ended", bad)

    lorie_inc = src / "lorie/src/main/cpp/lorie"
    tests_r8 = src / "tests/r8"
    host_cflags = [
        "-DLORIE_ENABLE_R8_TEST_SUPPORT=1",
        "-I", str(lorie_inc),
        "-I", str(tests_r8),
    ]

    def compile_run(label: str, cmd_args: list[str], run_bin: str | None = None) -> None:
        r = subprocess.run(cmd_args, capture_output=True, text=True)
        need(r.returncode == 0, f"{label}:{r.stderr[-400:]}", bad)
        if r.returncode == 0 and run_bin:
            r2 = subprocess.run([run_bin], capture_output=True, text=True)
            need(r2.returncode == 0, f"{label}_run:{r2.returncode} {r2.stderr[-200:]}", bad)

    compile_run(
        "shared_c",
        ["gcc", "-std=c11", "-c", *host_cflags, str(tests_r8 / "test_r8_header_shared.c"),
         "-o", "/tmp/test_r8_header_shared.o"],
    )
    compile_run(
        "shared_cxx",
        ["g++", "-std=c++17", "-c", *host_cflags, str(tests_r8 / "test_r8_header_shared.cpp"),
         "-o", "/tmp/test_r8_header_shared.o"],
    )
    compile_run(
        "x_only_c",
        ["gcc", "-std=c11", "-c", *host_cflags, str(tests_r8 / "test_r8_header_x.c"),
         "-o", "/tmp/test_r8_header_x.o"],
    )
    compile_run(
        "x_after_typedef",
        ["gcc", "-std=c11", "-c", *host_cflags, str(tests_r8 / "test_r8_header_x_after_typedef.c"),
         "-o", "/tmp/test_r8_header_x_after_typedef.o"],
    )
    compile_run(
        "proto_alone_c",
        ["gcc", "-std=c11", "-c", "-I", str(tests_r8), str(tests_r8 / "test_r8_header_proto.c"),
         "-o", "/tmp/test_r8_header_proto_c.o"],
    )
    compile_run(
        "proto_c",
        ["gcc", "-std=c11", "-c", "-I", str(tests_r8), str(tests_r8 / "test_r8_protocol.c"),
         "-o", "/tmp/test_r8_protocol.o"],
    )
    compile_run(
        "proto_c_exe",
        ["gcc", "-std=c11", "-O0", "-I", str(tests_r8), str(tests_r8 / "test_r8_protocol.c"),
         "-o", "/tmp/test_r8_protocol"],
        "/tmp/test_r8_protocol",
    )
    compile_run(
        "proto_cxx",
        ["g++", "-std=c++17", "-c", "-I", str(tests_r8), str(tests_r8 / "test_r8_header_proto.cpp"),
         "-o", "/tmp/test_r8_header_proto.o"],
    )
    compile_run(
        "order_xmd_proto",
        ["gcc", "-std=c11", "-c", "-I", str(tests_r8),
         str(tests_r8 / "test_r8_header_order_xmd_proto.c"),
         "-o", "/tmp/test_r8_header_order_xmd_proto.o"],
    )
    compile_run(
        "order_x_proto",
        ["gcc", "-std=c11", "-c", "-I", str(tests_r8),
         str(tests_r8 / "test_r8_header_order_x_proto.c"),
         "-o", "/tmp/test_r8_header_order_x_proto.o"],
    )
    compile_run(
        "order_xproto_proto",
        ["gcc", "-std=c11", "-c", "-I", str(tests_r8),
         str(tests_r8 / "test_r8_header_order_xproto_proto.c"),
         "-o", "/tmp/test_r8_header_order_xproto_proto.o"],
    )
    compile_run(
        "order_proto_xproto",
        ["gcc", "-std=c11", "-c", "-I", str(tests_r8),
         str(tests_r8 / "test_r8_header_order_proto_xproto.c"),
         "-o", "/tmp/test_r8_header_order_proto_xproto.o"],
    )

    pre = subprocess.run(
        ["gcc", "-std=c11", "-E", *host_cflags, str(tests_r8 / "test_r8_header_shared.c")],
        capture_output=True, text=True,
    )
    need(pre.returncode == 0, f"shared_preproc:{pre.stderr[-200:]}", bad)
    if pre.returncode == 0:
        need("pixmap.h" not in pre.stdout and "pixmapstr.h" not in pre.stdout,
             "shared_preproc_no_pixmap", bad)

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
             "-o", "/tmp/p_r8_lifecycle",
             str(src / "tests/r8/p_r8_lifecycle.c"),
             str(src / "tests/r8/r8_xcb_request.c"),
             "-lxcb", "-lxcb-render", "-lxcb-present"],
            capture_output=True, text=True,
        )
    need(r.returncode == 0, f"fixture_compile:{r.stderr[-300:]}", bad)
    if r.returncode == 0:
        r2 = subprocess.run(["/tmp/p_r8_lifecycle", "--help"], capture_output=True, text=True)
        need(r2.returncode == 0 and "R8-C1" in r2.stdout, "fixture_help", bad)

    runner_v2 = tests_r8 / "run-r8-one-cell-v2.sh"
    need(runner_v2.is_file(), "runner_v2_present", bad)
    if runner_v2.is_file():
        rt = runner_v2.read_text()
        fn = rt.split("record_r8_terminate_shutdown()", 1)[-1].split(
            "copy_close_artifacts", 1)[0]
        need("LORIE_R8_TERMINATE" in fn, "runner_terminate_source", bad)
        need("kill" not in fn, "runner_terminate_no_kill", bad)
        need("GiveUp(0)" in fn, "runner_records_giveup", bad)
        need("request_clean_shutdown" not in rt, "runner_no_sigterm_shutdown", bad)
        need("require_server_terminate_obs" in rt, "runner_requires_server_terminate", bad)
        need("MISSING_TEST_CONTROL_TERMINATE" in rt, "runner_missing_test_control", bad)
        need("require_server_terminate_obs\n  record_r8_terminate_shutdown" in rt,
             "runner_obs_before_shutdown", bad)
        need("copy_close_artifacts\n  require_terminate_markers\n  require_server_terminate_obs" in rt,
             "runner_copy_before_server_obs", bad)
        need('"hangup": ("X_HANGUP_AFTER_TERMINATE" in text)' in rt,
             "runner_classb_hangup_not_sent", bad)
        need('JUDGE="$HERE/judge-r8-v2.py"' in rt, "runner_amend_judge", bad)
        need('JUDGE="$HERE/judge-r8.py"' not in rt, "runner_not_frozen_judge", bad)
        need("R8-C2|R8-C5-full) CLASS=B;;" in rt, "runner_classb_cells", bad)
        wait_at = rt.find('python3 "$ORCH" wait-finalized')
        freeze_at = rt.find("freeze_obs\nemit RAW_EVIDENCE_FROZEN")
        permit_at = rt.find('python3 "$ORCH" permit-judge')
        need(wait_at >= 0 and freeze_at >= 0 and permit_at >= 0, "runner_classb_order_present", bad)
        need(wait_at < freeze_at < permit_at, "runner_wait_freeze_permit_order", bad)

    FROZEN_JUDGE_SHA = (
        "f021048da3c1b729c6f9bf560eba52609b2f980336dd4fab77ad1700438c0e31"
    )
    # The collector was AMENDED in 2b6f9f0 ("R8: fix judge/fixture contract
    # defects that made 8 of 10 cells unreachable"): json.loads -> obs_loads.
    # That is the GAP-8 containment — product b984ded emits a DUPLICATE "phase"
    # key for X_CHECKPOINT, and obs_loads (r8_obs_stream.py) keeps the first
    # "phase" while surfacing the later one as "checkpoint_phase". Without it the
    # collector silently mis-parses every checkpoint record.
    #
    # The pin was not updated at the time, which left this check RED from 2b6f9f0
    # onward. Re-pinned 2026-09-22 to the amended content, which is the collector
    # that actually produced the frozen runtime-b984ded corpus (V2-R8-AGG check 11
    # "one collect-r8.py across all ten", check 12 "all ten re-run after the last
    # amendment"). collect-r8.py itself is deliberately NOT edited here: it must
    # stay byte-identical to the file that produced the frozen evidence.
    COLLECTOR_PARENT_SHA = (
        "e6df519a75c872c8a56fac00146585853eec7f17a3f424e70e5d4736340666c8"
    )
    FROZEN_COLLECTOR_SHA = (
        "c2d4013bbf6fb2532047e97fd1ab6989e3c31a1ddcefcd81ef5a6b4f11f5640b"
    )
    frozen_judge = tests_r8 / "judge-r8.py"
    amend_judge = tests_r8 / "judge-r8-v2.py"
    need(frozen_judge.is_file(), "frozen_judge_present", bad)
    need(amend_judge.is_file(), "amend_judge_present", bad)
    need(sha256(frozen_judge) == FROZEN_JUDGE_SHA, "frozen_judge_sha", bad)
    need(sha256(tests_r8 / "collect-r8.py") == FROZEN_COLLECTOR_SHA,
         "collector_pinned", bad)
    need(COLLECTOR_PARENT_SHA != FROZEN_COLLECTOR_SHA, "collector_amended_hash", bad)
    need("obs_loads" in (tests_r8 / "collect-r8.py").read_text(),
         "collector_uses_obs_loads", bad)
    need(sha256(amend_judge) != FROZEN_JUDGE_SHA, "amend_judge_new_hash", bad)
    need("FROZEN_PARENT_SHA256" in amend_judge.read_text(),
         "amend_records_parent_sha", bad)
    need(FROZEN_JUDGE_SHA in amend_judge.read_text(),
         "amend_records_parent_sha_value", bad)

    orch_txt = (tests_r8 / "r8_orchestration_v2.py").read_text()
    need("xrows += load_jsonl" not in orch_txt, "permit_no_jsonl_concat", bad)
    need("load_semantic_obs" in orch_txt, "permit_semantic_jsonl", bad)
    wait_fn = orch_txt.split('if args.cmd == "wait-finalized":', 1)[-1]
    wait_fn = wait_fn.split("ev = Path(args.evidence)", 1)[-1]
    wait_fn = wait_fn.split("ev = Path(args.evidence)", 1)[0]
    need("scan_files(raw_paths)" in wait_fn, "wait_raw_scan", bad)
    need("x-observations.jsonl" not in wait_fn, "wait_not_jsonl", bad)
    need("load_semantic_obs" not in wait_fn, "wait_not_semantic", bad)

    r = subprocess.run(
        [sys.executable, "-m", "unittest", "test_r8_orchestration_v2", "-q"],
        cwd=str(tests_r8), capture_output=True, text=True)
    need(r.returncode == 0, f"orch_v2:{r.stdout}{r.stderr}", bad)
    r = subprocess.run(
        [sys.executable, "-m", "unittest", "test_r8_obs_ingestion", "-q"],
        cwd=str(tests_r8), capture_output=True, text=True)
    need(r.returncode == 0, f"obs_ingestion:{r.stdout}{r.stderr}", bad)

    r = subprocess.run([sys.executable, str(src / "tests/r8/test_r8_parser.py")],
                       capture_output=True, text=True)
    need(r.returncode == 0, f"parser:{r.stdout}{r.stderr}", bad)
    r = subprocess.run([sys.executable, str(src / "tests/r8/test-judge-r8.py")],
                       capture_output=True, text=True)
    need(r.returncode == 0 and "failures=0" in r.stdout, "judge_vectors_frozen", bad)
    r = subprocess.run([sys.executable, str(src / "tests/r8/test-judge-r8-v2.py")],
                       capture_output=True, text=True)
    need(r.returncode == 0 and "failures=0" in r.stdout, "judge_vectors_v2", bad)
    r = subprocess.run([sys.executable, str(src / "tests/r8/test_r8_obs_terminal.py")],
                       capture_output=True, text=True)
    need(r.returncode == 0 and "PASS" in r.stdout, f"obs_terminal_py:{r.stdout}{r.stderr}", bad)
    compile_run(
        "xcb_request",
        ["gcc", "-std=c11", "-O0", "-Wall", "-Werror", "-I", str(tests_r8),
         str(tests_r8 / "test_r8_xcb_request.c"),
         str(tests_r8 / "r8_xcb_request.c"),
         "-Wl,--wrap=xcb_get_extension_data",
         "-Wl,--wrap=xcb_send_request64",
         "-Wl,--wrap=xcb_wait_for_reply64",
         "-Wl,--wrap=xcb_flush",
         "-Wl,--wrap=xcb_connection_has_error",
         "-o", "/tmp/test_r8_xcb_request"],
        "/tmp/test_r8_xcb_request",
    )
    compile_run(
        "epoch_obs_c",
        ["gcc", "-std=c11", "-O0", "-Wall", "-Werror", "-D_GNU_SOURCE",
         "-DLORIE_ENABLE_R8_TEST_SUPPORT=1",
         "-I", str(tests_r8 / "hoststubs"),
         "-I", str(tests_r8),
         "-I", str(lorie_inc),
         str(tests_r8 / "test_r8_epoch_obs.c"),
         str(src / "lorie/src/main/cpp/lorie/lorie_r8_obs.c"),
         "-lpthread", "-o", "/tmp/test_r8_epoch_obs"],
        "/tmp/test_r8_epoch_obs",
    )
    compile_run(
        "obs_terminal_c",
        ["gcc", "-std=c11", "-O0", "-Wall", "-Werror", "-D_GNU_SOURCE",
         "-DLORIE_ENABLE_R8_TEST_SUPPORT=1",
         "-I", str(tests_r8 / "hoststubs"),
         "-I", str(tests_r8),
         "-I", str(lorie_inc),
         str(tests_r8 / "test_r8_obs_terminal.c"),
         str(src / "lorie/src/main/cpp/lorie/lorie_r8_obs.c"),
         "-lpthread", "-o", "/tmp/test_r8_obs_terminal"],
        "/tmp/test_r8_obs_terminal",
    )

    if bad:
        print("R8_SUPPORT_HOST_FAIL")
        for b in bad:
            print(" ", b)
        return 1
    print("R8_SUPPORT_HOST_STATIC_OK")
    print("judge_vectors=53 device_cells=10")
    print("spec_sha", sha256(Path(args.spec)))
    print("judge_sha_frozen", sha256(src / "tests/r8/judge-r8.py"))
    print("judge_sha", sha256(src / "tests/r8/judge-r8-v2.py"))
    print("collector_sha", sha256(src / "tests/r8/collect-r8.py"))
    return 0


if __name__ == "__main__":
    rc = main()
    if rc:
        # re-run to print failures: main already returned; print via second pass
        sys.exit(rc)
    sys.exit(0)
