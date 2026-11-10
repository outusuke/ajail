#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Text that comes from an AppImage must not reach your terminal with control characters (OSC 52 clipboard writes,
# title reports and similar). These tests feed hostile text through the paths that print it.
import contextlib, io, os, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))

def load_ajail():
    src = open(f"{HERE}/ajail").read().rstrip()
    assert src.endswith("\nmain()")
    ns = {"__name__": "ajail_under_test"}
    exec(compile(src[:-len("main()")], "ajail", "exec"), ns)
    return ns

A = load_ajail()
EVIL = "bad file \x1b]52;c;SGVsbG8=\x07 \x1b[2J name \x9b31m end"

class FakeSubprocess:
    """Stands in for the subprocess module inside ajail: every command 'fails' with hostile stderr."""
    def __getattr__(self, n): return getattr(subprocess, n)
    @staticmethod
    def run(cmd, *a, **k): return subprocess.CompletedProcess(cmd, 1, "", EVIL)

class Errors(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.keep = {k: A[k] for k in ("subprocess", "EXTRACT", "UNSQ", "notify", "MOUNTS", "need_squashfuse", "sweep_mounts")}
        A.update(subprocess=FakeSubprocess(), EXTRACT=f"{self.d}/x", UNSQ="/bin/true", notify=lambda *a, **k: None,
                 MOUNTS=f"{self.d}/m", need_squashfuse=lambda: None, sweep_mounts=lambda: None)
        self.app = f"{self.d}/app"; open(self.app, "w").write("x")
    def tearDown(self): A.update(self.keep)

    def run_and_capture(self, fn, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            fn(*args)
        return err.getvalue()

    def check(self, out):
        for ch in ("\x1b", "\x07", "\x9b"): self.assertNotIn(ch, out)
        self.assertIn("bad file", out); self.assertIn("name", out)      # the readable part still gets through

    def test_extraction_error_is_clean(self):
        self.check(self.run_and_capture(A["ensure_extracted"], self.app, "Foo", 0))

    def test_mount_error_is_clean(self):
        self.check(self.run_and_capture(A["mount_squashfs"], self.app, 0, "Foo"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
