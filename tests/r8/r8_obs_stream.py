#!/usr/bin/env python3
"""R8 observation stream contract (host tooling).

Collector x-observations.jsonl is the sole X semantic observation stream.
Collector renderer-observations.jsonl is the sole renderer semantic stream.
raw/logcat/ring remain immutable provenance and must never be concatenated
into semantic counting rows.

Real duplicate BEGIN/END inside collector still fail. This module does not
dedupe collector rows.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OBS_PAT = re.compile(r"R8_OBS\s+(\{.*\})\s*$")

RAW_PROVENANCE_NAMES = (
    "raw-logcat.txt",
    "logcat.txt",
    "x-launcher.raw.log",
    "x3-launcher.raw.log",
    "gatea-ring.txt",
    "ring.txt",
    "gatea-summary.txt",
)


def parse_raw_obs(text: str) -> tuple[list[dict], list[dict]]:
    xs, rs = [], []
    for line in (text or "").splitlines():
        m = OBS_PAT.search(line)
        if not m:
            continue
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        role = obj.get("role")
        if role == "x":
            xs.append(obj)
        elif role == "r":
            rs.append(obj)
    return xs, rs


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def load_semantic_obs(ev: Path) -> tuple[list[dict], list[dict]]:
    return (
        load_jsonl(ev / "x-observations.jsonl"),
        load_jsonl(ev / "renderer-observations.jsonl"),
    )


def raw_provenance_text(ev: Path) -> str:
    blob = ""
    for name in RAW_PROVENANCE_NAMES:
        p = ev / name
        if p.is_file():
            blob += p.read_text(encoding="utf-8", errors="replace") + "\n"
    return blob


def obs_id(row: dict) -> tuple:
    phase = row.get("phase")
    if phase == "END":
        return (row.get("role"), "END", row.get("producer_seq"), row.get("actual_count"))
    return (row.get("role"), phase, row.get("producer_seq"))


def collector_provenance(raw_text: str, jsonl_rows: list[dict]) -> tuple[bool, str]:
    """Fail-closed when collector semantic rows are absent from producer raw.

    If raw has no R8_OBS (host vectors with jsonl-only), skip. Extra raw rows
    are not counted and are not a mismatch.
    """
    raw_x, raw_r = parse_raw_obs(raw_text or "")
    raw_all = raw_x + raw_r
    if not raw_all:
        return True, "ok"
    raw_ids = {obs_id(r) for r in raw_all}
    for row in jsonl_rows:
        if obs_id(row) not in raw_ids:
            if row.get("phase") == "END":
                return False, "COLLECTOR_SYNTHETIC_END"
            return False, "PROVENANCE_MISMATCH"
    return True, "ok"
