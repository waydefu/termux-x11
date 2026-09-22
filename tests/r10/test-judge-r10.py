#!/usr/bin/env python3
"""Host vectors for judge-r10.py. No device, no product, no round consumed.

Every negative pins exactly ONE assertion, so a failure names the rule that broke.
The INVALID/FAIL split is tested deliberately: a round whose construction never
happened yields no information and must come out INVALID, never FAIL and never PASS.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUDGE = HERE / "judge-r10.py"
APK = "1bd8bef0909249737ea43acf0a35f3c995d941e1badbfcf370e5cd854f4bb8a3"

TOL = {
    "status": "R10_TOLERANCE_FROZEN_V1",
    "apk_sha256": APK,
    "tolerance": {"x.fd_count": 1, "x.maps_count": 16, "activity.fd_count": 8,
                  "activity.pss_kb": 21098},
}

STABLE = {"pid": 20881, "cmdline": "termux-x11 com.termux.x11 :1 -legacy-drawing",
          "versionName": "v", "versionCode": 15, "lastUpdateTime": "t"}


def counters(**over):
    c = {"summary_present": True, "where": "x-close-screen",
         "ring_overflow": 0, "nonce_at_dump": 0, "generation_at_dump": 0,
         "c_ahb_acquire": 8, "c_ahb_release": 8,
         "c_eglimage_create": 8, "c_eglimage_destroy": 8,
         "c_texture_create": 8, "c_texture_delete": 8,
         "c_x_registry_current": 0, "c_renderer_registry_current": 0,
         "c_lease_current": 0, "c_generation_fatal": 0, "c_generation_close": 1}
    c.update(over)
    return c


def sample(fd=88, maps=3004, act_fd=200, act_pss=63000):
    return {"x": {"pid": 1, "alive": True, "fd_count": fd, "maps_count": maps},
            "activity": {"pid": 2, "alive": True, "fd_count": act_fd,
                         "pss_kb": act_pss}}


def rnd(n, *, fd=88, act_pid=25809, nonce=None, gen=1, **cover):
    return {
        "round": n,
        "samples": {"B0": sample(fd=fd), "B1": sample(fd=fd), "B3": sample(fd=fd)},
        "counters_at_close": counters(**cover),
        "derived": {"x_registry_current": 0, "renderer_registry_current": 0,
                    "lease_current": 0, "direct_success_events": 8,
                    "source": "ring_events"},
        "session": {"session_nonce": nonce if nonce is not None else 1000 + n,
                    "session_generation": gen, "binds_seen": 1},
        "client_rc": 0, "x_alive_after_terminate": False, "activity_pid": act_pid,
    }


def ledger(mode="B", rounds=5, **kw):
    per = kw.pop("per_round", {})
    rs = []
    for i in range(1, rounds + 1):
        rs.append(rnd(i, **{**kw, **per.get(i, {})}))
    return {"schema_version": 1, "mode": mode, "rounds_declared": rounds,
            "source_sha": "dc94485", "apk_sha256": APK,
            "stable_before": STABLE, "stable_after": STABLE,
            "halts": [], "x11_unix_after": ["X1"], "rounds": rs}


def run(led, tol=None):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "l.json").write_text(json.dumps(led))
        (d / "t.json").write_text(json.dumps(tol or TOL))
        r = subprocess.run([sys.executable, str(JUDGE), "--ledger", str(d / "l.json"),
                            "--tolerance", str(d / "t.json")],
                           capture_output=True, text=True)
        return r.stdout.strip()


def eround(n, *, halt=None, xalive=False, actalive=False, nonce=None,
           gen=1, xreg=0, rreg=0, **cover):
    r = rnd(n, nonce=nonce if nonce is not None else 7000 + n, gen=gen, **cover)
    r["halts_in_round"] = [{"what": halt, "reason": 6}] if halt else []
    r["x_alive_after_ending"] = xalive
    r["activity_alive_after_ending"] = actalive
    r["derived"].update(x_registry_current=xreg, renderer_registry_current=rreg)
    return r


def eledger(**over):
    per = over.pop("per_round", {})
    base = {1: {"halt": "r-test-fatal-pre-fence", "xreg": 2, "rreg": 2},
            2: {"halt": "r-hup"}, 3: {}}
    rs = [eround(n, **{**base[n], **per.get(n, {})}) for n in (1, 2, 3)]
    l = ledger(mode="E", rounds=3)
    l["rounds"] = rs
    l["halts"] = [{"what": "r-test-fatal-pre-fence", "reason": 6},
                  {"what": "r-hup", "reason": 6}]
    l.update(over)
    return l


CASES = [
    ("V01", "clean B run", ledger(), "R10_PASS ACCEPT"),
    ("V02", "clean C run", (lambda: (lambda l: (l.update(mode="C"),
        [r.update(activity_pid=100 + r["round"]) for r in l["rounds"]], l)[-1])(ledger()))(),
        "R10_PASS ACCEPT"),

    # ---- class 1, counters. Exact, never "within tolerance". ----
    ("N01", "x registry not empty",
     ledger(per_round={3: {"c_x_registry_current": 2}}),
     "R10_FAIL r3_C_X_REGISTRY_CURRENT_IS_2_EXPECTED_0"),
    ("N02", "renderer registry not empty",
     ledger(per_round={1: {"c_renderer_registry_current": 1}}),
     "R10_FAIL r1_C_RENDERER_REGISTRY_CURRENT_IS_1_EXPECTED_0"),
    ("N03", "lease not released",
     ledger(per_round={5: {"c_lease_current": 1}}),
     "R10_FAIL r5_C_LEASE_CURRENT_IS_1_EXPECTED_0"),
    ("N04", "AHB unbalanced",
     ledger(per_round={2: {"c_ahb_release": 7}}), "R10_FAIL r2_AHB_UNBALANCED_8_vs_7"),
    ("N05", "EGLImage unbalanced",
     ledger(per_round={2: {"c_eglimage_destroy": 6}}),
     "R10_FAIL r2_EGLIMAGE_UNBALANCED_8_vs_6"),
    ("N06", "texture unbalanced",
     ledger(per_round={4: {"c_texture_delete": 0}}),
     "R10_FAIL r4_TEXTURE_UNBALANCED_8_vs_0"),
    ("N07", "counter not observed is INVALID, not a pass",
     ledger(per_round={1: {"c_lease_current": None}}),
     "R10_INVALID r1_COUNTER_NOT_OBSERVED_c_lease_current"),
    ("N08", "no dump at close",
     ledger(per_round={1: {"summary_present": False}}), "R10_INVALID r1_NO_DUMP_AT_CLOSE"),
    ("N09", "not a clean close",
     ledger(per_round={1: {"where": "x-hup"}}),
     "R10_INVALID r1_NOT_A_CLEAN_CLOSE_where=x-hup"),
    ("N10", "close did not zero the tuple",
     ledger(per_round={2: {"nonce_at_dump": 77}}),
     "R10_FAIL r2_CLOSE_DID_NOT_ZERO_TUPLE 77,0"),

    # ---- D-06 at runtime ----
    ("N11", "generation 2 reopens D-06", ledger(gen=2),
     "R10_FAIL r1_GENERATION_IS_2_D06_REOPENED"),
    ("N12", "generation unobserved is null, not 2", ledger(gen=None), "R10_PASS ACCEPT"),

    # ---- mode premises ----
    ("N13", "B requires ONE surviving Activity",
     (lambda l: (l["rounds"][2].update(activity_pid=999), l)[-1])(ledger()),
     "R10_INVALID ACTIVITY_DID_NOT_SURVIVE_[25809, 25809, 999, 25809, 25809]"),
    ("N14", "session nonce reused",
     (lambda l: (l["rounds"][1]["session"].update(session_nonce=1001), l)[-1])(ledger()),
     "R10_FAIL SESSION_NONCE_REUSED_[1001, 1001, 1003, 1004, 1005]"),

    # ---- class 4, residue and redlines ----
    ("N15", "stable changed",
     (lambda l: (l.update(stable_after={**STABLE, "pid": 1}), l)[-1])(ledger()),
     "R10_FAIL STABLE_CHANGED"),
    ("N16", "X socket residue",
     (lambda l: (l.update(x11_unix_after=["X1", "X3"]), l)[-1])(ledger()),
     "R10_FAIL X_SOCKET_RESIDUE_X3"),
    ("N17", "unexpected fatal",
     (lambda l: (l.update(halts=[{"what": "r-hup", "reason": 6}]), l)[-1])(ledger()),
     "R10_FAIL UNEXPECTED_FATAL_r-hup_6"),
    ("N18", "client failed -> INVALID, not FAIL",
     (lambda l: (l["rounds"][0].update(client_rc=1), l)[-1])(ledger()),
     "R10_INVALID r1_CLIENT_RC_1"),
    ("N19", "X survived its own terminate",
     (lambda l: (l["rounds"][0].update(x_alive_after_terminate=True), l)[-1])(ledger()),
     "R10_FAIL r1_X_SURVIVED_TERMINATE"),

    # ---- class 3, trend ----
    ("N20", "monotonic fd growth beyond tolerance is a LEAK",
     ledger(per_round={i: {"fd": 88 + i} for i in range(1, 6)}),
     "R10_FAIL LEAK_x.fd_count@B0,x.fd_count@B1,x.fd_count@B3"),
    ("N21", "rise then flat is not a leak",
     ledger(per_round={1: {"fd": 88}, 2: {"fd": 95}, 3: {"fd": 95},
                       4: {"fd": 95}, 5: {"fd": 95}}), "R10_PASS ACCEPT"),
    ("N22", "movement inside tolerance is noise",
     ledger(per_round={1: {"fd": 88}, 2: {"fd": 89}, 3: {"fd": 88},
                       4: {"fd": 89}, 5: {"fd": 88}}), "R10_PASS ACCEPT"),

    # ---- gates ----
    ("N23", "a noise run is never judged", ledger(mode="noise"),
     "R10_BLOCKED NOISE_RUN_IS_NOT_JUDGED"),
    ("N24", "fewer than five rounds", ledger(rounds=4),
     "R10_INVALID ROUNDS_4_BELOW_5"),
    ("N25", "tolerance not frozen", ledger(),
     "R10_BLOCKED TOLERANCE_NOT_FROZEN"),
    ("N26", "tolerance from a different artifact", ledger(),
     "R10_BLOCKED TOLERANCE_ARTIFACT_MISMATCH"),
    ("N27", "derived state not observed",
     (lambda l: (l["rounds"][0]["derived"].update(source="ring_overflowed"), l)[-1])(ledger()),
     "R10_INVALID r1_DERIVED_NOT_OBSERVED_ring_overflowed"),
    # ---- mode E: inheritance and residue only ----
    ("E01", "both unclean endings then a clean fresh session", eledger(),
     "R10_PASS ACCEPT"),
    ("E02", "E rounds 1-2 keep their registries and that is NOT a leak",
     eledger(per_round={1: {"xreg": 8, "rreg": 8}}), "R10_PASS ACCEPT"),
    ("E03", "the F ending's expected halt is missing",
     eledger(per_round={1: {"halt": None}}),
     "R10_INVALID E1_EXPECTED_HALT_r-test-fatal-pre-fence_ABSENT_got=[]"),
    ("E04", "the H ending's expected halt is missing",
     eledger(per_round={2: {"halt": "something-else"}}),
     "R10_INVALID E2_EXPECTED_HALT_r-hup_ABSENT_got=['something-else']"),
    ("E05", "X survived an unclean ending",
     eledger(per_round={2: {"xalive": True}}), "R10_FAIL E2_X_SURVIVED_UNCLEAN_ENDING"),
    ("E06", "the Activity survived an unclean ending -> D-06 reopens",
     eledger(per_round={1: {"actalive": True}}),
     "R10_FAIL E1_ACTIVITY_SURVIVED_UNCLEAN_ENDING_D06_REOPENED"),
    ("E07", "the fresh session was not clean",
     eledger(per_round={3: {"halt": "x-hup"}}),
     "R10_FAIL E3_FRESH_SESSION_NOT_CLEAN_x-hup"),
    ("E08", "a nonce was inherited across an ending",
     eledger(per_round={3: {"nonce": 7001}}),
     "R10_FAIL E_SESSION_NONCE_INHERITED_[7001, 7002, 7001]"),
    ("E09", "generation 2 in an E round still reopens D-06",
     eledger(per_round={3: {"gen": 2}}), "R10_FAIL r3_GENERATION_IS_2_D06_REOPENED"),

    ("N28", "derived lease not zero",
     (lambda l: (l["rounds"][0]["derived"].update(lease_current=1), l)[-1])(ledger()),
     "R10_FAIL r1_DERIVED_LEASE_CURRENT_IS_1"),
]


def main() -> int:
    fails = []
    for cid, desc, led, want in CASES:
        tol = TOL
        if cid == "N25":
            tol = {**TOL, "status": "draft"}
        if cid == "N26":
            tol = {**TOL, "apk_sha256": "deadbeef"}
        got = run(led, tol)
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'} {cid} {desc}\n"
              f"      want={want}\n      got ={got}" if not ok
              else f"PASS {cid} {desc}")
        if not ok:
            fails.append(cid)
    print(f"r10_judge_vectors={len(CASES)} failures={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
