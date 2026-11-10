#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Text that comes from an AppImage must not reach your terminal with control characters (OSC 52 clipboard writes,
# title reports and similar). These tests feed hostile text through the paths that print it.
import contextlib, io, os, subprocess, sys, tempfile, unittest

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

class Tty(io.StringIO):
    def isatty(self): return True

class LaunchOutput(unittest.TestCase):
    """The app's stdout must be filtered like its stderr when it goes to a terminal."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        names = ("check_identity", "private_net", "sweep_runtime", "start_bus", "seccomp_file", "work_dir", "make_files",
                 "bwrap_args", "limit_prefix", "forward_files", "stop_bus", "running", "notify_failure", "log",
                 "HOMES", "RUNNING", "SOURCES", "LOG")
        self.keep = {k: A[k] for k in names}
        A.update(check_identity=lambda *a: None, private_net=lambda c: False, sweep_runtime=lambda: None,
                 start_bus=lambda *a: None, seccomp_file=lambda h: None, work_dir=lambda: None, make_files=lambda *a: {},
                 bwrap_args=lambda *a: [], limit_prefix=lambda *a: [], forward_files=lambda e: [], stop_bus=lambda b: None,
                 running=lambda n: False, notify_failure=lambda n: None, log=lambda *a, **k: None,
                 HOMES=f"{self.d}/h", RUNNING=f"{self.d}/r", SOURCES=f"{self.d}/s", LOG=f"{self.d}/log")
        os.makedirs(f"{self.d}/root"); open(f"{self.d}/root/AppRun", "w").close()
        app = ("import sys; e = %r\n"
               "sys.stdout.write('out ' + e + ' ok\\n'); sys.stdout.flush()\n"
               "sys.stderr.write('err ' + e + ' ok\\n')") % EVIL
        term = self.term = f"{self.d}/term"       # where an inherited stdout lands (the real terminal in production)
        class P(subprocess.Popen):
            def __init__(s, cmd, **k):
                if k.get("stdout") is None: k["stdout"] = open(term, "w")
                super().__init__([sys.executable, "-c", app], **k)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            Popen = P
        A["subprocess"] = FakeSub()
    def tearDown(self): A.update(self.keep); A["subprocess"] = subprocess

    def run_launch(self, tty):
        out, err = (Tty() if tty else io.StringIO()), io.StringIO()
        cfg = dict(A["DEFAULTS"]); cfg.update(A["NUM"]); cfg["theme"] = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            A["launch"]("Foo", f"{self.d}/root", cfg, [])
        shown = out.getvalue() + (open(self.term).read() if os.path.exists(self.term) else "")
        return shown, err.getvalue()

    def test_stdout_to_terminal_is_clean(self):
        out, err = self.run_launch(True)
        for ch in ("\x1b", "\x07", "\x9b"): self.assertNotIn(ch, out)
        self.assertIn("out bad file", out); self.assertIn("end ok", out)
        self.assertNotIn("\x1b", err)                              # stderr stays filtered and still reaches the log
        self.assertIn("err bad file", open(f"{self.d}/log").read())
    def test_stdout_not_a_terminal_is_untouched(self):
        out, err = self.run_launch(False)
        self.assertIn(EVIL, out)                                  # data going to a pipe or file is not altered
        self.assertNotIn("\x1b", err)

if __name__ == "__main__":
    unittest.main(verbosity=2)
