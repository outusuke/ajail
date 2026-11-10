#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# ajail and ajail-gui each carry a copy of the permission tables. If they drift, the GUI shows switches that ajail
# ignores (or the other way round). This fails as soon as one copy is edited without the other.
import ast, os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = ("DEFAULTS", "NUM", "LISTS", "EXTRA_PORTALS", "OUTSIDE", "SELF")      # must be identical in both files

def assigned(path):
    out = {}
    for n in ast.parse(open(f"{HERE}/{path}").read()).body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
            out[n.targets[0].id] = ast.unparse(n.value)
    return out

class Sync(unittest.TestCase):
    def test_shared_tables_are_identical(self):
        a, g = assigned("ajail"), assigned("ajail-gui")
        for name in SHARED:
            self.assertIn(name, a, f"{name} missing from ajail")
            self.assertIn(name, g, f"{name} missing from ajail-gui")
            self.assertEqual(a[name], g[name], f"{name} differs between ajail and ajail-gui: edit both")

if __name__ == "__main__":
    unittest.main(verbosity=2)
