#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for ajail's settings parser: whitespace, comments and warnings about lines it can't use.
import ast, contextlib, io, os, re, stat, tempfile, threading, unittest, unittest.mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load(name):
    src = open(f"{HERE}/{name}").read().rstrip()
    assert src.endswith("\nmain()")
    ns = {"__name__": "under_test"}
    exec(compile(src[:-len("main()")], name, "exec"), ns)
    return ns

A = load("ajail")

class Parse(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        A["LOG"] = f"{self.d}/ajail.log"; A["STATE"] = self.d
        self.path = f"{self.d}/x.conf"

    def parse(self, text):
        open(self.path, "w").write(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = A["parse_cfg"](self.path)
        return d, err.getvalue()

    def test_spaces_around_equals(self):
        d, err = self.parse("net = 0\n  sound=0  \n\tgpu\t=\t0\nmem_max = 512\n")
        self.assertEqual(d["s"], {"net": 0, "sound": 0, "gpu": 0, "mem_max": 512})
        self.assertEqual(err, "")

    def test_comments(self):
        d, err = self.parse("# a comment\n\nnet=0   # no network\n   # indented\nallow=/home/me/x  # shared\n")
        self.assertEqual(d["s"], {"net": 0}); self.assertEqual(d["allow"], ["/home/me/x"])
        self.assertEqual(err, "")

    def test_hash_inside_a_word_is_kept(self):
        d, _ = self.parse("env=COLOR=#fff\nallow=/home/me/C#\n")
        self.assertEqual(d["env"], ["COLOR=#fff"]); self.assertEqual(d["allow"], ["/home/me/C#"])

    def test_every_unusable_line_warns(self):
        text = "net=2\nsound\nbogus=1\nmem_max=lots\nallow=\nprofile=weird\nflag=--nope\nportal=Nope\nnet=0\n"
        d, err = self.parse(text)
        self.assertEqual(d["s"], {"net": 0})                           # only the valid line took effect
        for frag in ("x.conf:1", "x.conf:2", "x.conf:3", "x.conf:4", "x.conf:5", "x.conf:6", "x.conf:7", "x.conf:8"):
            self.assertIn(frag, err)
        self.assertNotIn("x.conf:9", err)

    def test_warning_goes_to_the_log_but_not_for_dump(self):
        self.parse("bogus=1\n")
        self.assertIn("bogus", open(A["LOG"]).read())
        os.unlink(A["LOG"])
        old = A["NO_LOG"][0]; A["NO_LOG"][0] = True
        try: self.parse("bogus=1\n")
        finally: A["NO_LOG"][0] = old
        self.assertFalse(os.path.exists(A["LOG"]))

    def test_wayland_switch(self):
        d, err = self.parse("wayland=0\nx11=1\n")
        self.assertEqual(err, ""); self.assertEqual((d["s"]["wayland"], d["s"]["x11"]), (0, 1))
        self.assertIn("expected 0 or 1", self.parse("wayland=yes\n")[1])

    def test_known_good_file_is_silent(self):
        d, err = self.parse("net=0\nprofile=strict\nflag=--x11\nxdg=download:ro\nportal=Secret\nportal=-Secret\ntalk=org.x.Y\n")
        self.assertEqual(err, ""); self.assertEqual(d["s"]["x11"], 1)

def load_gui():
    """TALK_RE, talk_ok and the 'talk' validator that the window passes to list_group, from ajail-gui (no GTK needed)."""
    tree = ast.parse(open(f"{HERE}/ajail-gui").read())
    body = [n for n in tree.body
            if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "TALK_RE")
            or (isinstance(n, ast.FunctionDef) and n.name == "talk_ok")]
    ns = {"re": re}
    exec(compile(ast.Module(body, []), "ajail-gui", "exec"), ns)
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "list_group"
             and len(c.args) > 4 and isinstance(c.args[2], ast.Constant) and c.args[2].value == "talk"]
    assert len(calls) == 1, "the window's talk list_group call was not found"
    ns["validate"] = eval(compile(ast.Expression(calls[0].args[4]), "ajail-gui", "eval"), ns)
    return ns

