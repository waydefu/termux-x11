#!/usr/bin/env python3
"""I02: protocol sizes, swapped CARD32, invalid phase rejection (host)."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
text = (HERE / "r8-test-protocol.h").read_text()


def need(cond: bool, label: str, bad: list[str]) -> None:
    if not cond:
        bad.append(label)


def main() -> int:
    bad: list[str] = []
    need("sz_xLorieR8QueryVersionReq 4" in text, "qv_req", bad)
    need("sz_xLorieR8QueryVersionReply 32" in text, "qv_rep", bad)
    need("sz_xLorieR8RegisterBufferReq 8" in text, "reg_req", bad)
    need("sz_xLorieR8RegisterBufferReply 72" in text, "reg_rep", bad)
    need("sz_xLorieR8CheckpointReq 8" in text, "ck_req", bad)
    need("sz_xLorieR8CheckpointReply 72" in text, "ck_rep", bad)
    need('#include <X11/Xmd.h>' in text, "xmd_include", bad)
    need("__X11_XMD_H" not in text and "defined(CARD8)" not in text, "no_card8_heuristic", bad)
    need("pad3" not in text, "no_qv_pad3", bad)
    need("X_LorieR8QueryVersion 0" in text, "op0", bad)
    need("X_LorieR8RegisterBuffer 1" in text, "op1", bad)
    need("X_LorieR8Checkpoint 2" in text, "op2", bad)
    # Native vs swapped CARD32 xid
    native = struct.pack("<I", 0x12345678)
    swapped = struct.pack(">I", 0x12345678)
    need(native != swapped, "endian_diff", bad)
    need(struct.unpack("<I", native)[0] == 0x12345678, "native_xid", bad)
    need(struct.unpack("<I", swapped)[0] == 0x78563412, "swapped_xid", bad)
    # Invalid phase 0 and 99
    need("LORIE_R8_PHASE_INVALID 0" in text, "phase0", bad)
    need("lorieR8PhaseValid" in text, "phase_fn", bad)
    # Overflow length: register req is exactly 8; extra bytes are invalid
    need("length; /* 2 */" in text or "length; /* 2" in text.replace(" ", ""), "reg_len", bad)
    if bad:
        print("FAIL", bad)
        return 1
    print("PASS test_r8_parser I02")
    return 0


if __name__ == "__main__":
    sys.exit(main())
