#!/usr/bin/python3
# Unit tests for the extra-portals feature. Run from the folder holding ajail, ajail-gui and make-selftest.
import ast, os, re, sys, tempfile, unittest, io, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))

def load_ajail():
    src = open(f"{HERE}/ajail").read()
    assert src.rstrip().endswith("\nmain()")
    src = src.rstrip()[:-len("main()")]          # don't run the CLI on import
    ns = {"__name__": "ajail_under_test"}
    exec(compile(src, "ajail", "exec"), ns)
    return ns

A = load_ajail()
ALL = A["EXTRA_PORTALS"]

def cfg(**kw):
    c = {**A["DEFAULTS"], **A["NUM"], "allow": [], "allow_ro": [], "env": [], "talk": [], "portal": [], "xdg": []}
    c.update(kw)
    return c

def calls(rules):
    """{interface: True} for every --call=org.freedesktop.portal.Desktop=org.freedesktop.portal.X.* rule."""
    out = set()
    for r in rules:
        m = re.fullmatch(r"--call=org\.freedesktop\.portal\.Desktop=org\.freedesktop\.portal\.(\w+)\.\*", r)
        if m: out.add(m.group(1))
    return out

class Rules(unittest.TestCase):
    def test_default_has_no_extra_portal(self):
        got = calls(A["portal_rules"](cfg()))
        for p in ALL: self.assertNotIn(p, got)

    def test_each_portal_enabled_alone(self):
        base = calls(A["portal_rules"](cfg()))
        for p in ALL:
            got = calls(A["portal_rules"](cfg(portal=[p])))
            self.assertEqual(got - base, {p}, p)
            self.assertTrue(base <= got)

    def test_rule_text_exact(self):
        r = A["portal_rules"](cfg(portal=["Location"]))
        self.assertIn("--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.Location.*", r)

    def test_unknown_and_duplicates_ignored_in_rules(self):
        r = A["portal_rules"](cfg(portal=["Location", "Location", "Documents", "Bogus*", "location"]))
        self.assertEqual(calls(r) & (set(ALL) | {"Documents", "Bogus", "location"}), {"Location"})
        self.assertEqual(sum("Location" in x for x in r), 1)
        self.assertFalse(any("Documents" in x or "Bogus" in x for x in r))

    def test_session_and_request_rules_still_present(self):
        r = A["portal_rules"](cfg(portal=["RemoteDesktop"]))
        self.assertTrue(any("portal.Session.*" in x for x in r))
        self.assertTrue(any("portal.Request.*" in x for x in r))

    def test_independent_of_other_switches(self):
        base = calls(A["portal_rules"](cfg(camera=0, portal_screenshot=0, portal_screencast=0)))
        self.assertTrue({"Camera", "Screenshot", "ScreenCast"}.isdisjoint(base))
        self.assertIn("Secret", calls(A["portal_rules"](cfg(portal=["Secret"], camera=0))))

    def test_extra_portals_helper(self):
        self.assertEqual(A["extra_portals"](cfg(portal=["Secret", "Nope", "Secret", "Wallpaper"])), ["Secret", "Wallpaper"])

class Config(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        A["CONF"] = self.d
        os.makedirs(f"{self.d}/apps")

    def put(self, rel, text):
        open(f"{self.d}/{rel}", "w").write(text)

    def test_parse_valid_and_invalid(self):
        self.put("default.conf", "portal=Location\nportal=Bogus\nportal=location\nportal=\nportal=Secret\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = A["parse_cfg"](f"{self.d}/default.conf")
        self.assertEqual(d["portal"], ["Location", "Secret"])
        self.assertIn("Bogus", err.getvalue()); self.assertIn("'location'", err.getvalue())

    def test_empty_cfg_has_portal(self):
        self.assertEqual(A["empty_cfg"]()["portal"], [])

    def test_load_merges_default_and_app(self):
        self.put("default.conf", "portal=Location\n")
        self.put("apps/Foo.conf", "portal=Secret\nportal=Location\n")
        self.assertEqual(A["load_cfg"]("Foo")["portal"], ["Location", "Secret"])
        self.assertEqual(A["load_cfg"]("Other")["portal"], ["Location"])

    def test_dump_json_roundtrip_keeps_portal(self):
        import json
        self.put("apps/Foo.conf", "portal=Wallpaper\nnet=0\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            A["cmd_dump"]("Foo")
        j = json.loads(out.getvalue())
        self.assertEqual(j["app"]["portal"], ["Wallpaper"]); self.assertEqual(j["app"]["s"], {"net": 0})
        self.assertEqual(j["global"]["portal"], [])

    def test_bwrap_env_lists_enabled_portals(self):
        c = A["load_cfg"]("Foo"); c["portal"] = ["Location", "Secret"]
        a = A["bwrap_args"]("Foo", "/x", c, ["/app/AppRun"], bus="/run/user/1/ajail/1.bus")
        i = a.index("AJAIL_PORTALS")
        self.assertEqual(a[i - 1], "--setenv"); self.assertEqual(a[i + 1], "Location,Secret")
        c["portals"] = 0
        a = A["bwrap_args"]("Foo", "/x", c, ["/app/AppRun"], bus="/run/user/1/ajail/1.bus")
        self.assertNotIn("AJAIL_PORTALS", a)
        a = A["bwrap_args"]("Foo", "/x", A["load_cfg"]("Foo"), ["/app/AppRun"], bus=None)
        self.assertNotIn("AJAIL_PORTALS", a)

class Sync(unittest.TestCase):
    def gui_const(self, name):
        tree = ast.parse(open(f"{HERE}/ajail-gui").read())
        for n in tree.body:
            if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets):
                return ast.literal_eval(n.value)
        self.fail(f"{name} not found in ajail-gui")

    def test_lists_defaults_portals_in_sync(self):
        self.assertEqual(tuple(self.gui_const("LISTS")), A["LISTS"])
        self.assertEqual(tuple(self.gui_const("EXTRA_PORTALS")), A["EXTRA_PORTALS"])
        self.assertEqual(self.gui_const("DEFAULTS"), A["DEFAULTS"])
        self.assertEqual(self.gui_const("NUM"), A["NUM"])

    def test_portal_in_lists(self):
        self.assertIn("portal", A["LISTS"])

    def test_gui_has_one_switch_per_portal(self):
        info = self.gui_const("PORTAL_INFO")
        self.assertEqual(set(info), set(A["EXTRA_PORTALS"]))
        for name, (title, sub) in info.items():
            self.assertTrue(title and sub, name)
        with open(f"{HERE}/ajail-gui") as f: s = f.read()
        self.assertIn('title="Extra portals"', s)
        self.assertIn('for pname in EXTRA_PORTALS:', s)
        self.assertNotIn('"portal", ["Portal name', s)      # no free-text entry any more

    def test_selftest_covers_all_portals_and_compiles(self):
        s = open(f"{HERE}/make-selftest").read()
        compile(s, "make-selftest", "exec")
        m = re.search(r"APPRUN = r'''(.*?)'''", s, re.S)
        compile(m.group(1), "AppRun", "exec")
        for p in A["EXTRA_PORTALS"]:
            self.assertIn(f'("{p}", "', m.group(1), p)

    def test_gui_compiles(self):
        compile(open(f"{HERE}/ajail-gui").read(), "ajail-gui", "exec")

    def test_readme_mentions_every_portal(self):
        r = open(f"{HERE}/README.md").read()
        for p in A["EXTRA_PORTALS"]: self.assertIn(f"`{p}`", r)

if __name__ == "__main__":
    unittest.main(verbosity=2)
