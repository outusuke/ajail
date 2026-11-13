#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for the "compat_env" option: which variables reach the sandbox, and that nothing else ever does.
import os, shlex, tempfile, unittest, unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))

def load(name):
    src = open(f"{HERE}/{name}").read().rstrip()
    assert src.endswith("\nmain()")
    ns = {"__name__": "under_test"}
    exec(compile(src[:-len("main()")], name, "exec"), ns)
    return ns

A = load("ajail")

ALLOWED = ["WGPU_BACKEND", "WGPU_ADAPTER_NAME", "WGPU_POWER_PREF", "ICED_BACKEND", "LIBGL_ALWAYS_SOFTWARE",
           "LIBGL_DRI3_DISABLE", "VK_ICD_FILENAMES", "VK_DRIVER_FILES", "__GLX_VENDOR_LIBRARY_NAME",
           "__NV_PRIME_RENDER_OFFLOAD", "DRI_PRIME", "SDL_VIDEODRIVER", "SDL_AUDIODRIVER", "QT_QPA_PLATFORM",
           "QT_QPA_PLATFORMTHEME", "QT_SCALE_FACTOR", "GDK_BACKEND", "GDK_SCALE", "MOZ_ENABLE_WAYLAND"]
DENIED = ["PATH", "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY",
          "API_KEY", "HOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "XAUTHORITY", "DISPLAY", "mesa_x", "XMESA_X", "MESA", "RADV",
          "WGPU_BACKEND_EXTRA", "wgpu_backend", "QT_PLUGIN_PATH", "GDK_PIXBUF_MODULE_FILE", "SDL_VIDEO_X11_VISUALID",
          "MOZ_ENABLE_WAYLAND2", "EDITOR"]

def cfg(**kw):
    c = {**A["DEFAULTS"], **A["NUM"], "allow": [], "allow_ro": [], "env": [], "talk": [], "portal": [], "xdg": [],
         "theme": 0, "tmp_home": 1}
    c.update(kw)
    return c

def setenvs(args):
    out, i = {}, 0
    while i < len(args):
        if args[i] == "--setenv": out[args[i + 1]] = args[i + 2]; i += 3
        else: i += 1
    return out

def build(c, environ):
    with unittest.mock.patch.dict(os.environ, environ, clear=True):
        return setenvs(A["bwrap_args"]("t", "/nonexistent-root", c, ["/app/AppRun"]))

class Helper(unittest.TestCase):
    def test_every_allowed_name_passes(self):
        env = {k: "v-" + k for k in ALLOWED}
        self.assertEqual(A["compat_env"](env), env)

    def test_prefixes(self):
        env = {"MESA_GLTHREAD": "true", "MESA_LOADER_DRIVER_OVERRIDE": "zink", "RADV_PERFTEST": "gpl", "RADV_DEBUG": "x"}
        self.assertEqual(A["compat_env"](env), env)

    def test_nothing_else_passes(self):
        self.assertEqual(A["compat_env"]({k: "x" for k in DENIED}), {})

    def test_only_variables_that_are_set(self):
        self.assertEqual(A["compat_env"]({"GDK_SCALE": "2"}), {"GDK_SCALE": "2"})
        self.assertEqual(A["compat_env"]({}), {})

    def test_empty_value_counts_as_set(self):
        self.assertEqual(A["compat_env"]({"LIBGL_ALWAYS_SOFTWARE": ""}), {"LIBGL_ALWAYS_SOFTWARE": ""})

    def test_list_matches_the_spec_exactly(self):
        self.assertEqual(sorted(A["COMPAT_ENV"]), sorted(ALLOWED))
        self.assertEqual(sorted(A["COMPAT_ENV_PREFIXES"]), ["MESA_", "RADV_"])

