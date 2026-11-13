#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The launch log: how it is written (private, never through a link, trimmed in place, no injected control characters),
what reaches it (errors, exit codes, an app's stderr) and when a failed-start popup is sent."""
import contextlib, io, os, signal, socket, subprocess, sys, tempfile, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))

def load():
    src = open(f"{HERE}/ajail").read().rstrip()
    assert src.endswith("\nmain()")
    ns = {"__name__": "ajail_under_test"}
    exec(compile(src[:-len("main()")], "ajail", "exec"), ns)
    return ns

A = load()

class LogFile(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.keep = {k: A[k] for k in ("LOG", "STATE", "LOG_MAX", "LOG_KEEP")}
        A.update(LOG=f"{self.d}/state/ajail.log", STATE=f"{self.d}/state")
        self.addCleanup(A.update, self.keep)
        self.addCleanup(A["NO_LOG"].__setitem__, 0, False)
        self.addCleanup(A["LOG_NAME"].__setitem__, 0, None)

    def text(self): return open(A["LOG"]).read()

    def test_control_characters_and_line_breaks_in_a_message_do_not_reach_the_log(self):
        A["log"]("bad \x1b]52;c;SGVsbG8=\x07 name\nfake line [x] EXIT code=0 \x9b31m end", "app")
        t = self.text()
        self.assertEqual(t.count("\n"), 1)
        self.assertNotRegex(t, r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
        self.assertIn("[app] bad ]52;c;SGVsbG8= name fake line", t)

    def test_a_log_that_is_a_link_is_not_followed(self):
        os.makedirs(A["STATE"]); target = f"{self.d}/target"; open(target, "w").write("keep\n")
        os.symlink(target, A["LOG"])
        self.assertIsNone(A["open_log"]())
        A["log"]("hello", "app")
        self.assertEqual(open(target).read(), "keep\n")

    def test_the_log_is_trimmed_in_place_also_by_app_output(self):
        A.update(LOG_MAX=2000, LOG_KEEP=5)
        f = A["open_log"]()
        before = os.fstat(f.fileno()).st_ino
        for i in range(200): A["log_write"](f, f"[app] line {i:04d} " + "x" * 30 + "\n")
        f.close()
        lines = self.text().splitlines()
        self.assertLess(len(lines), 80)                                   # it did not grow to 200 lines
        self.assertEqual(lines[-1][:16], "[app] line 0199 ")                # the newest line is kept
        self.assertTrue(all(l.endswith("x" * 30) for l in lines))         # no line is cut in half
        self.assertEqual(os.stat(A["LOG"]).st_ino, before)                # truncated, not replaced

    def test_die_logs_the_error_under_the_apps_name_once_a_run_has_started(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            A["die"]("before any run")
        self.assertFalse(os.path.exists(A["LOG"]))                        # no name yet: nothing logged
        A["LOG_NAME"][0] = "Foo"
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            A["die"]("no Wayland session found")
        self.assertRegex(self.text(), r"\[Foo\] ERROR no Wayland session found\n")
        os.unlink(A["LOG"]); A["NO_LOG"][0] = True
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            A["die"]("while previewing")
        self.assertFalse(os.path.exists(A["LOG"]))                        # preview never writes to the log

    def test_exit_code_is_the_shells_128_plus_signal(self):
        self.assertEqual([A["exit_code"](x) for x in (0, 3, -9, -15)], [0, 3, 137, 143])


class Launch(unittest.TestCase):
    """launch() with a stand-in for bwrap: a small Python child that writes to stderr, exits or waits to be stopped."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        names = ("check_identity", "sweep_runtime", "start_bus", "work_dir", "limit_prefix", "forward_files", "stop_bus", "running",
                 "notify_failure", "journal_tag", "HOMES", "RUNNING", "SOURCES", "LOG", "STATE", "subprocess")
        self.keep = {k: A[k] for k in names}
        self.sigs = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        self.child, self.popups = "pass", []
        test = self
        class P(subprocess.Popen):
            def __init__(s, cmd, **k): super().__init__([sys.executable, "-c", test.child], **k)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            Popen = P
        A.update(check_identity=lambda *a: None, sweep_runtime=lambda: None, start_bus=lambda *a: None, work_dir=lambda: f"{self.d}/wd",
                 limit_prefix=lambda *a: [], forward_files=lambda e: [], stop_bus=lambda b: None, running=lambda n: False,
                 notify_failure=self.popups.append, journal_tag=lambda n: None, HOMES=f"{self.d}/h", RUNNING=f"{self.d}/r",
                 SOURCES=f"{self.d}/s", LOG=f"{self.d}/state/ajail.log", STATE=f"{self.d}/state", subprocess=FakeSub())
        os.makedirs(f"{self.d}/root"); open(f"{self.d}/root/AppRun", "w").close()
    def tearDown(self):
        A.update(self.keep)
        for s, h in self.sigs.items(): signal.signal(s, h)
        A["LOG_NAME"][0] = None

    def launch(self):
        cfg = dict(A["DEFAULTS"]); cfg.update(A["NUM"]); cfg.update({k: [] for k in A["LISTS"]}); cfg.update(theme=0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as r:
            A["launch"]("Foo", f"{self.d}/root", cfg, [])
        return r.exception.code, err.getvalue()

    def log(self): return open(A["LOG"]).read() if os.path.exists(A["LOG"]) else ""

    def test_the_apps_stderr_reaches_the_terminal_and_the_log_without_control_characters(self):
        self.child = "import sys; sys.stderr.write('hello \\x1b]52;c;AAAA\\x07 world\\nlast line without newline')"
        code, err = self.launch()
        self.assertEqual(code, 0)
        self.assertIn("hello ]52;c;AAAA world", err); self.assertNotIn("\x1b", err)
        self.assertIn("[Foo] hello ]52;c;AAAA world\n", self.log()); self.assertIn("[Foo] last line without newline\n", self.log())
        self.assertEqual(oct(os.stat(A["LOG"]).st_mode & 0o777), "0o600")

    def test_the_apps_stderr_still_reaches_the_terminal_when_the_log_cannot_be_opened(self):
        open(f"{self.d}/file", "w").close()
        A.update(LOG=f"{self.d}/file/state/ajail.log", STATE=f"{self.d}/file/state")        # a folder under a plain file
        self.child = "import sys; sys.stderr.write('x' * 300000 + '\\nstill here\\n')"       # more than a pipe holds
        code, err = self.launch()
        self.assertEqual(code, 0); self.assertTrue(err.endswith("still here\n"))

    def test_a_quick_failure_is_logged_and_gets_the_popup(self):
        self.child = "import sys; sys.exit(3)"
        code, _ = self.launch()
        self.assertEqual(code, 3); self.assertIn("] EXIT code=3\n", self.log()); self.assertEqual(self.popups, ["Foo"])

    def test_death_by_signal_is_logged_and_exits_like_a_shell(self):
        self.child = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
        code, _ = self.launch()
        self.assertEqual(code, 137); self.assertIn("] EXIT code=137\n", self.log())

    def test_stopping_the_app_is_not_a_failed_start(self):
        self.child = "import time; time.sleep(30)"
        t = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)); t.start(); self.addCleanup(t.cancel)
        code, _ = self.launch()
        self.assertEqual(code, 143); self.assertIn("] EXIT code=143\n", self.log())
        self.assertEqual(self.popups, [])

    def test_errors_before_the_app_starts_are_in_the_log(self):
        os.unlink(f"{self.d}/root/AppRun")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            A["launch"]("Foo", f"{self.d}/root", dict(A["DEFAULTS"]), [])
        # launch() sets the name first, so even the first check is logged
        self.assertRegex(self.log(), r"\[Foo\] ERROR no AppRun found inside this AppImage\n")


class NotifyFailure(unittest.TestCase):
    def run_it(self, fake_which, fake_run):
        import shutil
        keep = {k: A[k] for k in ("shutil", "subprocess")}
        class FakeShutil:
            def __getattr__(s, n): return getattr(shutil, n)
            which = staticmethod(fake_which)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            run = staticmethod(fake_run)
        A.update(shutil=FakeShutil(), subprocess=FakeSub()); self.addCleanup(A.update, keep)
        A["notify_failure"]("app")

    def test_without_notify_send_nothing_happens(self):
        calls = []
        self.run_it(lambda x: None, lambda *a, **k: calls.append(a))
        self.assertEqual(calls, [])

    def test_without_actions_it_is_a_plain_critical_popup_and_every_call_has_a_timeout(self):
        calls = []
        def run(cmd, **k): calls.append((cmd, k)); return subprocess.CompletedProcess(cmd, 0, "no actions", "")
        self.run_it(lambda x: "/usr/bin/notify-send" if x == "notify-send" else None, run)
        self.assertTrue(all("timeout" in k for _, k in calls), calls)
        sent = calls[-1][0]
        self.assertIn("critical", sent); self.assertTrue(any("app failed to start" in x for x in sent))

    def test_a_notify_send_that_hangs_on_help_does_not_block_ajail(self):
        calls = []
        def run2(cmd, **k):
            calls.append(cmd)
            if "--help" in cmd: raise subprocess.TimeoutExpired(cmd, 5)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.run_it(lambda x: "/usr/bin/notify-send" if x == "notify-send" else None, run2)
        self.assertIn("critical", calls[-1])


class JournalTag(unittest.TestCase):
    def reconnected(self, set_env):
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/journal"
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(path); srv.listen(2); srv.settimeout(10)
            # The fake launcher's stream is a connection to the same kind of socket, so the test can tell it is "the journal"
            code = (f"import os, socket, sys\nns = {{'__name__': 'x'}}\nsrc = open({HERE + '/ajail'!r}).read().rstrip()\n"
                    "exec(compile(src[:-len('main()')], 'ajail', 'exec'), ns)\n"
                    f"c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.connect({path!r})\n"
                    "os.dup2(c.fileno(), 1); os.dup2(c.fileno(), 2)\n"
                    "st = os.fstat(1)\n" + ("os.environ['JOURNAL_STREAM'] = f'{st.st_dev}:{st.st_ino}'\n" if set_env else "os.environ.pop('JOURNAL_STREAM', None)\n") +
                    f"ns['journal_tag']('myapp', {path!r})\nsys.stdout.write('hello\\n'); sys.stdout.flush()\n"
                    "sys.stderr.write('oops\\n'); sys.stderr.flush()\n")
            p = subprocess.Popen([sys.executable, "-c", code])
            old, _ = srv.accept()                         # the launcher's own connection: it stays quiet
            new, _ = srv.accept(); new.settimeout(10)     # the one journal_tag opens
            data = b""
            while True:
                chunk = new.recv(4096)
                if not chunk: break
                data += chunk
            p.wait(timeout=10); old.close(); new.close(); srv.close()
        return data

    def test_journal_streams_are_reconnected_with_the_apps_name(self):
        self.assertEqual(self.reconnected(True), b"myapp\n\n6\n0\n0\n0\n0\nhello\noops\n")

    def test_journal_streams_are_found_without_JOURNAL_STREAM(self):
        self.assertEqual(self.reconnected(False), b"myapp\n\n6\n0\n0\n0\n0\nhello\noops\n")

    def test_nothing_changes_when_not_started_from_the_journal(self):
        os.environ.pop("JOURNAL_STREAM", None)
        A["journal_tag"]("myapp", "/nonexistent/journal")      # no stream to match: must not even try to connect


if __name__ == "__main__":
    unittest.main(verbosity=2)
