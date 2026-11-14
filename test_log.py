#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The launch log: how it is written (private, never through a link, trimmed in place, no injected control characters),
what reaches it (errors, exit codes, an app's stderr) and when a failed-start popup is sent."""
import contextlib, io, os, signal, socket, subprocess, sys, tempfile, threading, time, unittest

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
                 "notify_failure", "notify_crash", "notify_ended", "journal_tag", "HOMES", "RUNNING", "SOURCES", "LOG", "STATE",
                 "subprocess", "time")
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
                 notify_failure=self.popups.append, notify_crash=lambda n, sg: self.popups.append((n, sg)),
                 notify_ended=lambda n, c: self.popups.append(("ended", n, c)), journal_tag=lambda n: None, HOMES=f"{self.d}/h", RUNNING=f"{self.d}/r",
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


    def clock(self, *times):
        """launch() sees these times from time.time() in turn (the last one repeats); everything else is the real clock."""
        seq = list(times)
        class FakeTime:
            def __getattr__(s, n): return getattr(time, n)
            @staticmethod
            def time(): return seq.pop(0) if len(seq) > 1 else seq[0]
        A["time"] = FakeTime()

    def test_a_crash_is_told_by_its_signal_in_the_log_and_in_a_popup_even_after_a_long_run(self):
        self.child = "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"
        self.clock(0, 3600)                                                    # an hour in: not a failed start
        code, _ = self.launch()
        self.assertEqual(code, 139); self.assertIn("] EXIT code=139 (SIGSEGV)\n", self.log())
        self.assertEqual(self.popups, [("Foo", "SIGSEGV")])

    def test_a_quick_crash_is_a_crash_popup_not_a_failed_start_popup(self):
        self.child = "import os, signal; os.kill(os.getpid(), signal.SIGABRT)"
        code, _ = self.launch()
        self.assertEqual(code, 134); self.assertEqual(self.popups, [("Foo", "SIGABRT")])

    def test_an_error_exit_after_a_long_run_says_so_only_when_there_is_no_terminal(self):
        self.child = "import sys; sys.exit(2)"
        self.clock(0, 60)
        self.launch()                                                          # stderr here is not a terminal
        self.assertEqual(self.popups, [("ended", "Foo", 2)])
        self.popups.clear(); self.clock(0, 60)
        class Tty(io.StringIO):
            def isatty(s): return True
        with contextlib.redirect_stderr(Tty()), self.assertRaises(SystemExit):
            A["launch"]("Foo", f"{self.d}/root", {**A["DEFAULTS"], **A["NUM"], **{k: [] for k in A["LISTS"]}, "theme": 0}, [])
        self.assertEqual(self.popups, [])                                      # the terminal already showed it

    def test_a_clean_exit_and_a_stop_by_signal_say_nothing(self):
        self.child = "pass"; self.clock(0, 60)
        self.launch(); self.assertEqual(self.popups, [])
        self.child = "import time; time.sleep(30)"
        t = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)); t.start(); self.addCleanup(t.cancel)
        self.launch(); self.assertEqual(self.popups, [])

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


class NotifyCrash(unittest.TestCase):
    def test_a_crash_is_told_by_its_signal_whenever_it_happens(self):
        cs = A["crash_signal"]
        self.assertEqual(cs(139), "SIGSEGV"); self.assertEqual(cs(134), "SIGABRT"); self.assertEqual(cs(-11), "SIGSEGV")    # bwrap's 128+n, or a direct signal
        for rc in (0, 1, 2, 128, 130, 143, -9, -15, 255, 300): self.assertIsNone(cs(rc))                                # exits, Ctrl-C, SIGTERM, SIGKILL: not crashes

    def sent(self, call):
        import shutil
        calls = []
        def fake_run(cmd, **k): calls.append(cmd); return subprocess.CompletedProcess(cmd, 0, "no actions", "")
        class FakeShutil:
            def __getattr__(s, n): return getattr(shutil, n)
            which = staticmethod(lambda x: "/usr/bin/notify-send" if x == "notify-send" else None)
        class FakeSub:
            def __getattr__(s, n): return getattr(subprocess, n)
            run = staticmethod(fake_run)
        keep = {k: A[k] for k in ("shutil", "subprocess")}
        A.update(shutil=FakeShutil(), subprocess=FakeSub()); self.addCleanup(A.update, keep)
        call()
        return calls[-1]

    def test_the_crash_popup_is_critical_and_names_the_app_and_the_signal(self):
        sent = self.sent(lambda: A["notify_crash"]("chrome", "SIGSEGV"))
        self.assertIn("critical", sent); self.assertTrue(any("chrome crashed (segmentation fault, SIGSEGV)" in x for x in sent))

    def test_the_ended_popup_is_normal_urgency_and_gives_the_exit_code(self):
        sent = self.sent(lambda: A["notify_ended"]("chrome", 1))
        self.assertIn("normal", sent); self.assertTrue(any("chrome stopped with an error (exit code 1)" in x for x in sent))

    def test_exit_codes_that_mean_somebody_stopped_it(self):
        self.assertEqual(A["STOP_CODES"], (129, 130, 143))


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