class Talk(unittest.TestCase):
    setUp = Parse.setUp
    parse = Parse.parse
    def test_talk_names(self):
        good = ["org.gnome.SessionManager", "org.example.App.*", "com.a.b.c.*"]
        bad = ["org.*", "org.freedesktop.*", "*", "org..x", "a b", "-x.y"]
        for n in good + bad: self.assertEqual(A["talk_ok"](n), n in good, n)
        d, err = self.parse("".join(f"talk={n}\n" for n in good + bad))
        self.assertEqual(d["talk"], good)
        for no in range(len(good) + 1, len(good) + len(bad) + 1): self.assertIn(f"x.conf:{no}", err)

    def test_gtk_portal_takes_auto_1_or_0(self):
        for v in ("auto", "1", "0"):
            d, err = self.parse(f"gtk_portal = {v}   # file chooser\n")
            self.assertEqual(d["s"], {"gtk_portal": v}); self.assertEqual(err, "")
        for v in ("yes", "2", "AUTO", ""):
            d, err = self.parse(f"gtk_portal={v}\n")
            self.assertEqual(d["s"], {}); self.assertIn("x.conf:1", err); self.assertIn("expected auto, 1 or 0", err)

    def test_share_dirs_takes_a_list_of_folders_or_nothing(self):
        d, err = self.parse("share_dirs = download, music   # only these\n")
        self.assertEqual(d["s"], {"share_dirs": "download,music"}); self.assertEqual(err, "")
        d, err = self.parse("share_dirs=\n")
        self.assertEqual(d["s"], {"share_dirs": ""}); self.assertEqual(err, "")
        d, err = self.parse("share_dirs=download,desktop\n")
        self.assertEqual(d["s"], {}); self.assertIn("x.conf:1", err); self.assertIn("comma list", err)

    def test_share_dirs_is_all_five_until_a_file_says_otherwise(self):
        os.makedirs(f"{self.d}/apps")
        with unittest.mock.patch.dict(A, {"CONF": self.d}):
            self.assertEqual(A["load_cfg"]("x")["share_dirs"], "download,documents,pictures,music,videos")
            open(f"{self.d}/default.conf", "w").write("share_dirs=music\n")
            self.assertEqual(A["load_cfg"]("x")["share_dirs"], "music")
            open(f"{self.d}/apps/x.conf", "w").write("share_dirs=\n")      # an empty line cancels the default's
            self.assertEqual(A["load_cfg"]("x")["share_dirs"], "")

    def test_args_is_kept_as_text_and_an_unclosed_quote_is_refused(self):
        d, err = self.parse("args = --profile 'my work'   # mine\n")
        self.assertEqual(d["s"], {"args": "--profile 'my work'"}); self.assertEqual(err, "")
        d, err = self.parse("args=--title 'oops\n")
        self.assertEqual(d["s"], {}); self.assertIn("x.conf:1", err); self.assertIn("unclosed quote", err)

    def test_an_apps_args_replace_the_defaults_and_empty_cancels_them(self):
        os.makedirs(f"{self.d}/apps")
        with unittest.mock.patch.dict(A, {"CONF": self.d}):
            self.assertEqual(A["load_cfg"]("x")["args"], "")
            open(f"{self.d}/default.conf", "w").write("args=--a\n")
            self.assertEqual(A["load_cfg"]("x")["args"], "--a")
            open(f"{self.d}/apps/x.conf", "w").write("args=--b 'c d'\n")
            self.assertEqual(A["load_cfg"]("x")["args"], "--b 'c d'")
            open(f"{self.d}/apps/x.conf", "w").write("args=\n")
            self.assertEqual(A["load_cfg"]("x")["args"], "")

    def test_args_go_between_the_app_and_what_you_typed(self):
        os.makedirs(f"{self.d}/apps")
        open(f"{self.d}/apps/x.conf", "w").write("args=--profile 'my work'\n")
        out = io.StringIO()
        with unittest.mock.patch.dict(A, {"CONF": self.d, "LOG": f"{self.d}/l", "STATE": self.d, "bwrap_has": lambda o: True}), contextlib.redirect_stdout(out):
            A["cmd_preview"]("x")
        words = __import__("shlex").split(out.getvalue())
        self.assertEqual(words[words.index("/app/AppRun") + 1:][:2], ["--profile", "my work"])

    def test_share_mounts_give_each_app_its_own_subfolder(self):
        home = f"{self.d}/h"
        for n in ("Downloads", "Music", ".hidden"): os.makedirs(f"{home}/{n}")
        os.makedirs(f"{self.d}/homes/x/Downloads"); open(f"{self.d}/homes/x/Downloads/old.txt", "w").close()
        c = {"share_dirs": "download,music,videos,pictures", "xdg": []}
        with unittest.mock.patch.dict(A, {"HOME": home, "HOMES": f"{self.d}/homes"}), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(A["share_mounts"]("x", c, False), [(f"{home}/Downloads/ajail/x", f"{home}/Downloads"), (f"{home}/Music/ajail/x", f"{home}/Music")])
            self.assertFalse(os.path.exists(f"{home}/Downloads/ajail"))      # a preview makes nothing
            A["share_mounts"]("x", c, True)
            self.assertTrue(os.path.exists(f"{home}/Downloads/ajail/x/old.txt"))      # what the app's home held moves over
            self.assertEqual(oct(os.stat(f"{home}/Downloads/ajail/x").st_mode & 0o777), "0o700")
            self.assertEqual([m[1] for m in A["share_mounts"]("x", {**c, "xdg": ["download"]}, False)], [f"{home}/Music"])      # xdg= shares the whole folder instead

    def test_portal_file_args_swap_opened_files_for_their_portal_copy(self):
        home, rt = f"{self.d}/h", f"{self.d}/run"
        os.makedirs(f"{home}/docs"); os.makedirs(f"{home}/shared"); os.makedirs(f"{home}/.ssh")
        f, kept, key = (f"{home}/docs/report.html", f"{home}/shared/a.txt", f"{home}/.ssh/key")
        for p in (f, kept, key): open(p, "w").close()
        c = {"allow": [f"{home}/shared"], "allow_ro": [], "xdg": []}
        conv = lambda a, export=lambda paths, app: {p: "ab12" for p in paths}: A["portal_file_args"](a, "io.x.Run.x", c, export)
        doc = f"{rt}/doc/ab12/report.html"
        with unittest.mock.patch.dict(A, {"HOME": home, "runtime_dir": lambda: rt}):
            self.assertEqual(conv([f, "--flag"]), [doc, "--flag"])
            self.assertEqual(conv([f"file://{f}"]), [f"file://{doc}"])
            self.assertEqual(conv(["report.html", "/usr/bin/env", home, key, kept]), ["report.html", "/usr/bin/env", home, key, kept])      # shared folder, hidden folder, folder, relative: unchanged
            self.assertEqual(conv([f], lambda paths, app: {}), [f])      # no portal: the file is passed on as before

    def test_gtk_portal_is_auto_until_a_file_says_otherwise(self):
        os.makedirs(f"{self.d}/apps")
        with unittest.mock.patch.dict(A, {"CONF": self.d}):
            self.assertEqual(A["load_cfg"]("x")["gtk_portal"], "auto")
            open(f"{self.d}/default.conf", "w").write("gtk_portal=0\n")
            self.assertEqual(A["load_cfg"]("x")["gtk_portal"], "0")
            open(f"{self.d}/apps/x.conf", "w").write("gtk_portal=auto\n")      # an app can go back to automatic
            self.assertEqual(A["load_cfg"]("x")["gtk_portal"], "auto")
            open(f"{self.d}/apps/x.conf", "w").write("gtk_portal=1\n")
            self.assertEqual(A["load_cfg"]("x")["gtk_portal"], "1"); self.assertEqual(A["load_cfg"]("y")["gtk_portal"], "0")

    def test_gtk_portal_is_not_a_permission_an_import_has_to_flag(self):
        self.assertEqual(A["widening"]("gtk_portal=1\ngtk_portal=0\n"), [])

    def test_gui_accepts_exactly_what_ajail_accepts(self):
        G = load_gui()
        names = ["org.gnome.SessionManager", "org.example.App.*", "com.a.b.c.*", "org.*", "org.freedesktop.*", "a..b",
                 "org..x", "*", "a b", "-x.y", "a.b.", ".a.b", "org.example.*.x", "x", ""]
        for n in names:
            self.assertEqual(G["talk_ok"](n), A["talk_ok"](n), n)
            self.assertEqual(G["validate"]([n]), n if A["talk_ok"](n) else None, n)    # what the window really uses

