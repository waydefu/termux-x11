#!/usr/bin/env python3
"""judge-r10.py — verdict from a ledger.json plus a frozen tolerance file.

Verdict codes match R8/R9: 0 PASS, 1 FAIL, 2 INVALID, 3 BLOCKED.

The four classes are V2-R10-DESIGN §8 and they are NOT interchangeable:

  class 1  Gate A counters at each clean close. EXACT, zero tolerance. These are
           counts of protocol events, not measurements of a noisy system.
  class 2  in-session balance (mode A): registry and lease must return to 0.
  class 3  cross-round trend, judged against the FROZEN noise tolerances. Never
           from a single delta.
  class 4  residue and redlines. Binary.

INVALID vs FAIL is the R8 §8.8 split and is preserved: a round whose construction
never happened yields no information and must not be scored as a failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PASS, FAIL, INVALID, BLOCKED = 0, 1, 2, 3


class Verdict(Exception):
    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


def counters_exact(r: dict) -> None:
    """Class 1. The clean-close signature, measured on a healthy session in
    p2-r10-probe/probe-01: AHB 16/16, EGLImage 16/16, texture 16/16, both
    registries 0, lease 0, no fatal, generation_close 1."""
    n = r["round"]
    c = r["counters_at_close"]
    if not c["summary_present"]:
        raise Verdict(INVALID, f"r{n}_NO_DUMP_AT_CLOSE")
    if c["where"] != "x-close-screen":
        raise Verdict(INVALID, f"r{n}_NOT_A_CLEAN_CLOSE_where={c['where']}")
    for key in ("c_x_registry_current", "c_renderer_registry_current",
                "c_lease_current", "c_generation_fatal"):
        v = c[key]
        if v is None:
            raise Verdict(INVALID, f"r{n}_COUNTER_NOT_OBSERVED_{key}")
        if v != 0:
            raise Verdict(FAIL, f"r{n}_{key.upper()}_IS_{v}_EXPECTED_0")
    for a, b, label in (("c_ahb_acquire", "c_ahb_release", "AHB"),
                        ("c_eglimage_create", "c_eglimage_destroy", "EGLIMAGE"),
                        ("c_texture_create", "c_texture_delete", "TEXTURE")):
        x, y = c[a], c[b]
        if x is None or y is None:
            raise Verdict(INVALID, f"r{n}_COUNTER_NOT_OBSERVED_{label}")
        if x != y:
            raise Verdict(FAIL, f"r{n}_{label}_UNBALANCED_{x}_vs_{y}")
    if c["c_generation_close"] != 1:
        raise Verdict(FAIL, f"r{n}_GENERATION_CLOSE_IS_{c['c_generation_close']}")
    # the close zeroes both halves (Q10). A non-zero pair here means the dump was
    # taken somewhere other than after the close.
    if (c["nonce_at_dump"], c["generation_at_dump"]) != (0, 0):
        raise Verdict(FAIL, f"r{n}_CLOSE_DID_NOT_ZERO_TUPLE "
                            f"{c['nonce_at_dump']},{c['generation_at_dump']}")


def d06_holds(r: dict) -> None:
    """D-06 at runtime: every session is generation 1. A 2 here would mean a
    generation boundary became reachable, which reopens D-06 and the R9 WARM/COLD-2
    removals - so it is a FAIL of this judge, not a quiet pass."""
    n, s = r["round"], r["session"]
    g = s.get("session_generation")
    if g is None:
        return                      # not observed; §7 says null, and null is not 2
    if g != 1:
        raise Verdict(FAIL, f"r{n}_GENERATION_IS_{g}_D06_REOPENED")


def derived_zero(r: dict, tag_required: bool) -> None:
    """Class 2. Occupancy back to zero, from the traced events."""
    n, d = r["round"], r["derived"]
    if d["source"] not in ("ring_events", "logcat_events"):
        if tag_required:
            raise Verdict(INVALID, f"r{n}_DERIVED_NOT_OBSERVED_{d['source']}")
        return
    for k in ("x_registry_current", "renderer_registry_current", "lease_current"):
        if d[k] != 0:
            raise Verdict(FAIL, f"r{n}_DERIVED_{k.upper()}_IS_{d[k]}")


def trend(ledger: dict, tol: dict) -> list[dict]:
    """Class 3. Per (metric, tag) across rounds, classified exactly as plan §9.6:
    strictly increasing AND beyond tolerance is a leak; rise-then-flat is a one-off
    that must be explained; inside tolerance is noise."""
    series: dict[str, list] = {}
    for r in ledger["rounds"]:
        for tag, sample in sorted((r["samples"] or {}).items()):
            if not sample:
                continue
            for dom in ("x", "activity"):
                for k, v in (sample.get(dom) or {}).items():
                    if isinstance(v, bool) or not isinstance(v, int) or k == "pid":
                        continue
                    series.setdefault(f"{dom}.{k}@{tag}", []).append(v)
    out = []
    for key, vals in sorted(series.items()):
        metric = key.split("@")[0]
        t = tol["tolerance"].get(metric)
        if len(vals) < 2 or t is None:
            out.append({"key": key, "values": vals, "tolerance": t,
                        "class": "INSUFFICIENT"})
            continue
        total = vals[-1] - vals[0]
        strictly_up = all(b > a for a, b in zip(vals, vals[1:]))
        if strictly_up and total > t:
            cls = "LEAK"
        elif abs(total) <= t:
            cls = "NOISE"
        else:
            cls = "ONE_OFF"
        out.append({"key": key, "values": vals, "tolerance": t,
                    "total_delta": total, "class": cls})
    return out


def endings(ledger: dict) -> None:
    """Mode E. Judged on inheritance and residue ONLY.

    The F and H endings release nothing by design - the process dies holding its
    resources and the OS reclaims - so a leak metric taken after them would measure
    the OS, not Gate A (V2-R10-DESIGN §3.1). What they DO answer is the question the
    whole V1 recovery model rests on: after an unclean ending, does the next session
    inherit anything?

    Expected, from D-06 §2.3: both unclean endings kill BOTH processes, so a fresh
    session can only be fresh."""
    rs = {r["round"]: r for r in ledger["rounds"]}
    for n, want in ((1, "r-test-fatal-pre-fence"), (2, "r-hup")):
        r = rs.get(n)
        if r is None:
            raise Verdict(INVALID, f"E_ROUND_{n}_MISSING")
        got = [h["what"] for h in r.get("halts_in_round", [])]
        if want not in got:
            raise Verdict(INVALID, f"E{n}_EXPECTED_HALT_{want}_ABSENT_got={got}")
        if r.get("x_alive_after_ending") is not False:
            raise Verdict(FAIL, f"E{n}_X_SURVIVED_UNCLEAN_ENDING")
        if r.get("activity_alive_after_ending") is not False:
            raise Verdict(FAIL, f"E{n}_ACTIVITY_SURVIVED_UNCLEAN_ENDING_D06_REOPENED")
    fresh = rs.get(3)
    if fresh is None:
        raise Verdict(INVALID, "E_FRESH_ROUND_MISSING")
    if fresh.get("halts_in_round"):
        raise Verdict(FAIL, "E3_FRESH_SESSION_NOT_CLEAN_"
                            + ",".join(h["what"] for h in fresh["halts_in_round"]))
    nonces = [rs[n]["session"].get("session_nonce") for n in (1, 2, 3)]
    known = [x for x in nonces if x]
    if len(known) < 3:
        raise Verdict(INVALID, f"E_SESSION_NONCE_NOT_OBSERVED_{nonces}")
    if len(set(known)) != 3:
        raise Verdict(FAIL, f"E_SESSION_NONCE_INHERITED_{nonces}")
    counters_exact(fresh)
    derived_zero(fresh, tag_required=True)


def residue(ledger: dict) -> None:
    """Class 4. Binary."""
    if ledger["stable_before"] is None or ledger["stable_after"] is None:
        raise Verdict(INVALID, "STABLE_EVIDENCE_MISSING")
    if ledger["stable_before"] != ledger["stable_after"]:
        raise Verdict(FAIL, "STABLE_CHANGED")
    leftover = [s for s in ledger["x11_unix_after"] if s != "X1"]
    if leftover:
        raise Verdict(FAIL, f"X_SOCKET_RESIDUE_{','.join(leftover)}")
    if ledger["mode"] != "E":
        # mode E's halts are its construction, not a surprise; they are checked
        # round by round in endings() against the exact fatal each round expects.
        for h in ledger["halts"]:
            raise Verdict(FAIL, f"UNEXPECTED_FATAL_{h['what']}_{h['reason']}")
    for r in ledger["rounds"]:
        if ledger["mode"] == "E" and r["round"] in (1, 2):
            continue          # the client is EXPECTED to lose its server

        if r["client_rc"] not in (0,):
            raise Verdict(INVALID, f"r{r['round']}_CLIENT_RC_{r['client_rc']}")
        if r["x_alive_after_terminate"] is True:
            raise Verdict(FAIL, f"r{r['round']}_X_SURVIVED_TERMINATE")


def activity_identity(ledger: dict) -> None:
    """Mode B's premise is that ONE Activity process spans every session; mode C's
    is that each round gets a new one. Neither is assumed - both are checked."""
    mode = ledger["mode"]
    pids = [r["activity_pid"] for r in ledger["rounds"]]
    if any(p is None for p in pids):
        raise Verdict(INVALID, "ACTIVITY_PID_NOT_OBSERVED")
    if mode == "E":
        return            # each E round is a different process pair, by construction
    if mode in ("A", "B", "noise"):   # A is ONE session, so also one Activity
        if len(set(pids)) != 1:
            raise Verdict(INVALID, f"ACTIVITY_DID_NOT_SURVIVE_{pids}")
    elif mode == "C":
        if len(set(pids)) != len(pids):
            raise Verdict(INVALID, f"ACTIVITY_NOT_FRESH_EACH_ROUND_{pids}")


def session_freshness(ledger: dict) -> None:
    """Each X process draws its own sessionNonce, so consecutive sessions must not
    share one. Unobserved is null and is skipped, never defaulted to 'fresh'."""
    if ledger["mode"] not in ("B", "C"):
        return
    seen = [r["session"].get("session_nonce") for r in ledger["rounds"]]
    known = [s for s in seen if s]
    if len(known) >= 2 and len(set(known)) != len(known):
        raise Verdict(FAIL, f"SESSION_NONCE_REUSED_{seen}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--tolerance", required=True)
    ap.add_argument("--output")
    a = ap.parse_args()

    code, reason, tr = PASS, "ACCEPT", []
    try:
        ledger = json.loads(Path(a.ledger).read_text())
        tol = json.loads(Path(a.tolerance).read_text())
        if tol.get("status") != "R10_TOLERANCE_FROZEN_V1":
            raise Verdict(BLOCKED, "TOLERANCE_NOT_FROZEN")
        if tol.get("apk_sha256") != ledger.get("apk_sha256"):
            raise Verdict(BLOCKED, "TOLERANCE_ARTIFACT_MISMATCH")
        if ledger["mode"] == "noise":
            raise Verdict(BLOCKED, "NOISE_RUN_IS_NOT_JUDGED")
        want = 5 if ledger["mode"] in ("B", "C") else (3 if ledger["mode"] == "E" else 1)
        if len(ledger["rounds"]) < want:
            raise Verdict(INVALID, f"ROUNDS_{len(ledger['rounds'])}_BELOW_{want}")

        residue(ledger)
        activity_identity(ledger)
        session_freshness(ledger)
        # D-06's runtime guard applies to EVERY mode, before any mode-specific
        # path returns. Caught by vector E09: with this inside the generic loop, an
        # E round could have reported generation 2 and still passed.
        for r in ledger["rounds"]:
            d06_holds(r)
        if ledger["mode"] == "E":
            # E returns here. The generic per-round checks below assume a CLEAN
            # ending: counters_exact() wants a balanced close and derived_zero()
            # wants an empty registry. Rounds 1 and 2 end in a fatal, so their
            # registries legitimately still hold what the dying process held - that
            # is the definition of the ending, not a leak. Running those checks on
            # them would manufacture a FAIL out of the expected result.
            endings(ledger)
            return _emit(PASS, "ACCEPT", [], a.output)
        for r in ledger["rounds"]:
            if ledger["mode"] in ("B", "C"):
                counters_exact(r)
                derived_zero(r, tag_required=True)
            else:
                derived_zero(r, tag_required=False)
        tr = trend(ledger, tol)
        leaks = [t for t in tr if t["class"] == "LEAK"]
        if leaks:
            raise Verdict(FAIL, "LEAK_" + ",".join(t["key"] for t in leaks))
    except Verdict as v:
        code, reason = v.code, v.reason
    except (OSError, json.JSONDecodeError, KeyError) as e:
        code, reason = INVALID, f"LEDGER_UNREADABLE_{type(e).__name__}"

    return _emit(code, reason, tr, a.output)


def _emit(code: int, reason: str, tr: list, out: str | None) -> int:
    name = {PASS: "R10_PASS", FAIL: "R10_FAIL",
            INVALID: "R10_INVALID", BLOCKED: "R10_BLOCKED"}[code]
    print(f"{name} {reason}")
    if out:
        Path(out).write_text(json.dumps(
            {"exit": code, "verdict": name, "reason": reason, "trend": tr},
            indent=2) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
