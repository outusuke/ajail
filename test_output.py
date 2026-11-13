#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Text that comes from an AppImage must not reach your terminal with control characters (OSC 52 clipboard writes,
# title reports and similar). These tests feed hostile text through the paths that print it.
import contextlib, io, os, socket, subprocess, sys, tempfile, types, unittest

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
                 "HOMES", "RUNNING", "SOURCES", "LOG", "platform")
        self.keep = {k: A[k] for k in names}
        A.update(check_identity=lambda *a: None, private_net=lambda c: False, sweep_runtime=lambda: None,
                 start_bus=lambda *a: None, seccomp_file=lambda h: tempfile.TemporaryFile(), work_dir=lambda: None, make_files=lambda *a: {},
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

    def test_partial_line_shows_without_waiting_for_newline(self):
        flag = f"{self.d}/seen"
        app = ("import sys, os, time\n"
               "sys.stdout.write('prompt> '); sys.stdout.flush()\n"
               "for _ in range(30):\n"
               "    if os.path.exists(%r): break\n"
               "    time.sleep(0.1)\n"
               "print('released' if os.path.exists(%r) else 'stuck')\n") % (flag, flag)
        class P(subprocess.Popen):
            def __init__(s, cmd, **k): super().__init__([sys.executable, "-c", app], **k)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            Popen = P
        A["subprocess"] = FakeSub()
        class Watch(Tty):
            def write(s, t):
                if "prompt>" in t: open(flag, "w").close()      # the 'terminal' saw the prompt while the app is still waiting
                return super().write(t)
        out, cfg = Watch(), dict(A["DEFAULTS"], **A["NUM"], theme=0)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            A["launch"]("Foo", f"{self.d}/root", cfg, [])
        self.assertIn("released", out.getvalue())

    def test_no_filter_on_a_supported_cpu_refuses_to_run(self):
        """If the system-call filter could not be created, the app must not run unfiltered."""
        A["seccomp_file"] = lambda h: None
        A["platform"] = types.SimpleNamespace(machine=lambda: "x86_64")
        err = io.StringIO()
        cfg = dict(A["DEFAULTS"], **A["NUM"], theme=0)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            A["launch"]("Foo", f"{self.d}/root", cfg, [])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("system-call filter", err.getvalue())
        self.assertFalse(os.path.exists(self.term), "the app was started")

    def test_endless_stderr_without_newline_is_bounded(self):
        """A flood with no newline must not become one giant line (memory) or get past the log cap."""
        app = "import sys; sys.stderr.write('A' * 3000000 + '\\nend\\n')"
        class P(subprocess.Popen):
            def __init__(s, cmd, **k): super().__init__([sys.executable, "-c", app], **k)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            Popen = P
        A["subprocess"] = FakeSub()
        out, err = self.run_launch(False)
        self.assertIn("A" * 3000000 + "\nend\n", err)         # the terminal still gets everything
        lines = open(f"{self.d}/log").read().splitlines()
        self.assertLess(max(len(l) for l in lines), 70000)          # no single giant log line
        self.assertLess(sum(len(l) + 1 for l in lines), A["LOG_RUN_MAX"] + 70000 + 200)     # the per-run log cap holds

class PidFiles(unittest.TestCase):
    """The pid file says which process wrote it, so a later process that reuses the PID is not taken for a running app."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.keep = {k: A[k] for k in ("RUNNING",)}
        A["RUNNING"] = self.d
        # a real process with "ajail" in its command line, like an ajail that is running an app
        self.p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "ajail"])
        self.pid = str(self.p.pid)
        import ast
        tree = ast.parse(open(f"{HERE}/ajail-gui").read())
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("live_ajail", "proc_start", "pid_app", "running_pids")]
        self.G = {"os": os, "RUNNING": self.d}
        exec(compile(ast.Module(body, []), "ajail-gui", "exec"), self.G)
    def tearDown(self):
        A.update(self.keep); self.p.kill(); self.p.wait()

    def write(self, text): open(f"{self.d}/{self.pid}", "w").write(text)
    def seen(self):                                            # (ajail's answer, the GUI's answer)
        return A["running"]("Foo"), self.G["running_pids"]("Foo")

    def test_matching_start_time_counts(self):
        start = A["proc_start"](self.pid)
        self.assertTrue(start and start.isdigit())
        self.assertEqual(start, self.G["proc_start"](self.pid))
        self.write(f"Foo\n{start}")
        self.assertEqual(self.seen(), ([self.pid], [int(self.pid)]))
        self.assertEqual(A["running"]("Bar"), [])

    def test_other_start_time_does_not_count(self):          # the PID was reused by another ajail process
        self.write(f"Foo\n{int(A['proc_start'](self.pid)) + 1}")
        self.assertEqual(self.seen(), ([], []))

    def test_old_or_broken_pid_files_are_ignored(self):
        for text in ("Foo", "Foo\n", "Foo\nnot-a-number", "", "\n123"):
            self.write(text)
            self.assertEqual(self.seen(), ([], []), repr(text))

    def test_process_without_ajail_in_its_command_line_does_not_count(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            open(f"{self.d}/{p.pid}", "w").write(f"Foo\n{A['proc_start'](p.pid)}")
            self.assertEqual(A["running"]("Foo"), [])
        finally:
            p.kill(); p.wait()

    def test_launch_writes_name_and_start_time(self):
        d = tempfile.mkdtemp()
        names = ("check_identity", "private_net", "sweep_runtime", "start_bus", "seccomp_file", "work_dir", "make_files",
                 "bwrap_args", "limit_prefix", "forward_files", "stop_bus", "running", "notify_failure", "log",
                 "HOMES", "SOURCES", "LOG", "subprocess")
        keep = {k: A[k] for k in names}
        seen = f"{d}/seen"
        app = "import os; open(%r, 'w').write(open(%r + '/' + str(os.getppid())).read())" % (seen, self.d)    # the parent is the test, i.e. launch()
        class P(subprocess.Popen):
            def __init__(s, cmd, **k): super().__init__([sys.executable, "-c", app], **k)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            Popen = P
        A.update(check_identity=lambda *a: None, private_net=lambda c: False, sweep_runtime=lambda: None,
                 start_bus=lambda *a: None, seccomp_file=lambda h: tempfile.TemporaryFile(), work_dir=lambda: None,
                 make_files=lambda *a: {}, bwrap_args=lambda *a: [], limit_prefix=lambda *a: [], forward_files=lambda e: [],
                 stop_bus=lambda b: None, running=lambda n: False, notify_failure=lambda n: None, log=lambda *a, **k: None,
                 HOMES=f"{d}/h", SOURCES=f"{d}/s", LOG=f"{d}/log", subprocess=FakeSub())
        os.makedirs(f"{d}/root"); open(f"{d}/root/AppRun", "w").close()
        try:
            cfg = dict(A["DEFAULTS"], **A["NUM"], theme=0)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                A["launch"]("Foo", f"{d}/root", cfg, [])
        finally:
            A.update(keep)
        self.assertEqual(open(seen).read(), f"Foo\n{A['proc_start'](os.getpid())}")
        self.assertEqual(os.listdir(self.d), [])                        # launch removed its own pid file again

class XauthFailsClosed(unittest.TestCase):
    """If the X cookie file can't be parsed down to one display's cookie, it must not be shared whole."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.xa = f"{self.d}/Xauthority"
        open(self.xa, "wb").write(b"\x00\x01garbage-not-an-xauthority-file")
        self.keep = {k: A[k] for k in ("work_dir", "warn")}
        self.warned = []
        A.update(work_dir=lambda: self.d, warn=lambda m, n=None: self.warned.append(m))
        self.env = {k: os.environ.get(k) for k in ("DISPLAY", "XAUTHORITY")}
        os.environ.update(DISPLAY=":0", XAUTHORITY=self.xa)
    def tearDown(self):
        A.update(self.keep)
        for k, v in self.env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def test_unparsable_cookie_file_is_not_bound(self):
        cfg = dict(A["DEFAULTS"]); cfg.update(A["NUM"]); cfg["x11"] = 1
        cfg.update({k: [] for k in A["LISTS"]})
        argv = A["bwrap_args"]("Foo", f"{self.d}/root", cfg, ["/app/AppRun"])
        self.assertNotIn(self.xa, argv)
        self.assertNotIn("XAUTHORITY", argv)
        self.assertTrue(any("X cookie" in m for m in self.warned))

    def test_only_this_machines_cookies_are_kept(self):
        def entry(fam, addr, num, data=b"\x11" * 16):
            return fam.to_bytes(2, "big") + b"".join(len(f).to_bytes(2, "big") + f for f in (addr, num, b"MIT-MAGIC-COOKIE-1", data))
        me = socket.gethostname().encode()
        mine, wild, nodisp = entry(256, me, b"0"), entry(65535, b"", b"0"), entry(65535, b"", b"")
        other = [entry(256, b"foreign-host", b"0"),       # same display number, another machine
                 entry(256, me, b"1"), entry(65535, b"", b"1")]      # this machine, another display
        src = f"{self.d}/src.xauth"
        open(src, "wb").write(b"".join([other[0], mine, other[1], wild, other[2], nodisp]))
        out = A["scoped_xauth"](src, ":0")
        self.assertEqual(open(out, "rb").read(), mine + wild + nodisp)
        open(src, "wb").write(other[0])                             # nothing of ours left: not shared at all
        self.assertIsNone(A["scoped_xauth"](src, ":0"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
