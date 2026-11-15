#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""ajail export / import: one text file with every settings file, and back again.
ajail takes its home from the password database, so the tests run a copy of the script whose CONF points at a temp folder."""
import os, re, stat, subprocess, sys, tempfile, unittest

AJAIL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ajail")

class Export(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        self.conf = f"{self.t}/conf"
        src = open(AJAIL).read()
        src, n = re.subn(r'^CONF    = .*$', f'CONF    = "{self.conf}"', src, count=1, flags=re.M)
        self.assertEqual(n, 1)
        self.script = f"{self.t}/ajail.py"
        open(self.script, "w").write(src)
        os.makedirs(f"{self.conf}/apps")
        self.put("default.conf", "net=0\nallow=/media/x\n")
        self.put("apps/Foo.conf", "gpu=0\n# my note\nenv=A=1\n")
        self.put("apps/Empty.conf", "")

    def tearDown(self): self.tmp.cleanup()

    def put(self, rel, text): open(f"{self.conf}/{rel}", "w").write(text)
    def get(self, rel): return open(f"{self.conf}/{rel}").read()
    def run_(self, *args, stdin=None):
        return subprocess.run([sys.executable, self.script, *args], capture_output=True, text=True, input=stdin)

    def test_all_goes_to_stdout_with_markers(self):
        r = self.run_("export")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "# ajail settings export")
        for label in ("default.conf", "apps/Empty.conf", "apps/Foo.conf"):
            self.assertIn(f"# [{label}]", r.stdout)

    def test_one_app_and_default(self):
        r = self.run_("export", "Foo")
        self.assertIn("# [apps/Foo.conf]", r.stdout); self.assertNotIn("default.conf]\nnet", r.stdout)
        self.assertNotIn("# [apps/Empty.conf]", r.stdout)
        r = self.run_("export", "--default")
        self.assertIn("net=0", r.stdout); self.assertNotIn("gpu=0", r.stdout)

    def test_comment_that_looks_like_a_marker_is_defused(self):
        self.put("apps/Foo.conf", "gpu=0\n# [default.conf]\nenv=A=1\n")
        out = self.run_("export", "Foo").stdout
        self.assertNotIn("# [default.conf]", out.splitlines())      # Foo's own comment line did not become a section
        self.assertIn("## [default.conf]", out.splitlines())
        os.unlink(f"{self.conf}/apps/Foo.conf")
        self.assertEqual(self.run_("import", "-", stdin=out).returncode, 0)
        self.assertEqual(self.get("apps/Foo.conf"), "gpu=0\n## [default.conf]\nenv=A=1\n")

    def test_file_is_private_and_not_overwritten_without_force(self):
        f = f"{self.t}/out.txt"
        r = self.run_("export", "-o", f)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600)
        r = self.run_("export", "-o", f)
        self.assertNotEqual(r.returncode, 0); self.assertIn("--force", r.stderr)
        self.assertEqual(self.run_("export", "-o", f, "--force").returncode, 0)

    def test_errors(self):
        for args, msg in ((("export", "--bogus"), "unknown option"), (("export", "Nope"), "no settings file for Nope"),
                          (("export", "-o"), "needs a file"), (("export", "a", "b"), "usage"), (("export", "../x"), "not an app name")):
            r = self.run_(*args)
            self.assertNotEqual(r.returncode, 0, args); self.assertIn(msg, r.stderr, args)
        os.unlink(f"{self.conf}/default.conf"); os.unlink(f"{self.conf}/apps/Foo.conf"); os.unlink(f"{self.conf}/apps/Empty.conf")
        r = self.run_("export"); self.assertNotEqual(r.returncode, 0); self.assertIn("no settings to export", r.stderr)

    def test_round_trip(self):
        f = f"{self.t}/all.txt"
        self.run_("export", "-o", f)
        before = {k: self.get(k) for k in ("default.conf", "apps/Foo.conf")}
        for k in ("default.conf", "apps/Foo.conf", "apps/Empty.conf"): os.unlink(f"{self.conf}/{k}")
        os.rmdir(f"{self.conf}/apps")
        r = self.run_("import", f)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("Imported 3 settings files", r.stdout)
        for k, v in before.items(): self.assertEqual(self.get(k), v)
        self.assertTrue(os.path.exists(f"{self.conf}/apps/Empty.conf"))
        self.assertEqual(stat.S_IMODE(os.stat(f"{self.conf}/apps/Foo.conf").st_mode), 0o600)
        self.assertIn("3 already the same", self.run_("import", f).stdout)

    def test_import_refuses_to_replace_without_force_and_writes_nothing(self):
        f = f"{self.t}/all.txt"
        self.run_("export", "-o", f)
        self.put("default.conf", "net=1\n"); os.unlink(f"{self.conf}/apps/Foo.conf")
        r = self.run_("import", f)
        self.assertNotEqual(r.returncode, 0); self.assertIn("default.conf", r.stderr)
        self.assertEqual(self.get("default.conf"), "net=1\n")
        self.assertFalse(os.path.exists(f"{self.conf}/apps/Foo.conf"))          # not even the missing one
        self.assertEqual(self.run_("import", f, "--force").returncode, 0)
        self.assertEqual(self.get("default.conf"), "net=0\nallow=/media/x\n")
        self.assertTrue(os.path.exists(f"{self.conf}/apps/Foo.conf"))

    def test_import_rejects_other_text(self):
        for text in ("net=0\n", "", "# sbx settings export\n# [default.conf]\nnet=0\n", "# ajail settings export\n",
                     "# ajail settings export\nnet=0\n# [default.conf]\n",
                     "# ajail settings export\n# [default.conf]\n# [default.conf]\n",
                     "# ajail settings export\n# [apps/../evil.conf]\nnet=0\n", "# ajail settings export\n# [default.conf]\n\0"):
            r = self.run_("import", "-", stdin=text)
            self.assertNotEqual(r.returncode, 0, repr(text))
        self.assertFalse(os.path.exists(f"{self.t}/evil.conf"))
        self.assertNotEqual(self.run_("import", f"{self.t}/missing.txt").returncode, 0)
        self.assertIn("unknown option", self.run_("import", "--forse", "x").stderr)

    def test_import_from_stdin(self):
        text = self.run_("export", "Foo").stdout
        os.unlink(f"{self.conf}/apps/Foo.conf")
        self.assertEqual(self.run_("import", "-", stdin=text).returncode, 0)
        self.assertIn("gpu=0", self.get("apps/Foo.conf"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
