#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Screen reader, GNOME Shell and KDE Plasma integration (a11y, gnome_shell, kde) and the log page / desktop rows of ajail-gui."""
import ast, os, re, tempfile, time, types, unittest, unittest.mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_ajail():
    src = open(f"{HERE}/ajail").read().rstrip()
    ns = {"__name__": "ajail_under_test"}
    exec(compile(src[:-len("main()")], "ajail", "exec"), ns)
    return ns

def load_gui():
    tree = ast.parse(open(f"{HERE}/ajail-gui").read())
    names = {"DEFAULTS", "SECTIONS", "DESKTOP_ONLY", "DESKTOP_NAMES", "DESKTOP_FOLD", "START_RE", "DIVIDER", "LOG_LINE_RE", "LOG_KINDS",
             "desktop_hidden", "folded_desktops", "last_start", "latest_run_lines", "last_run", "switch_title", "start_changes",
             "short_ts", "log_rows", "log_line_parts"}
    body = [n for n in tree.body
            if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in names)
            or (isinstance(n, ast.FunctionDef) and n.name in names)]
    ns = {"re": re, "os": os, "time": time}
    exec(compile(ast.Module(body, []), "ajail-gui", "exec"), ns)
    return ns

A, G = load_ajail(), load_gui()

def cfg(**kw):
    c = {**A["DEFAULTS"], **A["NUM"], "allow": [], "allow_ro": [], "env": [], "talk": [], "portal": [], "xdg": []}
    c.update(kw)
    return c

class BusRules(unittest.TestCase):
    def test_off_by_default_and_no_bus_needed(self):
        c = cfg(portals=0)
        self.assertEqual((A["gnome_shell_rules"](c), A["kde_rules"](c)), ([], []))
        self.assertFalse(A["want_bus"](c))

    def test_each_switch_alone_wants_the_bus(self):
        for k in ("gnome_shell", "kde"): self.assertTrue(A["want_bus"](cfg(portals=0, **{k: 1})), k)
        self.assertFalse(A["want_bus"](cfg(portals=0, a11y=1)))      # a11y has its own bus

    def test_gnome_shell_is_narrow(self):
        r = A["gnome_shell_rules"](cfg(gnome_shell=1))
        self.assertIn("--own=org.gnome.BrowserConnector", r)
        self.assertIn("--call=org.gnome.Shell=org.gnome.Shell.Extensions.ListExtensions@/org/gnome/Shell", r)
        self.assertFalse(any("Eval" in x or "UninstallExtension" in x or x.startswith("--talk") for x in r))
        self.assertFalse(any("Properties.Set" in x for x in r))

    def test_gnome_shell_manage_adds_three_calls(self):
        a = A["gnome_shell_rules"](cfg(gnome_shell=1)); b = A["gnome_shell_rules"](cfg(gnome_shell=1, gnome_shell_manage=1))
        self.assertEqual(len(b) - len(a), 3)
        self.assertIn("--call=org.gnome.Shell=org.gnome.Shell.Extensions.UninstallExtension@/org/gnome/Shell", b)
        self.assertEqual(A["gnome_shell_rules"](cfg(gnome_shell_manage=1)), [])      # manage alone does nothing

    def test_kde_is_narrow(self):
        r = A["kde_rules"](cfg(kde=1))
        self.assertIn("--own=org.kde.plasma.browser_integration", r)
        self.assertIn("--own=org.kde.plasma.browser_integration-2", r)
        self.assertIn("--call=org.kde.JobViewServer=org.kde.JobViewServerV2.requestView@/JobViewServer", r)
        self.assertIn("--call=org.freedesktop.PowerManagement.Inhibit=org.freedesktop.PowerManagement.Inhibit.Inhibit@/org/freedesktop/PowerManagement/Inhibit", r)
        self.assertFalse(any(x.startswith("--talk") or "kdeconnect" in x.lower() for x in r))
        self.assertEqual(len(r), len(set(r)))

    def test_start_bus_passes_the_rules_to_the_proxy(self):
        cmds = []
        class P:
            def __init__(s, cmd, **k): cmds.append(cmd); s.pid = 1
            def terminate(s): pass
            def wait(s, t=None): pass
        fake = {"shutil": types.SimpleNamespace(which=lambda n: "/x/" + n), "warn": lambda *a: None,
                "subprocess": types.SimpleNamespace(Popen=P, DEVNULL=-3, SubprocessError=Exception, TimeoutExpired=Exception),
                "select": types.SimpleNamespace(select=lambda *a: ([a[0][0]], [], []))}
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}), \
                unittest.mock.patch.dict(A, fake), unittest.mock.patch.object(os, "read", lambda fd, n: b"x"):
            b = A["start_bus"](cfg(portals=0, gnome_shell=1, kde=1), "Foo")
            self.assertIsNotNone(b)
            self.assertIn("--own=org.gnome.BrowserConnector", cmds[0])
            self.assertIn("--own=org.mpris.MediaPlayer2.plasma-browser-integration", cmds[0])
            A["stop_bus"](b)

