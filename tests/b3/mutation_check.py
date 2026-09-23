#!/usr/bin/env python3
"""Each mutant disables one b3_analyze.py / b3_attribution.py rule (literal replacement; an
anchor that does not match exactly once is a harness error, never a pass). The named test
must go red. (file, old, new, test file, test)"""
import shutil, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
M = [
 ('b3_analyze.py', 'pruning_ok = (agreement is not None', 'pruning_ok = True or (agreement is not None',
  'test_b3.py', 'T.test_hidden_interaction_rejects_pruning'),
 ('b3_analyze.py', '"judgeable": nb is not None and nb < th["noise_band_max"]', '"judgeable": True',
  'test_b3.py', 'T.test_noise_band_blocks_judgement'),
 ('b3_analyze.py', 'if d["errors"] or not d["pixel_ok"]:', 'if d["errors"]:',
  'test_b3.py', 'T.test_bad_pixel_cell_excluded_and_listed'),
 ('b3_analyze.py', 'if all(p["winner"] == "GPU" for p in pts[i:]):', 'if pts[i]["winner"] == "GPU":',
  'test_b3.py', 'T.test_crossover_is_after_the_last_non_gpu_point'),
 ('b3_attribution.py', 'if int(m.group(3)) == 5:', 'if True:',
  'test_b3_attribution.py', 'T.test_classes'),
 ('b3_attribution.py', 'issued = (a.warmup + a.iters) * d["batch"] + d["batch"]',
  'issued = (a.warmup + a.iters) * d["batch"]', 'test_b3_attribution.py', 'T.test_classes'),
 ('b3_attribution.py', 'complete = seqs.complete(nxt)', 'complete = True',
  'test_b3_attribution.py', 'T.test_incomplete_events_null_every_cell'),
 ('b3_attribution.py', 'if r["pid"] not in ours:', 'if False:',
  'test_b3_attribution.py', 'T.test_foreign_pid_event5_not_counted'),
]
bad = 0
for i, (fname, old, new, tfile, test) in enumerate(M):
    tmp = Path(tempfile.mkdtemp())
    try:
        shutil.copytree(HERE, tmp / "b3", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(HERE.parent / "xfce3", tmp / "xfce3", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(HERE.parent / "common", tmp / "common", ignore=shutil.ignore_patterns("__pycache__"))
        f = tmp / "b3" / fname
        s = f.read_text()
        if s.count(old) != 1:
            print(f"MUTANT {i} ANCHOR_BROKEN"); bad += 1; continue
        f.write_text(s.replace(old, new))
        r = subprocess.run([sys.executable, str(tmp / "b3" / tfile), test], capture_output=True)
        killed = r.returncode != 0
        bad += not killed
        print(f"MUTANT {i} {'KILLED ' if killed else 'SURVIVED'} {test}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
print(f"mutants={len(M)} survived={bad}")
sys.exit(1 if bad else 0)
