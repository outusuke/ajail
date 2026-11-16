#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for the AppArmor hint that "ajail check" shows when bubblewrap cannot make a user namespace.
import os, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load(name):
    src = open(f"{HERE}/{name}").read().rstrip()
    assert src.endswith("\nmain()")
    ns = {"__name__": "under_test"}
    exec(compile(src[:-len("main()")], name, "exec"), ns)
    return ns

A = load("ajail")

class AppArmorHint(unittest.TestCase):
    def hint(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/restrict"
            if text is not None: open(p, "w").write(text)
            return A["apparmor_userns_hint"](p)

    def test_restriction_on_gives_the_fix(self):
        h = self.hint("1\n")
        self.assertIn("kernel.apparmor_restrict_unprivileged_userns=0", h)
        self.assertIn("Ubuntu 24.04", h)

    def test_restriction_off_gives_nothing(self):
        self.assertIsNone(self.hint("0\n"))

    def test_missing_file_gives_nothing(self):                  # kernels without AppArmor or without the sysctl
        self.assertIsNone(self.hint(None))

if __name__ == "__main__":
    unittest.main()
