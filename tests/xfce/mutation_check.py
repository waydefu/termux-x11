#!/usr/bin/env python3
"""Proves the XFCE tests can fail. Each mutation disables ONE rule in a scratch copy of
the collector or judge; the named test MUST go red on the mutant. A mutant that stays
green means that test is a control that cannot fail - and the rule it claims to guard
is unguarded.

    python3 tests/xfce/mutation_check.py      exit 0 iff every mutant was killed
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (file, exact text to replace, replacement, test that must fail)
MUTANTS = [
    ("judge-xfce.py",
     '(c(a) is not None and c(a) == c(bb)) if have else None',
     'True',
     "MustGoRed.test_counter_imbalance_is_defect"),
    ("judge-xfce.py",
     '((18, "x_registry_current"), (19, "renderer_registry_current"),',
     '((25, "x_registry_current"), (26, "renderer_registry_current"),',
     "Clean.test_registry_index_is_c18_c19_not_c25_c26"),
    ("judge-xfce.py",
     'if (s.get("lateness_s") is None) or s["lateness_s"] > late_lim]',
     'if False]',
     "MustGoRed.test_late_step_is_invalid"),
    ("judge-xfce.py",
     "t0.get(\"timescale\") == 1.0 and",
     "True and",
     "MustGoRed.test_test_mode_timescale_is_invalid"),
    ("judge-xfce.py",
     'core_ok = all(c1.get(n) and c1.get(n) == c2.get(n) for n in core)',
     'core_ok = True',
     "MustGoRed.test_xfwm4_restart_is_defect"),
    ("judge-xfce.py",
     'if crash_fail:\n        verdict = "BASELINE_DEFECT"\n    elif valid_fail:',
     'if valid_fail:\n        verdict = "INVALID"\n    elif crash_fail:',
     "MustGoRed.test_terminal_nonzero_exit_is_defect"),
    ("xfce_collect.py",
     'xw = [r for r in inwin if x_pid is not None and r["pid"] == x_pid]',
     'xw = list(inwin)',
     "Clean.test_x_pid_filter_excludes_stable"),
    ("xfce_collect.py",
     '(r["pid"] in ours and any(p in r["msg"] for p in FATAL_PATTERNS))',
     '(any(p in r["msg"] for p in FATAL_PATTERNS))',
     "MustGoRed.test_fatal_from_stable_is_ignored"),
    ("xfce_collect.py",
     'or (r["tag"] == "DEBUG" and r["lvl"] == "F" and names_ours(r["msg"]))]',
     ']',
     "MustGoRed.test_tombstone_naming_our_pid_is_defect"),
    ("xfce_collect.py",
     'complete = nxt is not None and len(ev_all) == nxt and seqs == set(range(nxt))',
     'complete = True',
     "Incomplete.test_seq_gap_nulls_event_metrics_but_counters_still_judge"),
    ("xfce_collect.py",
     'if prev_newest is not None and vp["full"] and oldest > prev_newest:',
     'if False:',
     "Incomplete.test_sf_gap_nulls_frame_metrics"),
    ("xfce_collect.py",
     'if prev_newest is not None and vp["full"] and oldest > prev_newest:',
     'if prev_newest is not None:',
     "Incomplete.test_sf_overlap_is_complete"),
    ("xfce_collect.py",
     '        out["summary"] = summary_counters(line)\n',
     '        out["summary"] = None\n',
     "MustGoRed.test_summary_file_missing_but_in_logcat_is_valid"),
    ("xfce_collect.py",
     'return int(rest[11]) + int(rest[12])',
     'return int(text.split()[13]) + int(text.split()[14])',
     "Clean.test_cpu_ticks_parse_after_last_paren"),
]


def main() -> int:
    survivors = []
    for i, (fname, old, new, test) in enumerate(MUTANTS):
        tmp = Path(tempfile.mkdtemp(prefix=f"xfce-mut{i}-"))
        try:
            dst = tmp / "tests" / "xfce"
            shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(HERE.parent / "common", tmp / "tests" / "common",
                            ignore=shutil.ignore_patterns("__pycache__"))
            src = (dst / fname).read_text()
            if src.count(old) != 1:
                print(f"MUTANT {i} ANCHOR_BROKEN {fname}: {old!r}")
                survivors.append(i)
                continue
            (dst / fname).write_text(src.replace(old, new))
            r = subprocess.run([sys.executable, str(dst / "test_xfce.py"), test],
                               capture_output=True, text=True, timeout=300)
            killed = r.returncode != 0
            print(f"MUTANT {i:2d} {'KILLED ' if killed else 'SURVIVED'} {test}")
            if not killed:
                survivors.append(i)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"mutants={len(MUTANTS)} survived={len(survivors)}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