class A11y(unittest.TestCase):
    def test_rules_only_let_the_app_register(self):
        r = A["a11y_rules"]()
        self.assertIn("--sloppy-names", r)
        self.assertFalse(any(x.startswith("--talk") for x in r))
        self.assertFalse(any("GenerateKeyboardEvent" in x or "GetChildren" in x for x in r))
        self.assertTrue(any("Socket.Embed" in x for x in r))

    def test_address_comes_from_the_environment_first(self):
        with unittest.mock.patch.dict(os.environ, {"AT_SPI_BUS_ADDRESS": "unix:path=/x/bus"}):
            self.assertEqual(A["a11y_bus_address"](), "unix:path=/x/bus")

    def test_address_ignores_non_unix_values_and_finds_the_socket(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"AT_SPI_BUS_ADDRESS": "tcp:host=x", "XDG_RUNTIME_DIR": d}), \
                unittest.mock.patch.dict(A, {"shutil": types.SimpleNamespace(which=lambda n: None)}):
            self.assertIsNone(A["a11y_bus_address"]())
            os.makedirs(f"{d}/at-spi"); open(f"{d}/at-spi/bus_0", "w").close()
            self.assertEqual(A["a11y_bus_address"](), f"unix:path={d}/at-spi/bus_0")

    def args(self, **kw):
        return A["bwrap_args"]("Foo", "/x", cfg(**kw), ["/app/AppRun"], a11y="/run/user/1/ajail/9.a11y")

    def test_bwrap_gets_the_bus_and_the_toolkit_variables(self):
        a = self.args(a11y=1)
        i = a.index("/run/user/1/ajail/9.a11y"); self.assertEqual(a[i - 1], "--ro-bind")
        for k, v in A["A11Y_ENV"].items():
            j = a.index(k); self.assertEqual(a[j + 1], v)
        self.assertIn("AT_SPI_BUS_ADDRESS", a)

    def test_nothing_without_the_switch_or_the_socket(self):
        self.assertNotIn("AT_SPI_BUS_ADDRESS", self.args(a11y=0))
        a = A["bwrap_args"]("Foo", "/x", cfg(a11y=1), ["/app/AppRun"])
        self.assertNotIn("AT_SPI_BUS_ADDRESS", a)

    def test_gtk_colors_are_shared_with_the_theme(self):
        for rel in (".config/gtk-3.0/colors.css", ".config/gtk-4.0/colors.css"): self.assertIn(rel, A["THEME"])

    def test_stale_a11y_sockets_are_swept(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            os.makedirs(f"{d}/ajail"); p = f"{d}/ajail/4194300.a11y"; open(p, "w").close()
            A["sweep_runtime"](); self.assertFalse(os.path.exists(p))

class DesktopRows(unittest.TestCase):
    def test_hidden_on_the_other_desktop_only(self):
        h = G["desktop_hidden"]
        self.assertTrue(h("kde", 0, "ubuntu:GNOME")); self.assertFalse(h("kde", 0, "KDE"))
        self.assertTrue(h("gnome_shell_manage", 0, "KDE")); self.assertFalse(h("gnome_shell", 0, "GNOME"))
        self.assertFalse(h("kde", 0, "XFCE")); self.assertFalse(h("kde", 0, ""))
        self.assertFalse(h("kde", 1, "GNOME"))          # on: stays visible so it can be turned off
        self.assertFalse(h("tray", 0, "GNOME"))

    def test_fold_only_big_desktops(self):
        f = G["folded_desktops"]
        self.assertEqual(f(["gnome_shell", "gnome_shell_manage", "kde"]), set())
        self.assertEqual(f(["gnome_shell"] * 4), {"GNOME"})

    def test_every_desktop_switch_exists_and_is_listed(self):
        keys = {k for _g, _d, items in G["SECTIONS"] for k, _t, _s in items}
        for k in G["DESKTOP_ONLY"]: self.assertIn(k, keys); self.assertIn(k, G["DEFAULTS"])
        for d in G["DESKTOP_ONLY"].values(): self.assertIn(d, G["DESKTOP_NAMES"])

class LogPage(unittest.TestCase):
    L = ["[2026-11-18 09:00:00] [Foo] START allow=[net,sound,gpu,wayland,portals,theme,x11] limits=[] seccomp=on dbus=on\n",
         "[2026-11-18 09:00:01] [Foo] EXIT code=0\n",
         "[2026-11-19 10:41:00] [Foo] START allow=[net,sound,gpu,wayland,portals,theme,userns] limits=[mem_max=512] seccomp=on dbus=on\n",
         "plain app output\n",
         "[2026-11-19 10:41:05] [Foo] EXIT code=1\n"]

    def test_latest_run_starts_at_the_last_start(self):
        self.assertEqual(G["latest_run_lines"](self.L), self.L[2:])
        self.assertEqual(G["latest_run_lines"](self.L[:2][1:]), self.L[1:2])      # no START: everything
        self.assertEqual(G["last_run"](self.L), (self.L[3:], 1))
        self.assertEqual(G["last_start"]([]), -1)

    def test_start_shows_only_differences(self):
        s = G["start_changes"]
        self.assertEqual(s(["net", "sound", "gpu", "wayland", "portals", "theme", "userns", "portal_screenshot", "portal_screencast",
                            "portal_notification", "portal_openuri", "portal_print", "portal_trash", "portal_email", "portal_inhibit", "portal_network"]), "(defaults)")
        t = s(["net", "x11"], "mem_max=512")
        self.assertIn("+X11", t); self.assertIn("-Audio", t); self.assertIn("limits mem_max=512", t); self.assertNotIn("+Internet", t)

    def test_rows_drop_todays_date_and_separate_runs(self):
        rows = G["log_rows"](self.L, "2026-11-19")
        text = ["".join(c for c, _t in r) for r in rows]
        self.assertTrue(text[0].startswith("[2026-11-18 09:00:00] "))
        self.assertTrue(any(t.startswith("[10:41:00] ") for t in text))
        self.assertEqual(text.count(G["DIVIDER"]), 1)                      # a rule before the second run only
        self.assertTrue(all("\n" not in t for t in text))
        self.assertIn("plain app output", text)

if __name__ == "__main__":
    unittest.main(verbosity=2)
