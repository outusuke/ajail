#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line option checks: an unknown --option must be an error, never silently ignored.
No sandbox needed; every case fails before anything is read or written."""
import ast, os, re, subprocess, sys, unittest

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

class DesktopMenu(unittest.TestCase):
    """ajail desktop --install-menu only ever starts a root-owned ajail-gui; --remove-menu needs no ajail-gui at all."""
    def setUp(self):
        import tempfile, unittest.mock
        self.mock = unittest.mock
        self.ns = {"__name__": "under_test"}
        exec(compile(open(AJAIL).read().rstrip()[:-len("main()")], "ajail", "exec"), self.ns)
        self.t = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.t, True)
        self.ns["HOME"] = self.t

    def call(self, *args):
        with unittest.mock.patch("sys.stdout", new=__import__("io").StringIO()) as out, \
             unittest.mock.patch("sys.stderr", new=__import__("io").StringIO()) as err:
            try: self.ns["cmd_desktop"](list(args)); code = None
            except SystemExit as e: code = e.code
        return code, out.getvalue(), err.getvalue()

    def stat_as(self, uid, mode):
        real = os.stat
        def stat(p, *a, **k):
            return os.stat_result((mode, 0, 0, 1, uid, uid, 0, 0, 0, 0)) if str(p).endswith("/ajail-gui") else real(p, *a, **k)
        return stat

    def test_only_the_two_options_are_accepted(self):
        for args in ((), ("--install",), ("--install-menu", "x"), ("--install-opener",)):
            code, _, err = self.call(*args)
            self.assertNotEqual(code, 0, args); self.assertIn("usage: ajail desktop", err)

    def test_remove_takes_out_the_launcher_and_icon(self):
        apps = f"{self.t}/.local/share/applications"; icons = f"{self.t}/.local/share/icons/hicolor/scalable/apps"
        self.assertIn("nothing to remove", self.call("--remove-menu")[1])
        os.makedirs(apps); os.makedirs(icons)
        for f in (f"{apps}/org.ajail.Gui.desktop", f"{icons}/org.ajail.Gui.svg", f"{apps}/other.desktop"): open(f, "w").close()
        with self.mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            self.assertIn("removed", self.call("--remove-menu")[1])
        self.assertEqual(os.listdir(apps), ["other.desktop"]); self.assertEqual(os.listdir(icons), [])

    def test_install_needs_a_root_owned_copy_nobody_else_can_write(self):
        for uid, mode in ((1000, 0o100755), (0, 0o100775), (0, 0o100757)):
            with self.mock.patch("os.stat", self.stat_as(uid, mode)), self.mock.patch.object(self.ns["subprocess"], "run") as run:
                code, _, err = self.call("--install-menu")
            self.assertNotEqual(code, 0); self.assertIn("ajail-gui is not installed", err); run.assert_not_called()
        with self.mock.patch("os.stat", self.stat_as(0, 0o100755)), \
             self.mock.patch.object(self.ns["subprocess"], "run", return_value=self.mock.Mock(returncode=0)) as run:
            code, _, _ = self.call("--install-menu")
        self.assertEqual(code, 0); self.assertEqual(run.call_args[0][0], ["/usr/local/bin/ajail-gui", "--install-menu"])

class Completions(unittest.TestCase):
    """The shell completions list the commands and options ajail really has, no more and no fewer."""
    def setUp(self):
        self.tree = ast.parse(open(AJAIL).read())
        self.dir = os.path.join(os.path.dirname(AJAIL), "completions")

    def real_commands(self):
        main = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        out = set()
        for c in ast.walk(main):                                    # every  c == "name"  /  c in ("a", "b")  in main()
            if isinstance(c, ast.Compare) and isinstance(c.left, ast.Name) and c.left.id == "c":
                out |= {x.value for x in ast.walk(c.comparators[0]) if isinstance(x, ast.Constant) and isinstance(x.value, str)}
        return out - {"dump", "-h", "--help"}                      # dump is only for ajail-gui

    def real_options(self):
        help_ = next(ast.literal_eval(n.value) for n in self.tree.body
                     if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "HELP")
        return set(re.findall(r"--[a-z][a-z-]*", help_))

    def read(self, name): return open(os.path.join(self.dir, name)).read()

    def test_command_lists(self):
        want = self.real_commands()
        self.assertIn("install", want); self.assertIn("run-file", want)      # the scan itself found something
        for name, rx in (("ajail.bash", r'compgen -W "(on off[^"]*)"'), ("_ajail", r"cmds=\(([^)]*)\)"),
                         ("ajail.fish", r'-a "(on off[^"]*)"')):
            m = re.search(rx, self.read(name))
            self.assertTrue(m, f"{name}: no command list found")
            self.assertEqual(set(m.group(1).split()), want, name)

    def test_options(self):
        want = self.real_options()
        self.assertIn("--compat-env", want); self.assertIn("--discard-image", want)
        self.assertEqual(set(re.findall(r"--[a-z][a-z-]*", self.read("ajail.bash"))), want, "ajail.bash")
        self.assertEqual(set(re.findall(r"--[a-z][a-z-]*", self.read("_ajail"))), want, "_ajail")
        self.assertEqual({"--" + o for o in re.findall(r"^complete .* -l ([a-z-]+)", self.read("ajail.fish"), re.M)}, want, "ajail.fish")

if __name__ == "__main__":
    unittest.main()