class Gui(unittest.TestCase):
    def tree(self):
        return ast.parse(open(f"{HERE}/ajail-gui").read())

    def test_user_comes_from_the_account_database(self):
        src = open(f"{HERE}/ajail-gui").read()
        self.assertNotIn('os.environ.get("USER"', src)
        self.assertIn("pwd.getpwuid(os.getuid()).pw_name", src)

    def test_the_window_keeps_a_text_setting_it_has_no_row_for(self):
        ns = {"re": re, "os": os}
        for n in self.tree().body:
            if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "LISTS") \
               or (isinstance(n, ast.FunctionDef) and n.name == "write_cfg"):
                exec(compile(ast.Module([n], []), "gui", "exec"), ns)
        path = tempfile.mkdtemp() + "/x.conf"
        ns["path_for"] = lambda scope: path
        ns["write_cfg"]("x", {"s": {"net": 0, "gtk_portal": "auto"}, **{k: [] for k in ns["LISTS"]}})
        self.assertEqual(open(path).read(), "net=0\ngtk_portal=auto\n")
        d = A["parse_cfg"](path); self.assertEqual(d["s"], {"net": 0, "gtk_portal": "auto"})     # what the window wrote, ajail reads back

    def test_has_comment_matches_the_parser(self):
        ns = {"re": re}
        for n in self.tree().body:
            if isinstance(n, ast.FunctionDef) and n.name == "has_comment":
                exec(compile(ast.Module([n], []), "gui", "exec"), ns)
        f = ns["has_comment"]
        for v in ("/home/me/a #b", "#x", "a\t#b"): self.assertTrue(f(v), v)
        for v in ("/home/me/C#", "a#b"): self.assertFalse(f(v), v)
        for v in ("/home/me/a #b", "KEY=v #c"):            # whatever the GUI refuses, the parser would have cut
            self.assertNotEqual(A["COMMENT"].sub("", v), v)