class Sandbox(unittest.TestCase):
    ENV = {**{k: "val" for k in ALLOWED}, "MESA_X": "1", "RADV_Y": "2", "SSH_AUTH_SOCK": "/tmp/agent", "GITHUB_TOKEN": "s3cret",
           "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x", "DBUS_STARTER_BUS_TYPE": "session", "PATH": "/home/me/evil/bin:/usr/bin",
           "LD_PRELOAD": "/x.so", "AWS_SECRET_ACCESS_KEY": "k", "XDG_RUNTIME_DIR": "/run/user/1"}

    def test_off_by_default(self):
        self.assertEqual(A["DEFAULTS"]["compat_env"], 0)
        e = build(cfg(), self.ENV)
        for k in ALLOWED + ["MESA_X", "RADV_Y"]: self.assertNotIn(k, e)

    def test_on_passes_exactly_the_allowlist(self):
        e = build(cfg(compat_env=1), self.ENV)
        for k in ALLOWED + ["MESA_X", "RADV_Y"]: self.assertEqual(e.get(k), self.ENV[k], k)
        for k in ("SSH_AUTH_SOCK", "GITHUB_TOKEN", "DBUS_STARTER_BUS_TYPE", "LD_PRELOAD", "AWS_SECRET_ACCESS_KEY", "DBUS_SESSION_BUS_ADDRESS"):
            self.assertNotIn(k, e, k)

    def test_on_changes_nothing_but_the_allowlist(self):
        off, on = build(cfg(), self.ENV), build(cfg(compat_env=1), self.ENV)
        self.assertEqual(set(on) - set(off), set(ALLOWED) | {"MESA_X", "RADV_Y"})
        for k in off: self.assertEqual(on[k], off[k], k)

    def test_path_is_never_taken_from_the_session(self):
        self.assertEqual(build(cfg(compat_env=1), self.ENV)["PATH"], "/usr/local/bin:/usr/bin:/usr/sbin")

    def test_only_set_variables_appear(self):
        e = build(cfg(compat_env=1), {"GDK_SCALE": "2", "XDG_RUNTIME_DIR": "/run/user/1"})
        self.assertEqual(e["GDK_SCALE"], "2"); self.assertNotIn("GDK_BACKEND", e)

    def test_env_line_still_wins(self):
        e = build(cfg(compat_env=1, env=["GDK_SCALE=3", "MESA_X=9"]), self.ENV)
        self.assertEqual((e["GDK_SCALE"], e["MESA_X"]), ("3", "9"))

    def test_never_replaces_what_ajail_sets(self):
        # nothing on the list overlaps, but the guard must hold if the list ever grows
        with unittest.mock.patch.dict(A, {"COMPAT_ENV": A["COMPAT_ENV"] + ("PATH", "HOME")}):
            e = build(cfg(compat_env=1), {"PATH": "/evil", "HOME": "/evil", "XDG_RUNTIME_DIR": "/run/user/1"})
        self.assertEqual(e["PATH"], "/usr/local/bin:/usr/bin:/usr/sbin"); self.assertEqual(e["HOME"], A["HOME"])

    def test_ssh_agent_switch_is_independent(self):
        e = build(cfg(compat_env=1, ssh_agent=0), self.ENV); self.assertNotIn("SSH_AUTH_SOCK", e)

class Config(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); A["LOG"] = f"{self.d}/l"; A["STATE"] = self.d
        A["CONF"] = self.d; os.makedirs(f"{self.d}/apps")

    def test_key_parses_and_app_overrides_default(self):
        open(f"{self.d}/default.conf", "w").write("compat_env=1\n")
        self.assertEqual(A["load_cfg"]("x")["compat_env"], 1)
        open(f"{self.d}/apps/x.conf", "w").write("compat_env = 0  # off here\n")
        self.assertEqual(A["load_cfg"]("x")["compat_env"], 0)

    def test_bad_value_is_refused(self):
        open(f"{self.d}/default.conf", "w").write("compat_env=yes\n")
        with unittest.mock.patch("sys.stderr"):
            self.assertEqual(A["load_cfg"]("x")["compat_env"], 0)

    def test_preview_shows_the_variables(self):
        import contextlib, io
        open(f"{self.d}/default.conf", "w").write("compat_env=1\ntheme=0\nportals=0\n")
        env = {"WGPU_BACKEND": "gl", "MESA_DEBUG": "1", "GITHUB_TOKEN": "s3cret", "XDG_RUNTIME_DIR": "/run/user/1"}
        out = io.StringIO()
        with unittest.mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            A["cmd_preview"]("t")
        words = shlex.split(out.getvalue())
        self.assertEqual(setenvs(words)["WGPU_BACKEND"], "gl"); self.assertEqual(setenvs(words)["MESA_DEBUG"], "1")
        self.assertNotIn("s3cret", out.getvalue())

class Cli(unittest.TestCase):
    def test_flag_forces_the_option_for_one_run(self):
        seen = {}
        def fake_launch(name, root, cfg, extra, **k): seen["cfg"] = cfg
        with unittest.mock.patch.dict(A, {"launch": fake_launch, "guard": lambda: None, "check_display": lambda c: None,
                                          "installed_kind": lambda n: "root", "load_cfg": lambda n: cfg()}):
            A["run_installed"]("t", [], True); self.assertEqual(seen["cfg"]["compat_env"], 1)
            A["run_installed"]("t", []); self.assertEqual(seen["cfg"]["compat_env"], 0)

    def test_help_and_completions_mention_it(self):
        self.assertIn("--compat-env", A["HELP"])
        for f in ("ajail.bash", "_ajail", "ajail.fish"):
            self.assertIn("compat-env", open(f"{HERE}/completions/{f}").read(), f)

    def test_readme_lists_every_variable(self):
        r = open(f"{HERE}/README.md").read()
        for k in ALLOWED: self.assertIn(f"`{k}`", r, k)
        for p in ("MESA_", "RADV_"): self.assertIn(f"`{p}`", r)
        self.assertIn("`compat_env`", r)

    def test_gui_has_the_switch_and_matching_default(self):
        g = open(f"{HERE}/ajail-gui").read()
        self.assertIn('("compat_env", "Pass graphics and toolkit settings"', g)

if __name__ == "__main__":
    unittest.main(verbosity=2)
