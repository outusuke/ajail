#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line option checks: an unknown --option must be an error, never silently ignored.
No sandbox needed; every case fails before anything is read or written."""
import os, subprocess, sys, unittest

AJAIL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ajail")

def run(*args):
    return subprocess.run([sys.executable, AJAIL, *args], capture_output=True, text=True)

class UnknownOptions(unittest.TestCase):
    def check(self, *args):
        r = run(*args)
        self.assertNotEqual(r.returncode, 0, args)
        self.assertIn("unknown option", r.stderr, args)

    def test_install_typo(self):
        self.check("install", "--trsh", "x.AppImage")

    def test_install_unknown_flag_after_file(self):
        self.check("install", "x.AppImage", "--mounnt")

    def test_uninstall_typo(self):
        self.check("uninstall", "--purg", "someapp")

    def test_uninstall_unknown_flag_after_name(self):
        self.check("uninstall", "someapp", "--discard")

    def test_message_names_the_option(self):
        self.assertIn("--trsh", run("install", "--trsh", "x").stderr)

class StillAccepted(unittest.TestCase):
    def test_known_options_reach_the_next_check(self):
        # --move without --mount is rejected by the next rule, which proves --move itself was accepted
        r = run("install", "--move", "x.AppImage")
        self.assertIn("--move only works together with --mount", r.stderr)

    def test_missing_file_gives_usage(self):
        self.assertIn("usage: ajail install", run("install", "--trash").stderr)

class AppList(unittest.TestCase):
    def test_per_app_helper_files_are_not_apps(self):
        import tempfile, unittest.mock
        src = open(AJAIL).read().rstrip()
        ns = {"__name__": "under_test"}
        exec(compile(src[:-len("main()")], "ajail", "exec"), ns)
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(f"{t}/conf/apps"); os.makedirs(f"{t}/homes"); os.makedirs(f"{t}/installed")
            for f in ("Foo.conf", "Foo.machine-id", "Bar.machine-id", "notes.txt"): open(f"{t}/conf/apps/{f}", "w").close()
            os.makedirs(f"{t}/homes/Baz")
            with unittest.mock.patch.dict(ns, {"CONF": f"{t}/conf", "HOMES": f"{t}/homes", "INSTALLED": f"{t}/installed"}):
                self.assertEqual(ns["app_list"](), ["Baz", "Foo"])

if __name__ == "__main__":
    unittest.main()
