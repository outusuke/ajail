#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The environment-variable rows of ajail-gui: what a typed entry becomes and how it replaces another. Pure logic, no display."""
import ast, os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))

def load():
    tree = ast.parse(open(f"{HERE}/ajail-gui").read())
    want_assign, want_def = {"ENV_NAME_RE"}, {"has_comment", "parse_env_entry", "env_replace"}
    body = [n for n in tree.body
            if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in want_assign)
            or (isinstance(n, ast.FunctionDef) and n.name in want_def)]
    ns = {"re": re}
    exec(compile(ast.Module(body, []), "ajail-gui", "exec"), ns)
    return ns

G = load()
parse, replace = G["parse_env_entry"], G["env_replace"]

class Entry(unittest.TestCase):
    def test_name_equals_value_is_stored_trimmed(self):
        self.assertEqual(parse("SDL_VIDEODRIVER=x11"), ("SDL_VIDEODRIVER=x11", None))
        self.assertEqual(parse("  A = b c  "), ("A=b c", None))
        self.assertEqual(parse("EMPTY="), ("EMPTY=", None))                      # an empty value is a value
        self.assertEqual(parse("A=b=c"), ("A=b=c", None))                        # only the first = splits

    def test_a_bare_name_is_refused_because_ajail_has_no_pass_through(self):
        item, bad = parse("DISPLAY")
        self.assertIsNone(item); self.assertIn("NAME=value", bad)
        self.assertIsNone(parse("")[0])

    def test_bad_names_and_comment_values_are_refused(self):
        for t in ("1A=x", "A-B=x", "=x", "A B=x", "ä=x"):
            item, bad = parse(t); self.assertIsNone(item, t); self.assertTrue(bad, t)
        item, bad = parse("A=x #y"); self.assertIsNone(item); self.assertIn("comment", bad)
        self.assertEqual(parse("A=x#y")[0], "A=x#y")                              # no space before #: not a comment to ajail

    def test_the_name_rule_is_the_one_ajail_applies_when_it_starts_the_app(self):
        self.assertEqual(G["ENV_NAME_RE"].pattern, "[A-Za-z_][A-Za-z0-9_]*")
        with open(f"{HERE}/ajail") as f: self.assertIn('re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)', f.read())

class Replace(unittest.TestCase):
    def test_a_new_variable_goes_last(self):
        self.assertEqual(replace(["A=1"], "B=2"), ["A=1", "B=2"])
        self.assertEqual(replace([], "B=2"), ["B=2"])

    def test_a_row_is_changed_where_it_stands(self):
        self.assertEqual(replace(["A=1", "B=2", "C=3"], "B=9", old="B=2"), ["A=1", "B=9", "C=3"])
        self.assertEqual(replace(["A=1", "B=2", "C=3"], "X=7", old="B=2"), ["A=1", "X=7", "C=3"])      # renamed

    def test_one_value_per_name(self):
        self.assertEqual(replace(["A=1", "B=2"], "A=5"), ["A=5", "B=2"])                  # adding a name that exists changes it
        self.assertEqual(replace(["A=1", "B=2", "A=3"], "A=5"), ["A=5", "B=2"])           # a duplicate is dropped
        self.assertEqual(replace(["A=1", "B=2"], "A=9", old="B=2"), ["A=9"])              # renamed onto another: one left, here

    def test_the_input_list_is_not_changed(self):
        l = ["A=1"]; replace(l, "A=2"); self.assertEqual(l, ["A=1"])

if __name__ == "__main__":
    unittest.main()
