#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Text that comes from an AppImage must not reach your terminal with control characters (OSC 52 clipboard writes,
# title reports and similar). These tests feed hostile text through the paths that print it.
import contextlib, io, os, shutil, signal, socket, subprocess, sys, tempfile, time, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
                 "HOMES", "RUNNING", "SOURCES", "LOG", "STATE", "CONF", "platform")
        self.keep = {k: A[k] for k in names + ("wayland_path", "wayland_context", "wl_supported")}
        A.update(wayland_path=lambda: None, wayland_context=lambda *a: None, wl_supported=lambda p: False)       # never talk to the real compositor from a test
        A.update(check_identity=lambda *a: None, private_net=lambda c: False, sweep_runtime=lambda: None,
                 start_bus=lambda *a: None, seccomp_file=lambda h, a=False: tempfile.TemporaryFile(), work_dir=lambda: None, make_files=lambda *a: {},
                 bwrap_args=lambda *a: [], limit_prefix=lambda *a: [], forward_files=lambda e: [], stop_bus=lambda b: None,
                 running=lambda n: False, notify_failure=lambda n: None, log=lambda *a, **k: None,
                 HOMES=f"{self.d}/h", RUNNING=f"{self.d}/r", SOURCES=f"{self.d}/s", LOG=f"{self.d}/log", STATE=f"{self.d}/state", CONF=f"{self.d}/conf")
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

    def run_launch(self, tty, **over):
        out, err = (Tty() if tty else io.StringIO()), io.StringIO()
        cfg = dict(A["DEFAULTS"]); cfg.update(A["NUM"]); cfg["theme"] = 0; cfg.update(over)
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
    def test_launch_gives_bwrap_the_restricted_wayland_socket_and_lets_go_of_it(self):
        seen = {}
        class Ctx:
            path = "/rt/ajail/1.wl"
            def close(s): seen["closed"] = True
        A.update(wayland_path=lambda: "/rt/wayland-0", bwrap_args=lambda *a: seen.update(bw=a) or [], wl_supported=lambda p: True,
                 wayland_context=lambda wp, sock, name, inst: seen.update(ctx=(wp, sock.endswith(f"/ajail/{os.getpid()}.wl"), name)) or Ctx())
        self.run_launch(False)
        self.assertEqual(seen["bw"][-4], "/rt/ajail/1.wl")      # then the portal info descriptor, the a11y socket and the system bus socket, None here
        self.assertEqual(seen["ctx"], ("/rt/wayland-0", True, "ajail-Foo"))      # the launcher's id, not the bare name
        self.assertTrue(seen["closed"])

    def test_launch_without_a_context_binds_the_real_socket_and_strict_says_why(self):
        seen = {}
        A.update(wayland_path=lambda: "/rt/wayland-0", bwrap_args=lambda *a: seen.update(bw=a) or [])      # the compositor can't restrict
        _, err = self.run_launch(False)
        self.assertIsNone(seen["bw"][-1]); self.assertNotIn("restrict", err)                    # quiet unless strict was asked for
        self.assertEqual(seen["bw"][2]["wayland_strict"], 0)                                     # automatic is off where it can't work
        _, err = self.run_launch(False, wayland_strict=1)
        self.assertIn("Wayland only if restricted", err)
        seen.clear(); _, err = self.run_launch(False, wayland=0)                                 # Wayland off: no socket, no context, no warning
        self.assertIsNone(seen["bw"][-1]); self.assertNotIn("restrict", err)

    def test_automatic_strict_is_on_where_the_compositor_supports_it_and_fails_closed(self):
        seen = {}
        A.update(wayland_path=lambda: "/rt/wayland-0", bwrap_args=lambda *a: seen.update(bw=a) or [], wl_supported=lambda p: True,
                 wayland_context=lambda *a: None)                                               # supported, but this attempt failed
        self.assertIsNone(A["DEFAULTS"]["wayland_strict"])                                       # not set anywhere = automatic
        _, err = self.run_launch(False)
        self.assertEqual(seen["bw"][2]["wayland_strict"], 1); self.assertIn("Wayland is off", err)
        _, err = self.run_launch(False, wayland_strict=0)                                        # you said no: the real socket, no warning
        self.assertEqual(seen["bw"][2]["wayland_strict"], 0); self.assertNotIn("Wayland is off", err)

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
        A["seccomp_file"] = lambda h, a=False: None
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
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("live_ajail", "proc_start", "live_pid", "pid_app", "descendants", "is_pasta", "send_signal", "running_pids", "stop_app")]
        self.G = {"os": os, "signal": signal, "RUNNING": self.d}
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

    def test_stop_signals_the_process_that_wrote_the_pid_file(self):
        self.write(f"Foo\n{A['proc_start'](self.pid)}")
        self.assertEqual(A["stop_app"]("Foo"), 1)
        self.assertEqual(self.p.wait(5), -signal.SIGTERM)

    def test_stop_leaves_a_reused_pid_alone(self):
        """The list of PIDs is out of date: the number now belongs to another process (other start time)."""
        self.write(f"Foo\n{int(A['proc_start'](self.pid)) + 1}")
        keep = A["running"]; A["running"] = lambda n: [self.pid]
        self.G["running_pids"] = lambda n: [int(self.pid)]
        try:
            self.assertEqual(A["stop_app"]("Foo"), 0)
            self.G["stop_app"]("Foo")
        finally:
            A["running"] = keep
        self.assertIsNone(self.p.poll())                         # still alive: nobody sent it anything

    def test_gui_stop_signals_the_right_process(self):
        self.write(f"Foo\n{A['proc_start'](self.pid)}")
        self.G["stop_app"]("Foo")
        self.assertEqual(self.p.wait(5), -signal.SIGTERM)

    def entry(self, cstart, box):                              # 999999 is an ajail that is gone
        open(f"{self.d}/999999", "w").write(f"Foo\n123\n{box.pid}\n{cstart}")

    def sleeper(self):
        box = subprocess.Popen(["sleep", "60"]); self.addCleanup(lambda: box.kill() or box.wait())
        return box

    def tree_of_sleeps(self, pre=""):
        top = subprocess.Popen(["sh", "-c", pre + "sleep 60 & sleep 60 & wait"]); self.addCleanup(lambda: top.kill() or top.wait())
        for _ in range(100):
            kids = A["descendants"](top.pid)
            if len(kids) == 2: return top, kids
            time.sleep(0.05)
        self.fail("the sleeps did not start")

    def gone(self, pid):
        try:
            with open(f"/proc/{pid}/stat", "rb") as f: return f.read().rsplit(b")", 1)[1].split()[0] == b"Z"
        except OSError: return True

    def all_gone(self, kids):
        for _ in range(100):
            if all(self.gone(p) for p, _ in kids): break
            time.sleep(0.05)
        return all(self.gone(p) for p, _ in kids)

    def test_a_run_whose_ajail_died_still_counts_through_its_sandbox_and_stop_reaches_it(self):
        box = self.sleeper()
        self.entry("1", box); self.assertEqual(self.seen(), ([], []))                   # reused PID
        self.entry(A["proc_start"](box.pid), box); self.assertEqual(self.seen(), (["999999"], [999999]))
        self.assertEqual(A["running"]("Bar"), [])
        self.assertEqual(A["stop_app"]("Foo"), 1); self.assertEqual(box.wait(5), -signal.SIGTERM)
        self.assertEqual(self.seen(), ([], []))

    def test_the_window_stops_a_run_through_its_sandbox_when_ajail_is_gone(self):
        box = self.sleeper()
        self.entry(A["proc_start"](box.pid), box)
        self.G["stop_app"]("Foo"); self.assertEqual(box.wait(5), -signal.SIGTERM)

    def test_descendants_lists_every_process_below_a_pid_with_its_start_time(self):
        top, kids = self.tree_of_sleeps()
        self.assertEqual([s for _, s in kids], [A["proc_start"](p) for p, _ in kids])
        self.assertNotIn(top.pid, [p for p, _ in kids])
        self.assertEqual(sorted(self.G["descendants"](top.pid)), sorted(kids))

    def test_stop_of_a_run_whose_ajail_died_also_ends_what_the_sandbox_started(self):
        for stop in (A["stop_app"], self.G["stop_app"]):
            top, kids = self.tree_of_sleeps()
            self.entry(A["proc_start"](top.pid), top)
            stop("Foo"); top.wait(5)
            self.assertTrue(self.all_gone(kids), stop)

    def test_finish_tree_kills_what_ignores_the_stop_once_the_grace_is_over(self):
        top, kids = self.tree_of_sleeps("trap '' TERM; ")
        A["signal_tree"](kids, signal.SIGTERM); A["finish_tree"](kids, 0.3)
        self.assertTrue(self.all_gone(kids))

    def test_stop_leaves_pasta_to_end_with_its_app(self):
        with open(f"{self.d}/pasta", "w") as f: f.write("#!/bin/sh\nwhile :; do sleep 1; done\n")      # a multicall sleep refuses to run as "pasta"
        os.chmod(f"{self.d}/pasta", 0o755)
        net = subprocess.Popen([f"{self.d}/pasta"]); self.addCleanup(lambda: net.kill() or net.wait())
        time.sleep(0.2); self.assertIsNone(net.poll(), "the fake pasta did not start")
        plain = self.sleeper()
        for f in (A["is_pasta"], self.G["is_pasta"]): self.assertTrue(f(net.pid)); self.assertFalse(f(plain.pid))
        self.entry(A["proc_start"](net.pid), net)
        for stop in (A["stop_app"], self.G["stop_app"]): stop("Foo")
        time.sleep(0.3); self.assertIsNone(net.poll(), "pasta gets no signal from us")

    def test_signal_tree_leaves_a_reused_pid_alone(self):
        box = self.sleeper()
        A["signal_tree"]([(box.pid, str(int(A["proc_start"](box.pid)) + 1))], signal.SIGKILL)
        self.assertIsNone(box.poll())

    def test_stop_commands_use_stop_app(self):
        calls, keep = [], {k: A[k] for k in ("running", "stop_app")}
        A["running"] = lambda n: ["1"]; A["stop_app"] = lambda n: calls.append(n) or 1
        try:
            with contextlib.redirect_stdout(io.StringIO()): A["cmd_stop"](["Foo"])
        finally:
            A.update(keep)
        self.assertEqual(calls, ["Foo"])
        gui = open(f"{HERE}/ajail-gui").read()
        self.assertIn("stop_app(scope)", gui)
        self.assertEqual(gui.count("os.kill("), 1)               # only the fallback inside stop_app itself

    def test_launch_writes_name_and_start_time(self):
        d = tempfile.mkdtemp()
        names = ("check_identity", "private_net", "sweep_runtime", "start_bus", "seccomp_file", "work_dir", "make_files",
                 "bwrap_args", "limit_prefix", "forward_files", "stop_bus", "running", "notify_failure", "log",
                 "HOMES", "SOURCES", "LOG", "subprocess")
        keep = {k: A[k] for k in names + ("wayland_path", "wayland_context", "wl_supported")}
        A.update(wayland_path=lambda: None, wayland_context=lambda *a: None, wl_supported=lambda p: False)       # never talk to the real compositor from a test
        seen = f"{d}/seen"
        app = "import os; open(%r, 'w').write(open(%r + '/' + str(os.getppid())).read())" % (seen, self.d)    # the parent is the test, i.e. launch()
        class P(subprocess.Popen):
            def __init__(s, cmd, **k): super().__init__([sys.executable, "-c", app], **k)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            Popen = P
        A.update(check_identity=lambda *a: None, private_net=lambda c: False, sweep_runtime=lambda: None,
                 start_bus=lambda *a: None, seccomp_file=lambda h, a=False: tempfile.TemporaryFile(), work_dir=lambda: None,
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
        name, start, child, cstart = open(seen).read().split("\n")           # the app is the sandbox here
        self.assertEqual((name, start), ("Foo", A["proc_start"](os.getpid())))
        self.assertTrue(child.isdigit() and cstart.isdigit())
        self.assertEqual(os.listdir(self.d), [])                        # launch removed its own pid file again

class XauthFailsClosed(unittest.TestCase):
    """If the X cookie file can't be parsed down to one display's cookie, it must not be shared whole."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.xa = f"{self.d}/Xauthority"
        open(self.xa, "wb").write(b"\x00\x01garbage-not-an-xauthority-file")
        self.keep = {k: A[k] for k in ("work_dir", "warn", "LOG", "STATE", "CONF")}
        self.warned = []
        A.update(work_dir=lambda: self.d, warn=lambda m, n=None: self.warned.append(m),
                 LOG=f"{self.d}/state/ajail.log", STATE=f"{self.d}/state", CONF=f"{self.d}/conf")
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

class PrivateNetLaunch(unittest.TestCase):
    """launch() with net_private: the filter is written to a file for pasta to hand on, nothing is inherited, and the file is
    gone afterwards. Without pasta the app does not start at all (it never falls back to the shared network)."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        names = ("check_identity", "sweep_runtime", "start_bus", "work_dir", "limit_prefix", "forward_files", "stop_bus", "running",
                 "notify_failure", "log", "HOMES", "RUNNING", "SOURCES", "LOG", "STATE", "CONF", "shutil", "subprocess", "pasta_problem")
        self.keep = {k: A[k] for k in names + ("wayland_path", "wayland_context", "wl_supported")}
        A.update(wayland_path=lambda: None, wayland_context=lambda *a: None, wl_supported=lambda p: False)       # never talk to the real compositor from a test
        self.wd, self.seen, self.pasta = f"{self.d}/wd", [], True
        test = self
        class P(subprocess.Popen):
            def __init__(s, cmd, **k):
                n = len(A["PASTA"])
                sec = cmd[n + 4] if cmd[:n] == A["PASTA"] and cmd[n:n + 2] == ["/bin/sh", "-c"] else None
                test.seen.append({"cmd": cmd, "kw": k, "sec": sec, "sec_bytes": open(sec, "rb").read() if sec else None,
                                  "resolv": open(f"{test.wd}/resolv.conf").read() if os.path.exists(f"{test.wd}/resolv.conf") else None})
                super().__init__([sys.executable, "-c", "pass"], **k)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            Popen = P
        class FakeShutil:
            def __getattr__(s, n): return getattr(shutil, n)
            @staticmethod
            def which(x): return f"/usr/bin/{x}" if x != "pasta" or test.pasta else None
        A.update(check_identity=lambda *a: None, sweep_runtime=lambda: None, start_bus=lambda *a: None, work_dir=lambda: self.wd,
                 limit_prefix=lambda *a: [], forward_files=lambda e: [], stop_bus=lambda b: None, running=lambda n: False,
                 notify_failure=lambda n: None, log=lambda *a, **k: None, HOMES=f"{self.d}/h", RUNNING=f"{self.d}/r",
                 SOURCES=f"{self.d}/s", LOG=f"{self.d}/log", STATE=f"{self.d}/state", CONF=f"{self.d}/conf", shutil=FakeShutil(), subprocess=FakeSub(), pasta_problem=lambda: None)
        os.makedirs(f"{self.d}/root"); open(f"{self.d}/root/AppRun", "w").close()
    def tearDown(self): A.update(self.keep)

    def launch(self, **kw):
        cfg = dict(A["DEFAULTS"]); cfg.update(A["NUM"]); cfg.update({k: [] for k in A["LISTS"]}); cfg.update(theme=0, **kw)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as r:
            A["launch"]("Foo", f"{self.d}/root", cfg, [])
        return r.exception.code, err.getvalue()

    def test_the_filter_goes_through_a_file_and_everything_is_removed_after(self):
        code, _ = self.launch(net=1, net_private=1)
        self.assertEqual(code, 0)
        (s,) = self.seen
        n = len(A["PASTA"])
        self.assertEqual(s["cmd"][:n], A["PASTA"])
        self.assertEqual(s["cmd"][n:n + 5], ["/bin/sh", "-c", 'exec 9<"$1"; shift; exec "$@"', "sh", f"{self.wd}/seccomp.bpf"])
        self.assertEqual(s["cmd"][n + 5], "bwrap")
        self.assertEqual(s["cmd"][s["cmd"].index("--seccomp") + 1], "9")
        self.assertEqual(s["sec_bytes"], A["seccomp_file"](0).read())          # the same filter a plain run would pass
        self.assertFalse(s["kw"].get("pass_fds"), "pasta would close inherited descriptors")
        self.assertEqual(s["resolv"], "nameserver 169.254.1.1\n")
        self.assertFalse(os.path.exists(self.wd), "the filter file is removed when the app ends")

    def test_without_pasta_the_app_does_not_start(self):
        self.pasta = False
        code, err = self.launch(net=1, net_private=1)
        self.assertNotEqual(code, 0); self.assertIn("needs pasta", err); self.assertIn("passt", err)
        self.assertEqual(self.seen, []); self.assertFalse(os.path.exists(self.wd))

    def test_without_a_tun_device_the_app_does_not_start_and_the_message_says_why(self):
        A["pasta_problem"] = self.keep["pasta_problem"]
        with mock.patch.object(os, "access", lambda p, m: p != "/dev/net/tun" and os.path.exists(p)):
            self.assertIn("modprobe tun", A["pasta_problem"]())
            code, err = self.launch(net=1, net_private=1)
        self.assertNotEqual(code, 0); self.assertRegex(err, r"'Keep localhost private' cannot work: /dev/net/tun"); self.assertIn("turn that option off", err)
        self.assertEqual(self.seen, []); self.assertFalse(os.path.exists(self.wd))
        with mock.patch.object(os, "access", lambda p, m: True): self.assertIsNone(A["pasta_problem"]())

    def test_nothing_changes_without_the_switch_or_with_the_network_off(self):
        self.pasta = False                                          # not needed, so not missed
        for kw in ({"net": 1, "net_private": 0}, {"net": 0, "net_private": 1}):
            self.seen.clear()
            code, err = self.launch(**kw)
            self.assertEqual(code, 0, kw)
            (s,) = self.seen
            self.assertEqual(s["cmd"][0], "bwrap", kw)                # no pasta in front
            self.assertNotIn("--uid", s["cmd"]); self.assertIsNone(s["sec"]); self.assertIsNone(s["resolv"])
            self.assertTrue(s["kw"].get("pass_fds"), "the filter is still passed as a descriptor")
            self.assertNotEqual(s["cmd"][s["cmd"].index("--seccomp") + 1], "9", kw)

if __name__ == "__main__":
    unittest.main(verbosity=2)
