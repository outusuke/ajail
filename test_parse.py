#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for ajail's settings parser: whitespace, comments and warnings about lines it can't use.
import ast, contextlib, io, os, re, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))

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

    def test_known_good_file_is_silent(self):
        d, err = self.parse("net=0\nprofile=strict\nflag=--x11\nxdg=download:ro\nportal=Secret\nportal=-Secret\ntalk=org.x.Y\n")
        self.assertEqual(err, ""); self.assertEqual(d["s"]["x11"], 1)

class Gui(unittest.TestCase):
    def tree(self):
        return ast.parse(open(f"{HERE}/ajail-gui").read())

    def test_user_comes_from_the_account_database(self):
        src = open(f"{HERE}/ajail-gui").read()
        self.assertNotIn('os.environ.get("USER"', src)
        self.assertIn("pwd.getpwuid(os.getuid()).pw_name", src)

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
        self.assertEqual(A["DEFAULTS"]["userns"], 1); self.assertEqual(A["DEFAULTS"]["harden"], 0)   # the README says so

if __name__ == "__main__":
    unittest.main(verbosity=2)
