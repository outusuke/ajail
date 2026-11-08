#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
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

    def test_tray_rules_are_narrow(self):
        r = A["tray_rules"]()
        self.assertIn("--talk=org.kde.StatusNotifierWatcher", r)
        self.assertIn("--own=org.kde.StatusNotifierItem-2-1", r)
        self.assertNotIn("--own=org.kde.*", r)
        self.assertFalse(any(x.endswith("*") for x in r))                # xdg-dbus-proxy only takes ".*" suffixes
        self.assertEqual(len(r), len(set(r)))

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

    def test_app_can_opt_out_of_default_portal(self):
        self.put("default.conf", "portal=Location\nportal=Secret\n")
        self.put("apps/Foo.conf", "portal=-Location\n")
        self.assertEqual(A["load_cfg"]("Foo")["portal"], ["Secret"])
        self.assertEqual(A["load_cfg"]("Other")["portal"], ["Location", "Secret"])

    def test_optout_order_and_edge_cases(self):
        self.put("default.conf", "portal=Location\nportal=-Secret\n")
        self.put("apps/A.conf", "portal=-Location\nportal=Location\n")      # later line wins
        self.put("apps/B.conf", "portal=-Bogus\nportal=--Location\nportal=-Wallpaper\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(A["load_cfg"]("A")["portal"], ["Location"])
            self.assertEqual(A["load_cfg"]("B")["portal"], ["Location"])    # opting out of an off portal is harmless
        self.assertIn("-Bogus", err.getvalue()); self.assertIn("--Location", err.getvalue())

    def test_optout_removes_rules(self):
        self.put("default.conf", "portal=Location\n")
        self.put("apps/Foo.conf", "portal=-Location\n")
        self.assertNotIn("Location", calls(A["portal_rules"](A["load_cfg"]("Foo"))))
        self.assertIn("Location", calls(A["portal_rules"](A["load_cfg"]("Other"))))

    def test_dump_keeps_optout_for_gui(self):
        import json
        self.put("apps/Foo.conf", "portal=-Location\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            A["cmd_dump"]("Foo")
        self.assertEqual(json.loads(out.getvalue())["app"]["portal"], ["-Location"])

    def test_bwrap_env_tells_selftest_about_tray(self):
        c = A["load_cfg"]("Foo"); c["tray"] = 1
        a = A["bwrap_args"]("Foo", "/x", c, ["/app/AppRun"], bus="/run/user/1/ajail/1.bus")
        i = a.index("AJAIL_TRAY"); self.assertEqual(a[i + 1], "2-64")
        c["tray"] = 0
        a = A["bwrap_args"]("Foo", "/x", c, ["/app/AppRun"], bus="/run/user/1/ajail/1.bus")
        self.assertNotIn("AJAIL_TRAY", a)

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

    def test_selftest_covers_every_allowed_portal_and_tray(self):
        s = open(f"{HERE}/make-selftest").read()
        app = re.search(r"APPRUN = r'''(.*?)'''", s, re.S).group(1)
        names = set(A["PORTAL_BASIC"]) | {"Screenshot", "ScreenCast", "Camera", "Documents"}
        for p in names: self.assertIn(f'"{p}"', app, p)
        self.assertIn("StatusNotifierItem", app); self.assertIn("MediaPlayer2", app); self.assertIn("AJAIL_TRAY", app)

    def test_gui_compiles(self):
        compile(open(f"{HERE}/ajail-gui").read(), "ajail-gui", "exec")

    def test_readme_mentions_every_portal(self):
        r = open(f"{HERE}/README.md").read()
        for p in A["EXTRA_PORTALS"]: self.assertIn(f"`{p}`", r)

class InstallMove(unittest.TestCase):
    """Compressed installs move the AppImage into ajail's folder; 'restore' puts it back."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for k, sub in (("INSTALLED", "apps"), ("CONF", "conf"), ("STATE", "state"), ("HOMES", "homes"),
                       ("APPLICATIONS", "desk"), ("ICONS", "icons"), ("EXTRACT", "ext")):
            A[k] = f"{self.d}/{sub}"
        A["RUNNING"] = f"{self.d}/state/running"; A["SOURCES"] = f"{self.d}/state/sources"
        A["LOG"] = f"{self.d}/state/ajail.log"
        os.makedirs(f"{self.d}/src")
        self.app = f"{self.d}/src/Foo.AppImage"
        open(self.app, "wb").write(b"data")

    def test_can_move_plain_file(self):
        self.assertTrue(A["can_move"](self.app))

    def test_can_move_refuses_removable_and_readonly(self):
        self.assertFalse(A["can_move"]("/media/me/usb/Foo.AppImage"))
        self.assertFalse(A["can_move"](f"/run/media/{A['USER']}/x/Foo.AppImage"))
        os.chmod(f"{self.d}/src", 0o500)
        try:
            if os.geteuid() != 0: self.assertFalse(A["can_move"](self.app))
        finally:
            os.chmod(f"{self.d}/src", 0o700)

    def test_place_image_move_and_copy(self):
        dst = f"{self.d}/moved"
        self.assertTrue(A["place_image"](self.app, dst, True))
        self.assertFalse(os.path.exists(self.app)); self.assertEqual(open(dst, "rb").read(), b"data")
        open(self.app, "wb").write(b"data2")
        dst2 = f"{self.d}/copied"
        self.assertFalse(A["place_image"](self.app, dst2, False))
        self.assertTrue(os.path.exists(self.app)); self.assertEqual(open(dst2, "rb").read(), b"data2")

    def test_restore_puts_file_back_and_uninstalls(self):
        d = f"{A['INSTALLED']}/Foo"; os.makedirs(d)
        A["place_image"](self.app, f"{d}/image", True)
        open(f"{d}/origin", "w").write(self.app)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            A["cmd_restore"](["Foo"])
        self.assertTrue(os.path.isfile(self.app)); self.assertFalse(os.path.exists(d))
        self.assertIn("Put the AppImage back", out.getvalue())

    def test_restore_refuses_when_nothing_was_moved_or_target_exists(self):
        d = f"{A['INSTALLED']}/Foo"; os.makedirs(d); open(f"{d}/image", "wb").write(b"x")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): A["cmd_restore"](["Foo"])         # no origin file
            open(f"{d}/origin", "w").write(self.app)
            with self.assertRaises(SystemExit): A["cmd_restore"](["Foo"])         # original path is taken
        self.assertTrue(os.path.exists(f"{d}/image"))

    def test_uninstall_refuses_to_delete_a_moved_image(self):
        d = f"{A['INSTALLED']}/Foo"; os.makedirs(d)
        A["place_image"](self.app, f"{d}/image", True)
        open(f"{d}/origin", "w").write(self.app)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): A["cmd_uninstall"](["Foo"])
        self.assertTrue(os.path.isfile(f"{d}/image"))                          # nothing was deleted
        with contextlib.redirect_stdout(io.StringIO()):
            A["cmd_uninstall"](["Foo", "--discard-image"])                      # the explicit opt-out still works
        self.assertFalse(os.path.exists(d))

    def test_uninstall_without_move_is_unchanged(self):
        d = f"{A['INSTALLED']}/Foo"; os.makedirs(d); open(f"{d}/image", "wb").write(b"x")     # copied: no origin file
        with contextlib.redirect_stdout(io.StringIO()):
            A["cmd_uninstall"](["Foo"])
        self.assertFalse(os.path.exists(d))

    def test_gui_uninstall_restores_first(self):
        with open(f"{HERE}/ajail-gui") as f: s = f.read()
        self.assertIn('[SELF, "restore", scope]', s); self.assertIn('"--discard-image"', s)

    def moved_app(self):
        d = f"{A['INSTALLED']}/Foo"; os.makedirs(d)
        A["place_image"](self.app, f"{d}/image", True)
        open(f"{d}/origin", "w").write(self.app)
        return d

    def test_restore_keep_copies_back_and_stays_installed(self):
        d = self.moved_app()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            A["cmd_restore"](["Foo", "--keep"])
        self.assertEqual(open(self.app, "rb").read(), b"data")                 # the file is back
        self.assertTrue(os.path.isfile(f"{d}/image"))                          # the app still has its image
        self.assertFalse(os.path.exists(f"{d}/origin"))                        # nothing left to protect
        self.assertIn("stays installed", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            A["cmd_uninstall"](["Foo"])                                        # so uninstalling no longer refuses
        self.assertFalse(os.path.exists(d))

    def test_restore_to_another_folder(self):
        d = self.moved_app()
        other = f"{self.d}/other"; os.makedirs(other)
        with contextlib.redirect_stdout(io.StringIO()):
            A["cmd_restore"](["Foo", "--keep", "--to", other])
        self.assertEqual(open(f"{other}/Foo.AppImage", "rb").read(), b"data")  # same file name, chosen folder
        self.assertFalse(os.path.exists(self.app))                             # not at the old place
        self.assertTrue(os.path.isfile(f"{d}/image"))

    def test_restore_to_missing_folder_or_taken_name_refuses(self):
        d = self.moved_app()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): A["cmd_restore"](["Foo", "--keep", "--to", f"{self.d}/nope"])
            taken = f"{self.d}/taken"; os.makedirs(taken); open(f"{taken}/Foo.AppImage", "w").close()
            with self.assertRaises(SystemExit): A["cmd_restore"](["Foo", "--to", taken])
            with self.assertRaises(SystemExit): A["cmd_restore"](["Foo", "--to"])        # missing value
        self.assertTrue(os.path.exists(f"{d}/origin")); self.assertTrue(os.path.isfile(f"{d}/image"))

    def test_gui_restore_button_asks_for_a_folder(self):
        with open(f"{HERE}/ajail-gui") as f: s = f.read()
        self.assertIn('"restore", scope, "--keep", "--to", folder', s)
        self.assertIn("Choose where to put the AppImage", s); self.assertIn("Get the AppImage back", s)

class MountDefault(unittest.TestCase):
    def test_gui_has_global_switch_and_fallback(self):
        with open(f"{HERE}/ajail-gui") as f: s = f.read()
        self.assertIn("self.mount_row", s); self.assertIn('ui.get("mount")', s)
        self.assertIn('title="Installing"', s)                # the storage options live in settings, not the install dialog

    def test_mount_only_applies_to_install(self):
        with open(f"{HERE}/ajail") as f: s = f.read()
        self.assertNotIn("def mount_pref", s)
        self.assertIn('"--mount" in flags and "--once" not in flags', s)      # run-file --once ignores --mount

class GuiInstallFlow(unittest.TestCase):
    def gui(self):
        with open(f"{HERE}/ajail-gui") as f: return f.read()

    def offer_install_src(self):
        s = self.gui()
        return s[s.index("    def offer_install"):s.index("    def install_file")]

    def test_install_dialog_is_only_the_decision(self):
        d = self.offer_install_src()
        for gone in ("Use less disk space", "Original file", "CheckButton(active=can_mount", "original_choices"):
            self.assertNotIn(gone, d)
        for kept in ('"Run once"', '"Install"', "Don't ask again for"):
            self.assertIn(kept, d)

    def test_run_once_never_mounts(self):
        s = self.gui()
        self.assertIn('[SELF, "run-file", "--once", path]', s)
        self.assertIn('[SELF, "run-file", "--once", src]', s)
        self.assertNotIn('"run-file", "--once"] + (["--mount"]', s)

    def test_settings_dropdown_and_ask_after_install(self):
        s = self.gui()
        self.assertNotIn("get_install_opt", s); self.assertNotIn('"Undo"', s)
        for t in ('title="After installing, the AppImage file"', "install_original", '"--move"',
                  "Move into app folder", "def ask_trash", "Move the AppImage to the Trash?"):
            self.assertIn(t, s)

    def test_move_choice_only_with_less_disk_space(self):
        ns = {}
        s = self.gui()
        for fn in ("original_choices", "original_hint", "get_install_original"):
            i = s.index(f"def {fn}")
            j = s.index("\n\n", i)
            exec(s[i:j], ns)
        ns["ORIGINAL"] = {"ask": "", "trash": "", "keep": "", "move": ""}
        self.assertEqual(ns["original_choices"](False), ["ask", "trash", "keep"])
        self.assertEqual(ns["original_choices"](True), ["ask", "trash", "keep", "move"])

    def test_original_defaults_to_ask(self):
        ns = {}
        s = self.gui()
        i = s.index("def get_install_original"); exec(s[i:s.index("\n\n", i)], ns)
        ns["ORIGINAL"] = {"ask": "", "trash": "", "keep": "", "move": ""}
        ns["ui_read"] = lambda: {}
        self.assertEqual(ns["get_install_original"](), "ask")
        ns["ui_read"] = lambda: {"install_original": "keep"}
        self.assertEqual(ns["get_install_original"](), "keep")
        ns["ui_read"] = lambda: {"install_original": "bogus"}
        self.assertEqual(ns["get_install_original"](), "ask")

    def test_cli_move_is_opt_in(self):
        with open(f"{HERE}/ajail") as f: s = f.read()
        self.assertIn("movable = move and can_move", s)       # never moves unless --move was given

class ButtonLayout(unittest.TestCase):
    """The host's window button layout reaches the app as a read-only GSettings key file."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.patch("host_cursor", lambda: (None, None))     # keep the host's real cursor out of these tests

    def patch(self, name, value):
        old = A[name]; A[name] = value
        self.addCleanup(A.__setitem__, name, old)

    def test_keyfile_holds_only_the_layout(self):
        self.patch("host_button_layout", lambda: "menu:minimize,maximize,close")
        p = A["make_gsettings_keyfile"](self.d)
        self.assertEqual(open(p).read(),
                         "[org/gnome/desktop/wm/preferences]\nbutton-layout='menu:minimize,maximize,close'\n")

    def test_no_host_value_means_no_file(self):
        self.patch("host_button_layout", lambda: None)
        self.assertIsNone(A["make_gsettings_keyfile"](self.d))
        self.assertFalse(os.path.exists(f"{self.d}/gsettings-keyfile"))

    def test_layout_is_validated(self):
        for bad in ("a'b", "x\ny", "close;rm", "A" * 5):
            self.assertIsNone(A["LAYOUT_RE"].fullmatch(bad), bad)
        self.assertTrue(A["LAYOUT_RE"].fullmatch("appmenu:minimize,maximize,close"))

    def test_bwrap_binds_keyfile_only_with_theme(self):
        c = A["load_cfg"]("Foo")
        a = A["bwrap_args"]("Foo", "/x", c, ["/app/AppRun"], gset="/run/user/1/ajail/1.d/gsettings-keyfile")
        i = a.index("GSETTINGS_BACKEND"); self.assertEqual(a[i + 1], "keyfile")
        j = a.index(f"{A['HOME']}/.config/glib-2.0/settings/keyfile"); self.assertEqual(a[j - 2], "--ro-bind")
        c["theme"] = 0
        a = A["bwrap_args"]("Foo", "/x", c, ["/app/AppRun"], gset="/run/user/1/ajail/1.d/gsettings-keyfile")
        self.assertNotIn("GSETTINGS_BACKEND", a)
        a = A["bwrap_args"]("Foo", "/x", A["load_cfg"]("Foo"), ["/app/AppRun"])
        self.assertNotIn("GSETTINGS_BACKEND", a)

class Cursor(unittest.TestCase):
    """The host's cursor theme and size (org.gnome.desktop.interface) reach the app in the same key file."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.patch("host_button_layout", lambda: None)

    def patch(self, name, value):
        old = A[name]; A[name] = value
        self.addCleanup(A.__setitem__, name, old)

    def gs(self, theme, size):
        vals = {"cursor-theme": theme, "cursor-size": size}
        self.patch("gsettings_get", lambda schema, key: vals.get(key) if schema == "org.gnome.desktop.interface" else None)

    def test_reads_theme_and_size(self):
        self.gs("'Hackneyed'", "32")
        self.assertEqual(A["host_cursor"](), ("Hackneyed", 32))

    def test_size_zero_is_unset(self):
        self.gs("'Hackneyed'", "0")
        self.assertEqual(A["host_cursor"](), ("Hackneyed", None))
        for z in ("00", "000"):
            self.gs("'Hackneyed'", z)
            self.assertIsNone(A["host_cursor"]()[1], z)

    def test_theme_is_validated(self):
        for bad in ("'../evil'", "'a/b'", "'.hidden'", "''", "'a'b'", "\"it's\"", "Hackneyed", "'x\\ny'", "'a\nb'",
                    "'" + "a" * 65 + "'", "'a;b'", "'a=b'", "'[x]'", "'ü'", None):
            self.gs(bad, "24")
            self.assertIsNone(A["host_cursor"]()[0], bad)
        for good in ("Hackneyed", "Breeze_Snow", "Bibata-Modern-Classic", "Adwaita", "My Theme 2.0", "x+y"):
            self.gs(f"'{good}'", "24")
            self.assertEqual(A["host_cursor"]()[0], good)

    def test_size_is_validated(self):
        for bad in ("-1", "257", "1000", "1e2", "24.0", "uint32 24", "'24'", "", "0x18", " ", None):
            self.gs("'Adwaita'", bad)
            self.assertIsNone(A["host_cursor"]()[1], bad)
        for good, n in (("1", 1), ("24", 24), ("256", 256)):
            self.gs("'Adwaita'", good)
            self.assertEqual(A["host_cursor"]()[1], n)

    def test_keyfile_has_theme_and_no_size_when_size_is_zero(self):
        self.gs("'Hackneyed'", "0")
        p = A["make_gsettings_keyfile"](self.d)
        self.assertEqual(open(p).read(), "[org/gnome/desktop/interface]\ncursor-theme='Hackneyed'\n")

    def test_keyfile_has_theme_and_size(self):
        self.gs("'Hackneyed'", "36")
        p = A["make_gsettings_keyfile"](self.d)
        self.assertEqual(open(p).read(), "[org/gnome/desktop/interface]\ncursor-theme='Hackneyed'\ncursor-size=36\n")

    def test_keyfile_size_only(self):
        self.gs(None, "36")
        p = A["make_gsettings_keyfile"](self.d)
        self.assertEqual(open(p).read(), "[org/gnome/desktop/interface]\ncursor-size=36\n")

    def test_layout_and_cursor_share_one_file(self):
        self.patch("host_button_layout", lambda: ":minimize,maximize,close")
        self.gs("'Hackneyed'", "24")
        p = A["make_gsettings_keyfile"](self.d)
        self.assertEqual(open(p).read(),
                         "[org/gnome/desktop/wm/preferences]\nbutton-layout=':minimize,maximize,close'\n\n"
                         "[org/gnome/desktop/interface]\ncursor-theme='Hackneyed'\ncursor-size=24\n")

    def test_nothing_found_means_no_file(self):
        self.gs(None, None)
        self.assertIsNone(A["make_gsettings_keyfile"](self.d))
        self.assertFalse(os.path.exists(f"{self.d}/gsettings-keyfile"))
        self.gs("'Hackneyed'", "0"); self.gs("'../x'", "0")      # size 0 + rejected theme: still nothing
        self.assertIsNone(A["make_gsettings_keyfile"](self.d))

    def test_keyfile_only_asks_gnome_interface_schema(self):
        asked = []
        self.patch("gsettings_get", lambda schema, key: asked.append((schema, key)))
        A["host_cursor"]()
        self.assertEqual(sorted(asked), [("org.gnome.desktop.interface", "cursor-size"),
                                         ("org.gnome.desktop.interface", "cursor-theme")])

    def test_env_cursor_vars_are_still_passed_through(self):
        os.environ["XCURSOR_THEME"] = "Foo"
        self.addCleanup(os.environ.pop, "XCURSOR_THEME", None)
        a = A["bwrap_args"]("Foo", "/x", A["load_cfg"]("Foo"), ["/app/AppRun"])
        i = a.index("XCURSOR_THEME"); self.assertEqual(a[i + 1], "Foo")

    def test_preview_decides_by_what_would_be_written(self):
        with open(f"{HERE}/ajail") as f: s = f.read()
        self.assertIn('cfg["theme"] and gsettings_keyfile_text() is not None', s)

class IniLayout(unittest.TestCase):
    """The button layout comes from gsettings first, then from your own GTK settings.ini."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch("HOME", self.tmp)
        self.patch("gsettings_get", lambda schema, key: "'appmenu:close'" if key == "button-layout" else None)

    def patch(self, name, value):
        old = A[name]; A[name] = value
        self.addCleanup(A.__setitem__, name, old)

    def ini(self, text, ver="gtk-3.0"):
        os.makedirs(f"{self.tmp}/.config/{ver}", exist_ok=True)
        with open(f"{self.tmp}/.config/{ver}/settings.ini", "w") as f: f.write(text)

    def test_gsettings_comes_first(self):
        self.ini("[Settings]\ngtk-decoration-layout=close:\n")
        self.assertEqual(A["host_button_layout"](), "appmenu:close")

    def test_settings_ini_is_the_fallback(self):
        self.patch("gsettings_get", lambda schema, key: None)
        self.ini("[Settings]\ngtk-decoration-layout=close:\n")
        self.assertEqual(A["host_button_layout"](), "close:")

    def test_nothing_found_is_none(self):
        self.patch("gsettings_get", lambda schema, key: None)
        self.assertIsNone(A["host_button_layout"]())

    def test_invalid_ini_value_is_skipped(self):
        self.patch("gsettings_get", lambda schema, key: None)
        self.ini("[Settings]\ngtk-decoration-layout=close;rm\n")
        self.assertIsNone(A["host_button_layout"]())

    def test_gtk4_ini_comes_before_gtk3(self):
        self.ini("[Settings]\ngtk-decoration-layout=:minimize\n", "gtk-3.0")
        self.ini("[Settings]\ngtk-decoration-layout=:close\n", "gtk-4.0")
        self.assertEqual(A["gtk_ini_layout"](), ":close")

if __name__ == "__main__":
    unittest.main(verbosity=2)
