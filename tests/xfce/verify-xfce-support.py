#!/usr/bin/env python3
"""Static gate for XFCE-FREEZE-V1. Host only. Run before any XFCE run is granted.

    python3 tests/xfce/verify-xfce-support.py            exit 1 on any failure
    python3 tests/xfce/verify-xfce-support.py --selftest  proves each check can fail

Three groups:
  FREEZE   the frozen schedule is internally consistent with its own p06/p07/p08 counts,
           every window op targets a window that is open at that instant, phases tile
           the window, and the event classification covers 1..36 exactly once
  CONFIG   the overlay files exist, the two xfwm4 variants differ ONLY in
           use_compositing, and the panel carries none of the dialog-raising plugins
  PRODUCT  the product facts the freeze relies on are still true in the source that
           dc94485 was built from: the Over admission predicate, registry size 16, the
           counter and event enums, and no product file changed since dc94485
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
LORIE = REPO / "lorie" / "src" / "main" / "cpp" / "lorie"
sys.path.insert(0, str(HERE.parent / "common"))
import gatea_counters as C  # noqa: E402


def freeze_checks(f: dict) -> list[tuple[str, bool, object]]:
    out = []
    need = lambda n, ok, d=None: out.append((n, bool(ok), d))  # noqa: E731
    need("status", f.get("status") == "XFCE_DESIGN_FROZEN_V1", f.get("status"))
    need("frozen_before_any_xfce_session", f.get("frozen_before_any_xfce_session") is True)
    need("baseline_classification", f["baseline_vs_final"]["classification"] == "BASELINE"
         and f["baseline_vs_final"]["pga_closed"] is False)
    ch = f["p04_window_choreography"]
    sched = ch["schedule"]
    ts = [s["t"] for s in sched]
    need("schedule_strictly_increasing", all(a < b for a, b in zip(ts, ts[1:])), ts[:5])
    win = f["p01_duration"]["choreography_window_s"]
    need("schedule_ends_with_end_at_window", sched[-1]["op"] == "end" and sched[-1]["t"] == win,
         sched[-1])
    cnt = {}
    for s in sched:
        cnt[s["op"]] = cnt.get(s["op"], 0) + 1
    need("opens_match_p06", cnt.get("open") == f["p06_open_close"]["opens"], cnt.get("open"))
    need("closes_match_p06", cnt.get("close") == f["p06_open_close"]["closes"], cnt.get("close"))
    need("resizes_match_p07", cnt.get("resize") == f["p07_resize"]["count"], cnt.get("resize"))
    need("activates_match_p08", cnt.get("activate") == f["p08_redraw"]["focus_changes"],
         cnt.get("activate"))
    need("moves_match_p08", cnt.get("move") == f["p08_redraw"]["moves"], cnt.get("move"))
    open_now: set[str] = set()
    life_ok = True
    for s in sched:
        w = s.get("win")
        if s["op"] == "open":
            life_ok &= w not in open_now and w in ch["windows"]
            open_now.add(w)
        elif s["op"] == "close":
            life_ok &= w in open_now
            open_now.discard(w)
        elif s["op"] in ("activate", "move", "resize"):
            life_ok &= w in open_now
        elif s["op"] == "end":
            life_ok &= not open_now
    need("window_lifetimes_consistent", life_ok)
    sizes = [(s["w"], s["h"]) for s in sched if s["op"] == "resize"]
    want = [tuple(x) for x in f["p07_resize"]["sizes_px"]]
    need("resize_alternates_and_ends_small",
         sizes == [want[i % 2] for i in range(len(sizes))] and sizes[-1] == want[1], sizes)
    rw = {s["win"] for s in sched if s["op"] == "resize"}
    need("resize_window_matches_p07", rw == {f["p07_resize"]["window"]}, rw)
    ph = sorted(f["p04_window_choreography"]["phases"].values())
    need("phases_tile_window", ph[0][0] == 0 and ph[-1][1] == win
         and all(a[1] == b[0] for a, b in zip(ph, ph[1:])), ph)
    ev = f["p09_expected_gatea_traffic"]
    allowed = set(ev["allowed_events"])
    forb = {int(k) for k in ev["forbidden_events"]}
    need("events_disjoint", not (allowed & forb), sorted(allowed & forb))
    need("events_cover_1_36", allowed | forb == set(range(1, 37)),
         sorted(set(range(1, 37)) ^ (allowed | forb)))
    need("runs_order_matches_repetitions",
         all(f["runs"]["order"].count(v) == f["runs"]["repetitions_per_variant"]
             for v in f["p03_compositor"]["variants"]), f["runs"]["order"])
    need("x_root_is_measured_value", f["p02_resolution"]["x_root_px"] == [1200, 2191])
    return out


def config_checks(f: dict) -> list[tuple[str, bool, object]]:
    out = []
    need = lambda n, ok, d=None: out.append((n, bool(ok), d))  # noqa: E731
    cfg = HERE / "config"
    for n in ("xfwm4-C1.xml", "xfwm4-C0.xml", "xfce4-session.xml", "xfce4-panel.xml",
              "xfce4-terminal.xml"):
        need(f"config_exists_{n}", (cfg / n).is_file())
    for v, meta in f["p03_compositor"]["variants"].items():
        need(f"variant_{v}_config_path", (HERE / meta["config"]).is_file(), meta["config"])
    try:
        c1 = (cfg / "xfwm4-C1.xml").read_text().splitlines()
        c0 = (cfg / "xfwm4-C0.xml").read_text().splitlines()
        diff = [(a, b) for a, b in zip(c1, c0) if a != b]
        prop_diff = [d for d in diff if "<property" in d[0]]
        need("xfwm4_variants_differ_only_in_use_compositing",
             len(c1) == len(c0) and len(prop_diff) == 1 and "use_compositing" in prop_diff[0][0]
             and 'value="true"' in prop_diff[0][0] and 'value="false"' in prop_diff[0][1], prop_diff)
        need("vblank_off_both", all('name="vblank_mode" type="string" value="off"' in "\n".join(x)
                                    for x in (c1, c0)))
    except OSError as e:
        need("xfwm4_variants_readable", False, str(e))
    panel = (cfg / "xfce4-panel.xml").read_text() if (cfg / "xfce4-panel.xml").is_file() else ""
    need("panel_no_pulseaudio", "pulseaudio" not in panel)
    need("panel_no_power_manager", "power-manager" not in panel)
    ids1 = re.findall(r'<value type="int" value="(\d+)"/>',
                      panel.split('name="panel-1"')[1].split("</property>")[0]
                      .split('name="plugin-ids"')[1] if 'name="panel-1"' in panel else "")
    need("panel1_ids_exclude_7_8_9", not ({"7", "8", "9"} & set(ids1)), ids1)
    for t in ("term_load.sh", "session_wrapper.sh", "choreo.py"):
        need(f"tool_exists_{t}", (HERE / t).is_file())
    return out


def product_checks(f: dict) -> list[tuple[str, bool, object]]:
    out = []
    need = lambda n, ok, d=None: out.append((n, bool(ok), d))  # noqa: E731
    io = (LORIE / "InitOutput.c").read_text(errors="replace")
    m = re.search(r"static Bool lorieCanAccelCompositePictures\(.*?\n}\n", io, re.S)
    body = m.group(0) if m else ""
    for frag in ("if (op != PictOpOver)", "mask)", "src->format != PICT_a8r8g8b8",
                 "dst->format != PICT_x8r8g8b8", "src->transform || src->repeat",
                 "src->filter != PictFilterNearest", "componentAlpha", "alphaMap",
                 "src->pDrawable == dst->pDrawable"):
        need(f"over_predicate:{frag}", frag in body)
    ce = (LORIE / "cmdentrypoint.cpp").read_text(errors="replace")
    need("registry_size_16", re.search(r"#define LORIE_GATEA_XREGISTRY_SIZE 16\b", ce) is not None)
    lh = (LORIE / "lorie.h").read_text(errors="replace")
    need("counter_enum_matches_table", C.parse_from_source(lh) == C.COUNTERS)
    blk = lh.split("LORIE_GATEA_EVENT_NONE = 0,", 1)[1].split("LORIE_GATEA_EVENT_MAX", 1)[0]
    names = re.findall(r"LORIE_GATEA_EVENT_([A-Z0-9_]+),", blk)
    evmap = {i + 1: n for i, n in enumerate(names)}
    need("event_enum_36_entries", len(names) == 36, len(names))
    for i, n in ((1, "REGISTER_READY"), (2, "LEASE_RESERVED"), (5, "LEASE_GPU_OWNED"),
                 (9, "DIRECT_LOOKUP_FAIL"), (12, "FENCE_TIMEOUT"), (13, "FENCE_ERROR"),
                 (15, "FIRST_FAILED_SERIAL"), (16, "GENERATION_FATAL"), (23, "LEASE_RELEASE"),
                 (35, "TEST_FAULT_FIRED")):
        need(f"event_{i}_is_{n}", evmap.get(i) == n, evmap.get(i))
    forb = f["p09_expected_gatea_traffic"]["forbidden_events"]
    need("forbidden_names_match_enum", all(evmap.get(int(k)) == v for k, v in forb.items()), forb)
    r = subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", f["binding"]["source_sha"],
                        "--", "lorie", "app", "shell-loader", "build.gradle"],
                       capture_output=True)
    need("product_tree_unchanged_since_binding", r.returncode == 0, r.returncode)
    return out


def host_checks(f: dict) -> list[tuple[str, bool, object]]:
    out = []
    for pkg, ver in f["pinned_packages"].items():
        r = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", pkg], capture_output=True,
                           text=True)
        out.append((f"pkg_{pkg}", r.returncode == 0 and r.stdout == ver, r.stdout))
    return out


def run_all(f: dict, host: bool = True):
    res = freeze_checks(f) + config_checks(f) + product_checks(f)
    if host:
        res += host_checks(f)
    return res


def selftest(f: dict) -> int:
    """Each mutation must turn at least the named check red."""
    muts = []

    def m(name, fn, check):
        g = copy.deepcopy(f)
        fn(g)
        muts.append((name, g, check))
    m("swap two steps", lambda g: g["p04_window_choreography"]["schedule"].__setitem__(
        slice(3, 5), list(reversed(g["p04_window_choreography"]["schedule"][3:5]))),
      "schedule_strictly_increasing")
    m("drop a close", lambda g: g["p04_window_choreography"]["schedule"].pop(
        next(i for i, s in enumerate(g["p04_window_choreography"]["schedule"])
             if s["op"] == "close")), "closes_match_p06")
    m("activate a closed window", lambda g: g["p04_window_choreography"]["schedule"][3]
      .__setitem__("win", "T9"), "window_lifetimes_consistent")
    m("event both allowed and forbidden", lambda g: g["p09_expected_gatea_traffic"]
      ["allowed_events"].append(12), "events_disjoint")
    m("event unclassified", lambda g: g["p09_expected_gatea_traffic"]["allowed_events"]
      .remove(31), "events_cover_1_36")
    m("forbidden name wrong", lambda g: g["p09_expected_gatea_traffic"]["forbidden_events"]
      .__setitem__("12", "FENCE_SATISFIED"), "forbidden_names_match_enum")
    m("resize order", lambda g: g["p07_resize"].__setitem__("sizes_px", [[640, 480], [900, 700]]),
      "resize_alternates_and_ends_small")
    m("binding moved", lambda g: g["binding"].__setitem__("source_sha", "HEAD~40"),
      "product_tree_unchanged_since_binding")
    bad = 0
    for name, g, check in muts:
        res = {n: ok for n, ok, _ in run_all(g, host=False)}
        red = res.get(check) is False
        print(f"SELFTEST {'RED  ' if red else 'GREEN'} {name} -> {check}")
        bad += 0 if red else 1
    base = [n for n, ok, _ in run_all(f, host=False) if not ok]
    print(f"SELFTEST baseline failures={base}")
    return 1 if bad or base else 0


def main() -> int:
    f = json.loads((HERE / "xfce-design-freeze.json").read_text())
    if "--selftest" in sys.argv:
        return selftest(f)
    res = run_all(f)
    fails = [(n, d) for n, ok, d in res if not ok]
    for n, d in fails:
        print(f"FAIL {n} {d}")
    print(f"XFCE_SUPPORT {'PASS' if not fails else 'FAIL'} checks={len(res)} failed={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
