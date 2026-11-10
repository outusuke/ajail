#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# ajail is generated from src/*.py by build.py. These tests fail if the committed ajail is stale (someone edited src/
# or ajail by hand and did not run ./build.py) or if the security core starts depending on a higher layer.
import importlib.util, os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ajail_build", f"{HERE}/build.py")
B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)

class Build(unittest.TestCase):
    def test_layering_holds(self):
        self.assertEqual(B.check_layers(), [])

    def test_ajail_is_up_to_date(self):
        self.assertEqual(open(f"{HERE}/ajail").read(), B.render(),
                         "ajail does not match src/. Edit src/*.py and run ./build.py (do not edit ajail directly)")

    def test_ends_with_main_call(self):                  # the other tests load ajail by cutting off this last line
        self.assertTrue(open(f"{HERE}/ajail").read().rstrip().endswith("\nmain()"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
