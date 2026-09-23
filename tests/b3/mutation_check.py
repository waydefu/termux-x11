#!/usr/bin/env python3
"""Each mutant disables one b3_analyze.py rule (literal replacement; an anchor that does
not match exactly once is a harness error, never a pass). The named test must go red."""
import shutil, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
M = [
 ('pruning_ok = (agreement is not None', 'pruning_ok = True or (agreement is not None',
  'T.test_hidden_interaction_rejects_pruning'),
 ('"judgeable": nb is not None and nb < th["noise_band_max"]', '"judgeable": True',
  'T.test_noise_band_blocks_judgement'),
 ('if d["errors"] or not d["pixel_ok"]:', 'if d["errors"]:', 'T.test_bad_pixel_cell_excluded_and_listed'),
 ('if all(p["winner"] == "GPU" for p in pts[i:]):', 'if pts[i]["winner"] == "GPU":',
  'T.test_crossover_is_after_the_last_non_gpu_point'),
]
bad = 0
for i, (old, new, test) in enumerate(M):
    tmp = Path(tempfile.mkdtemp())
    try:
        shutil.copytree(HERE, tmp / "b3", ignore=shutil.ignore_patterns("__pycache__"))
        f = tmp / "b3" / "b3_analyze.py"
        s = f.read_text()
        if s.count(old) != 1:
            print(f"MUTANT {i} ANCHOR_BROKEN"); bad += 1; continue
        f.write_text(s.replace(old, new))
        r = subprocess.run([sys.executable, str(tmp / "b3" / "test_b3.py"), test], capture_output=True)
        killed = r.returncode != 0
        bad += not killed
        print(f"MUTANT {i} {'KILLED ' if killed else 'SURVIVED'} {test}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
print(f"mutants={len(M)} survived={bad}")
sys.exit(1 if bad else 0)