class Docs(unittest.TestCase):
    def test_readme_documents_the_compatibility_defaults(self):
        r = open(f"{HERE}/README.md").read()
        self.assertIn("Defaults that favor compatibility", r)
        self.assertIn("`userns`", r); self.assertIn("`harden`", r)
        self.assertEqual(A["DEFAULTS"]["userns"], 0); self.assertEqual(A["DEFAULTS"]["harden"], 0)   # the README says so

class LogFile(unittest.TestCase):
    """The log holds what apps print: private to you, trimmed in place, and never left with half a line."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.keep = {k: A[k] for k in ("LOG", "STATE")}
        A["STATE"] = f"{self.d}/state"; A["LOG"] = f"{self.d}/state/ajail.log"
    def tearDown(self): A.update(self.keep)

    def test_log_and_its_folder_are_private(self):
        A["log"]("hello", "app")
        self.assertEqual(stat.S_IMODE(os.stat(A["LOG"]).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(A["STATE"]).st_mode), 0o700)
        self.assertIn("[app] hello\n", open(A["LOG"]).read())

    def test_an_old_world_readable_log_is_made_private(self):
        os.makedirs(A["STATE"]); open(A["LOG"], "w").write("old\n"); os.chmod(A["LOG"], 0o644)
        A["log"]("x")
        self.assertEqual(stat.S_IMODE(os.stat(A["LOG"]).st_mode), 0o600)
        self.assertTrue(open(A["LOG"]).read().startswith("old\n"))

    def test_big_log_is_trimmed_in_place(self):
        os.makedirs(A["STATE"])
        open(A["LOG"], "w").write("".join(f"[old] line {i} " + "x" * 90 + "\n" for i in range(12000)))
        ino = os.stat(A["LOG"]).st_ino
        A["log"]("fresh")
        lines = open(A["LOG"]).read().splitlines()
        self.assertEqual(len(lines), 501); self.assertTrue(lines[-1].endswith("fresh"))
        self.assertIn("line 11999 ", lines[-2]); self.assertEqual(os.stat(A["LOG"]).st_ino, ino)    # same file, so other writers keep going

    def test_concurrent_writers_never_leave_a_broken_line(self):
        os.makedirs(A["STATE"])
        open(A["LOG"], "w").write("".join(f"[pre] {i} " + "y" * 90 + "\n" for i in range(11000)))    # over the trim limit
        def writer(k):
            for i in range(60):
                A["log"](f"w{k} {i} " + "z" * 200, f"app{k}")
        def streamer():                                       # like the app output that launch() copies into the log
            with open(A["LOG"], "a") as lf:
                for i in range(60): A["log_write"](lf, f"[tee] {i} " + "t" * 300 + "\n")
        ts = [threading.Thread(target=writer, args=(k,)) for k in range(6)] + [threading.Thread(target=streamer)]
        for t in ts: t.start()
        for t in ts: t.join()
        text = open(A["LOG"]).read()
        self.assertTrue(text.endswith("\n"))
        for l in text.splitlines():
            self.assertRegex(l, r"^(\[pre\] \d+ y{90}|\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] \[app\d\] w\d \d+ z{200}|\[tee\] \d+ t{300})$")

if __name__ == "__main__":
    unittest.main(verbosity=2)
