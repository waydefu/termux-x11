#!/usr/bin/env python3
"""Each mutant disables one judge-oracle.py rule; the named test must go red."""
import shutil, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
M = [
 ('chk("correctness", f"{ph}_exact", None if r is None else (r["fail"] == 0 and r["maxd"] == 0), r)',
  'chk("correctness", f"{ph}_exact", True, r)', "T.test_phase_fail_with_result_pass_line"),
 ('None if d_p is None else d_p == per.get("cases")', 'True', "T.test_not_all_direct_is_attribution"),
 ('None if d_n is None else d_n == 0, d_n)', 'True, d_n)', "T.test_direct_in_negative_is_correctness"),
 ('complete = seqs.complete(nxt)', 'complete = True',
  "T.test_events_incomplete_is_invalid"),
 ('r is not None and r["cases"] == exp[ph]', 'r is not None', "T.test_case_count_wrong_is_invalid"),
 ('COL._int(tr.get("TracerPid")) == 0', 'True', "T.test_traced_is_invalid"),
 ('bool(sb) and sb == sa', 'True', "T.test_stable_changed_is_invalid"),
 ('if failed("correctness"):\n        verdict = "FAIL_CORRECTNESS"\n    elif failed("validity")',
  'if failed("validity"):\n        verdict = "INVALID"\n    elif failed("correctness")', "T.test_correctness_beats_invalid"),
 ('act is not None and act == act_after', 'True', "T.test_activity_died"),
 ('if int(m.group(3)) == 5:', 'if True:', "T.test_other_events_in_window_are_not_direct"),
 ('chk("correctness", "no_fatal", n_fat == 0, fat)', 'chk("correctness", "no_fatal", True, fat)',
  "T.test_fatal_line"),
]
bad = 0
for i, (old, new, test) in enumerate(M):
    tmp = Path(tempfile.mkdtemp())
    try:
        shutil.copytree(HERE.parent, tmp / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        f = tmp / "tests" / "oracle" / "judge-oracle.py"
        s = f.read_text()
        if s.count(old) != 1:
            print(f"MUTANT {i} ANCHOR_BROKEN"); bad += 1; continue
        f.write_text(s.replace(old, new))
        r = subprocess.run([sys.executable, str(tmp / "tests" / "oracle" / "test_oracle.py"), test],
                           capture_output=True, text=True)
        killed = r.returncode != 0
        bad += not killed
        print(f"MUTANT {i} {'KILLED ' if killed else 'SURVIVED'} {test}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
print(f"mutants={len(M)} survived={bad}")
sys.exit(1 if bad else 0)
