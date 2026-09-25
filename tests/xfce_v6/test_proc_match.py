#!/usr/bin/env python3
"""Process matching used by v6_probes.py / daily_sampler_v2.py, on a fake /proc.
termux-x11 and Claude Desktop rewrite argv[0] to the whole command line (spaces, then NUL padding);
other programs keep NUL-separated arguments. Both forms must match, neighbours must not.
    python3 test_proc_match.py -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import daily_sampler_v2 as D
import v6_probes as P


class ProcMatch(unittest.TestCase):
    def setUp(self):
        self.proc = Path(tempfile.mkdtemp(prefix="fakeproc-"))

    def tearDown(self):
        shutil.rmtree(self.proc)

    def add(self, pid, raw: bytes):
        (self.proc / str(pid)).mkdir()
        (self.proc / str(pid) / "cmdline").write_bytes(raw)

    def test_x3_space_form_with_padding(self):      # the real termux-x11 form
        self.add(101, b"termux-x11gpu com.waydefu.x11gpu :3 -noreset" + b"\0" * 40)
        self.assertEqual(P.x3_pid(str(self.proc)), 101)

    def test_x3_nul_form(self):
        self.add(102, b"termux-x11gpu\0com.waydefu.x11gpu\0:3\0-noreset\0")
        self.assertEqual(P.x3_pid(str(self.proc)), 102)

    def test_x3_neighbours_do_not_match(self):
        self.add(103, b"termux-x11gpu com.waydefu.x11gpu :30 -noreset\0")
        self.add(104, b"termux-x11 com.termux.x11 :1 -legacy-drawing\0")
        self.assertIsNone(P.x3_pid(str(self.proc)))

    def test_x1_is_stable_only(self):
        self.add(105, b"termux-x11 com.termux.x11 :10\0")
        self.add(106, b"termux-x11gpu com.waydefu.x11gpu :3\0")
        self.assertIsNone(D.x1_pid(str(self.proc)))
        self.add(107, b"termux-x11 com.termux.x11 :1 -legacy-drawing" + b"\0" * 99)
        self.assertEqual(D.x1_pid(str(self.proc)), 107)

    def test_electron_mains_both_forms_helpers_excluded(self):
        self.add(201, b"/usr/lib/claude-desktop/claude-desktop --password-store=gnome-libsecret --no-sandbox\0")
        self.add(202, b"/usr/lib/claude-desktop/claude-desktop --type=renderer --lang=en\0")
        self.add(203, b"/usr/share/cursor/cursor\0--no-sandbox\0--disable-gpu\0")
        self.add(204, b"/usr/share/cursor/cursor\0--type=gpu-process\0")
        self.add(205, b"/usr/lib/claude-desktop/chrome_crashpad_handler\0--monitor-self\0")
        self.assertEqual(D.main_processes(str(self.proc)), {"claude": [201], "cursor": [203]})


if __name__ == "__main__":
    unittest.main()
