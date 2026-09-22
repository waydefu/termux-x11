#!/usr/bin/env python3
"""Gate A telemetry counter indices, bound to the product enum.

`lorieGateADumpSummary` prints every counter as `c<i>=<value>` where `i` is the
raw array index, so `i` IS the enum value (InitOutput.c, the
`for (i = 0; i < LORIE_GATEA_COUNTER_MAX; i++)` loop). The enum starts at
`LORIE_GATEA_COUNTER_DIRECT_PUBLISH = 0` in lorie.h.

WHY THIS FILE EXISTS
--------------------
These indices were previously written inline, and two of them were wrong:
`r9_evidence.registry_state()` read c25/c26 for "X registry current" and
"renderer registry current", which are actually UNREGISTER and RESOURCE_DESTROY.
The registry-current counters are c18 and c19. Nothing about the enum makes that
obvious at a call site, so the mapping now lives in one place with a static check
(`verify-r9-support.py`) that re-derives it from lorie.h and fails if it drifts.

No R9 verdict depended on the wrong pair: the only PASS that reads a registry file
is r9-f2/attempt-02, whose `source` is `telemetry_events`, and r9-f1/attempt-03 has
no registry file at all. The three attempts that did take the counter path are all
frozen INVALID, two of them on a since-removed cell.
"""
from __future__ import annotations

import re

# index -> enum suffix, verbatim from lorie.h's LorieGateACounter
COUNTERS = {
    0: "DIRECT_PUBLISH",
    1: "DIRECT_CONSUME",
    2: "DIRECT_LOOKUP",
    3: "DIRECT_LOOKUP_FAIL",
    4: "DIRECT_DRAW",
    5: "FENCE_SATISFIED",
    6: "SEMANTIC_SUCCESS",
    7: "DIRECT_TO_LEGACY",
    8: "RELOCK",
    9: "REPAIR",
    10: "ACK",
    11: "PENDING_DEC",
    12: "AHB_ACQUIRE",
    13: "AHB_RELEASE",
    14: "EGLIMAGE_CREATE",
    15: "EGLIMAGE_DESTROY",
    16: "TEXTURE_CREATE",
    17: "TEXTURE_DELETE",
    18: "X_REGISTRY_CURRENT",
    19: "RENDERER_REGISTRY_CURRENT",
    20: "LEASE_CURRENT",
    21: "FENCE_TIMEOUT",
    22: "FENCE_ERROR",
    23: "FIRST_FAILED",
    24: "GENERATION_FATAL",
    25: "UNREGISTER",
    26: "RESOURCE_DESTROY",
    27: "GENERATION_CLOSE",
}
INDEX = {v: k for k, v in COUNTERS.items()}

# the ones the tooling names directly
X_REGISTRY_CURRENT = INDEX["X_REGISTRY_CURRENT"]                 # 18
RENDERER_REGISTRY_CURRENT = INDEX["RENDERER_REGISTRY_CURRENT"]   # 19
LEASE_CURRENT = INDEX["LEASE_CURRENT"]                           # 20
AHB_ACQUIRE = INDEX["AHB_ACQUIRE"]                               # 12
AHB_RELEASE = INDEX["AHB_RELEASE"]                               # 13
EGLIMAGE_CREATE = INDEX["EGLIMAGE_CREATE"]                       # 14
EGLIMAGE_DESTROY = INDEX["EGLIMAGE_DESTROY"]                     # 15
TEXTURE_CREATE = INDEX["TEXTURE_CREATE"]                         # 16
TEXTURE_DELETE = INDEX["TEXTURE_DELETE"]                         # 17
GENERATION_FATAL = INDEX["GENERATION_FATAL"]                     # 24
UNREGISTER = INDEX["UNREGISTER"]                                 # 25
RESOURCE_DESTROY = INDEX["RESOURCE_DESTROY"]                     # 26
GENERATION_CLOSE = INDEX["GENERATION_CLOSE"]                     # 27


def parse_from_source(lorie_h_text: str) -> dict[int, str]:
    """Re-derive the mapping from lorie.h. Used by the static verifier, never by a
    judge: a judge that re-parses product source at run time would silently follow a
    drifting enum instead of failing."""
    head = "LORIE_GATEA_COUNTER_DIRECT_PUBLISH = 0,"
    if head not in lorie_h_text:
        return {}
    blk = lorie_h_text.split(head, 1)[1].split("LORIE_GATEA_COUNTER_MAX", 1)[0]
    names = ["DIRECT_PUBLISH"] + [
        m.split("LORIE_GATEA_COUNTER_")[1]
        for m in re.findall(r"(LORIE_GATEA_COUNTER_[A-Z0-9_]+)\s*,", blk)
    ]
    return dict(enumerate(names))


def read(summary_text: str, index: int):
    """The value of c<index>, or None if the summary does not carry it.
    None is NOT OBSERVED. It is never 0."""
    m = re.search(rf"\bc{index}=(\d+)\b", summary_text)
    return int(m.group(1)) if m else None
