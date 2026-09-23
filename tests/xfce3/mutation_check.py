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
     'if is_x and inw:',
     'if inw:',
     "Clean.test_x_pid_filter_excludes_stable"),
    ("xfce_collect.py",
     '(mine and any(p in msg for p in FATAL_PATTERNS))',
     '(any(p in msg for p in FATAL_PATTERNS))',
     "MustGoRed.test_fatal_from_stable_is_ignored"),
    ("xfce_collect.py",
     '\n                        or (r["tag"] == "DEBUG" and r["lvl"] == "F"'
     '\n                            and any(rx.search(msg) for rx in names_ours))):',
     '):',
     "MustGoRed.test_tombstone_naming_our_pid_is_defect"),
    ("xfce_collect.py",
     'return n is not None and self.count == n and self.distinct == n and self.max < n',
     'return True',
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
     '        out["summary"] = summary_counters(summ_line)\n',
     '        out["summary"] = None\n',
     "MustGoRed.test_summary_file_missing_but_in_logcat_is_valid"),
    # ---- V3 rules
    ("judge-xfce.py",
     'validity.append(chk("x3_untraced", run.get("x3_tracer_pid") == 0,',
     'validity.append(chk("x3_untraced", True,',
     "MustGoRed.test_traced_x_is_invalid"),
    ("judge-xfce.py",
     'all(s.get("awake") is True and s.get("keyguard") is False for s in scr)',
     'scr[0].get("awake") is True',
     "MustGoRed.test_screen_off_mid_run_is_invalid"),
    # ---- V2 rules
    ("judge-xfce.py",
     'validity.append(chk("diag_stamps_absent", run.get("diag_stamp_lines") == 0,',
     'validity.append(chk("diag_stamps_absent", True,',
     "MustGoRed.test_x_pid_stamp_is_invalid"),
    ("xfce_collect.py",
     'if is_x and msg.startswith(STAMP_PREFIXES):',
     'if msg.startswith(STAMP_PREFIXES):',
     "Clean.test_x_pid_filter_excludes_stable"),
    ("judge-xfce.py",
     'bool(closes) and all(rc is not None for rc in term_rc)',
     'True',
     "MustGoRed.test_terminal_never_exited_is_defect"),
    ("xfce_collect.py",
     'else pre_t_w - out["gatea_direct"])',
     'else pre_t_w)',
     "Clean.test_d0a_staged_from_counters"),
    ("xfce_collect.py",
     'out["composite_total"] = out["xrender_5s"]["xrender_ops"]',
     'out["composite_total"] = 12',
     "Clean.test_x_pid_filter_excludes_stable"),
    ("xfce_collect.py",
     'return int(rest[11]) + int(rest[12])',
     'return int(text.split()[13]) + int(text.split()[14])',
     "Clean.test_cpu_ticks_parse_after_last_paren"),
    # ---- streaming (INCIDENT-20260923): the flat-memory reader must equal the list one
    ("xfce_collect.py",
     'for line in phys.splitlines():',
     'for line in [phys.rstrip("\\n")]:',
     "Streaming.test_iter_logcat_matches_whole_text_parse"),
    ("xfce_collect.py",
     'return n is not None and self.count == n and self.distinct == n and self.max < n',
     'return n is not None and self.count == n and self.max < n',
     "Streaming.test_seqset_matches_set_semantics"),
    ("xfce_collect.py",
     'return n is not None and self.count == n and self.distinct == n and self.max < n',
     'return n is not None and self.count == n and self.distinct == n',
     "Streaming.test_seqset_matches_set_semantics"),
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
