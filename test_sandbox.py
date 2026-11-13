#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Properties of the bwrap command and of the settings parser that must hold for every configuration.
They check what the sandbox does NOT get, so a change in src/20_core.py that opens something up fails here.
No sandbox or desktop needed."""
import contextlib, io, itertools, os, random, string, tempfile, unittest, unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))

def load(name):
    src = open(f"{HERE}/{name}").read().rstrip()
    assert src.endswith("\nmain()")
    ns = {"__name__": "under_test"}
    exec(compile(src[:-len("main()")], name, "exec"), ns)
    return ns

A = load("ajail")
HOME = A["HOME"]
SESSION = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "XDG_RUNTIME_DIR": "/run/user/1000",
           "SSH_AUTH_SOCK": "/tmp/agent.sock", "GITHUB_TOKEN": "secret", "AWS_SECRET_ACCESS_KEY": "secret",
           "PATH": "/home/me/bin:/usr/bin", "LANG": "en_US.UTF-8"}

def cfg(**kw):
    c = {**A["DEFAULTS"], **A["NUM"], "allow": [], "allow_ro": [], "env": [], "talk": [], "portal": [], "xdg": []}
    c.update(kw)
    return c

def build(c, **kw):
    with unittest.mock.patch.dict(os.environ, SESSION, clear=True), \
         unittest.mock.patch.object(A["os"].path, "exists", lambda p: True), \
         unittest.mock.patch.dict(A["bwrap_args"].__globals__, {"bwrap_has": lambda o: True, "wayland_path": lambda: "/run/user/1000/wayland-0"}):
        return A["bwrap_args"]("t", "/fake-app-root", c, ["/app/AppRun"], **kw)

def setenv(args):
    out, i = {}, 0
    while i < len(args):
        if args[i] == "--setenv": out[args[i + 1]] = args[i + 2]; i += 3
        else: i += 1
    return out

def binds(args):
    """(option, source, destination) for every bind-like option."""
    out = []
    for i, a in enumerate(args):
        if a in ("--bind", "--ro-bind", "--dev-bind", "--bind-try", "--ro-bind-try", "--dev-bind-try"):
            out.append((a, args[i + 1], args[i + 2]))
    return out

SWITCHES = [k for k in A["DEFAULTS"] if k not in ("app_writable",)]

class EveryConfig(unittest.TestCase):
    """Run the invariants over defaults, everything on, everything off, and a spread of random mixes."""
    def configs(self):
        yield cfg()
        yield cfg(**{k: 1 for k in SWITCHES})
        yield cfg(**{k: 0 for k in SWITCHES})
        rnd = random.Random(7)
        for _ in range(60):
            yield cfg(**{k: rnd.randint(0, 1) for k in SWITCHES})

    def test_always_isolated_and_cleared(self):
        for c in self.configs():
            a = build(c)
            for must in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv"):
                self.assertIn(must, a)

    def test_never_binds_whole_root_or_real_home(self):
        for c in self.configs():
            for _, src, dst in binds(build(c)):
                self.assertNotIn(src, ("/", "/home", "/root", "/etc", "/var", "/run", "/proc"), (src, dst))
                if dst == HOME: self.assertTrue(src.endswith(".appimage-homes/t"), src)

    def test_host_secrets_never_reach_the_environment(self):
        for c in self.configs():
            env = setenv(build(c))
            for k in ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"): self.assertNotIn(k, env)
            self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/usr/sbin")

    def test_ssh_agent_only_when_enabled(self):
        for c in self.configs():
            env = setenv(build(c))
            self.assertEqual("SSH_AUTH_SOCK" in env, bool(c["ssh_agent"]))

    def test_network_only_when_enabled(self):
        for c in self.configs():
            self.assertEqual("--share-net" in build(c), bool(c["net"]))

    def test_devices_follow_their_switch(self):
        with unittest.mock.patch.object(A["glob"], "glob", lambda p: {"/dev/video*": ["/dev/video0"], "/dev/nvidia*": ["/dev/nvidia0"]}.get(p, [])):
            for c in self.configs():
                dsts = [d for _, _, d in binds(build(c))]
                self.assertEqual("/dev/video0" in dsts, bool(c["camera"]))
                self.assertEqual("/dev/nvidia0" in dsts, bool(c["gpu"]))
                self.assertEqual("/dev/input" in dsts, bool(c["input"]))

    def test_render_only_gpu_binds_no_card_nodes(self):
        nodes = ["/dev/dri/card0", "/dev/dri/renderD128", "/dev/dri/renderD129"]
        fake = lambda p: [n for n in nodes if __import__("fnmatch").fnmatch(n, p)] if p.startswith("/dev/dri/") else []
        with unittest.mock.patch.object(A["glob"], "glob", fake):
            full = [d for _, _, d in binds(build(cfg(gpu=1, gpu_render_only=0)))]
            only = [d for _, _, d in binds(build(cfg(gpu=1, gpu_render_only=1)))]
            off = [d for _, _, d in binds(build(cfg(gpu=0, gpu_render_only=1)))]
        self.assertIn("/dev/dri", full)
        self.assertNotIn("/dev/dri", only)
        self.assertEqual([d for d in only if d.startswith("/dev/dri")], ["/dev/dri/renderD128", "/dev/dri/renderD129"])
        self.assertFalse([d for d in off if d.startswith("/dev/dri")])     # gpu=0 wins over render-only

    def test_x11_socket_only_when_enabled(self):
        for c in self.configs():
            dsts = [d for _, _, d in binds(build(c))]
            self.assertEqual("/tmp/.X11-unix/X0" in dsts, bool(c["x11"]))

    def test_dbus_only_through_the_proxy(self):
        for c in self.configs():
            a = build(c)
            self.assertNotIn("/run/dbus/system_bus_socket", [d for _, _, d in binds(a)])
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", setenv(a))     # only set when a filtered bus is passed in
        a = build(cfg(), bus="/run/user/1000/ajail/1.bus")
        self.assertEqual(setenv(a)["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")

    def test_userns_off_disables_nested_sandboxes(self):
        for c in self.configs():
            a = build(c)
            self.assertEqual("--disable-userns" in a, not c["userns"])
            if "--disable-userns" in a: self.assertIn("--unshare-user", a)     # bwrap refuses --disable-userns without it

class PrivateNetwork(unittest.TestCase):
    """'Keep localhost private' (net_private): the app runs behind pasta in a network of its own, so it reaches the
    internet but not this computer. pasta closes inherited descriptors, so the seccomp filter goes in through a file."""
    SECPATH = "/run/user/1000/ajail/1.d/seccomp.bpf"

    def test_the_pasta_command_is_exactly_this(self):
        self.assertEqual(A["PASTA"], ["pasta", "--quiet", "--config-net", "--no-map-gw", "--dns-forward", "169.254.1.1",
                                      "-t", "none", "-u", "none", "-T", "none", "-U", "none", "--"])
        inner = ["bwrap", "--unshare-all", "--seccomp", "9"]
        self.assertEqual(A["net_wrap"](inner), A["PASTA"] + inner)
        self.assertEqual(A["net_wrap"](inner, self.SECPATH),
                         A["PASTA"] + ["/bin/sh", "-c", 'exec 9<"$1"; shift; exec "$@"', "sh", self.SECPATH] + inner)
        self.assertEqual(A["SEC_FD"], 9)

    def test_it_needs_the_network_to_be_on(self):
        self.assertTrue(A["private_net"](cfg(net=1, net_private=1)))
        for c in (cfg(net=1, net_private=0), cfg(net=0, net_private=1), cfg(net=0, net_private=0)):
            self.assertFalse(A["private_net"](c))

    def test_bwrap_keeps_your_ids_and_reads_the_filter_from_descriptor_9(self):
        a = build(cfg(net=1, net_private=1), sec=A["SEC_FD"])
        i = a.index("--uid")
        self.assertEqual(a[i:i + 4], ["--uid", str(os.getuid()), "--gid", str(os.getgid())])
        self.assertIn("--share-net", a)                                   # pasta's network, not the host's
        self.assertEqual(a[a.index("--seccomp") + 1], "9")
        for c in (cfg(net=1, net_private=0), cfg(net=0, net_private=1)):
            self.assertNotIn("--uid", build(c)); self.assertNotIn("--gid", build(c))
        self.assertNotIn("--share-net", build(cfg(net=0, net_private=1)))     # no network means none, with or without the switch

    def test_resolv_conf_points_at_the_forwarder_only_with_private_net(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(A, {"machine_id": lambda n: "0" * 32 + "\n"}):
            gen = A["make_files"](cfg(net=1, net_private=1), d)
            with open(gen["resolv.conf"]) as f: self.assertEqual(f.read(), "nameserver 169.254.1.1\n")
            for c in (cfg(net=1, net_private=0), cfg(net=0, net_private=1)):
                self.assertNotIn("resolv.conf", A["make_files"](c, d))
            a = build(cfg(net=1, net_private=1), gen=gen)
        self.assertIn(("--ro-bind", gen["resolv.conf"], "/etc/resolv.conf"), binds(a))
        self.assertNotIn("/etc/resolv.conf", [s for _, s, _ in binds(a)])      # not also the host's own file

class Shares(unittest.TestCase):
    def test_hidden_and_outside_paths_are_skipped(self):
        with tempfile.TemporaryDirectory() as t, unittest.mock.patch.dict(A["safe_path"].__globals__, {"HOME": t}):
            os.makedirs(f"{t}/ok"); os.makedirs(f"{t}/.ssh")
            real = os.path.realpath
            self.assertEqual(A["safe_path"](f"{t}/ok"), real(f"{t}/ok"))
            self.assertIsNone(A["safe_path"](f"{t}/.ssh"))
            self.assertIsNone(A["safe_path"]("/etc"))
            self.assertIsNone(A["safe_path"]("/usr"))

class ParserFuzz(unittest.TestCase):
    """Whatever is in a settings file, parse_cfg must not crash and must not grant more than a plain setting can."""
    def parse(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f: f.write(text)
        try:
            with unittest.mock.patch.dict(A["parse_cfg"].__globals__, {"warn": lambda *a, **k: None}):
                return A["parse_cfg"](f.name)
        finally: os.unlink(f.name)

    def test_random_input_never_crashes(self):
        rnd = random.Random(1)
        keys = list(A["DEFAULTS"]) + list(A["NUM"]) + list(A["LISTS"]) + ["profile", "flag", "bogus"]
        alphabet = string.printable + "\u00e9\u202e\x00"
        for _ in range(500):
            lines = []
            for _ in range(rnd.randint(1, 6)):
                k = rnd.choice(keys + ["", "=", "#"])
                v = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 12)))
                lines.append(rnd.choice([f"{k}={v}", f"{k} = {v}  # c", k + v, v]))
            d = self.parse("\n".join(lines))
            self.assertIsNotNone(d)
            for k, v in d["s"].items():
                self.assertIn(k, list(A["DEFAULTS"]) + list(A["NUM"]))
                self.assertIsInstance(v, int)

    def test_switches_accept_only_0_and_1(self):
        for k, v in itertools.product(A["DEFAULTS"], ["2", "-1", "yes", "true", "", "1 1", "01x"]):
            self.assertNotIn(k, self.parse(f"{k}={v}")["s"], (k, v))

    def test_unusable_lines_grant_nothing(self):
        d = self.parse("net=0\nallow=\ntalk=org.*\ntalk=org.freedesktop.*\nportal=Bogus\nportal=--Location\nwhatever\n")
        self.assertEqual((d["allow"], d["talk"], d["portal"]), ([], [], []))

    def test_wide_dbus_wildcards_are_refused_narrow_ones_kept(self):
        d = self.parse("talk=org.example.App.*\ntalk=org.example.App\ntalk=*\ntalk=org..x\n")
        self.assertEqual(d["talk"], ["org.example.App.*", "org.example.App"])

class OldBubblewrapFailsClosed(unittest.TestCase):
    """Isolation the user asked for is never silently dropped because bubblewrap is too old to do it."""
    def build_without(self, missing, c):
        err = io.StringIO()
        with unittest.mock.patch.dict(os.environ, SESSION, clear=True), \
             unittest.mock.patch.object(A["os"].path, "exists", lambda p: True), \
             unittest.mock.patch.dict(A["bwrap_args"].__globals__, {"bwrap_has": lambda o: o != missing,
                                                                   "wayland_path": lambda: "/run/user/1000/wayland-0"}), \
             contextlib.redirect_stderr(err):
            try: return A["bwrap_args"]("t", "/fake-app-root", c, ["/app/AppRun"]), err.getvalue()
            except SystemExit as e: return e, err.getvalue()

    def test_userns_off_without_disable_userns_refuses(self):
        r, err = self.build_without("--disable-userns", cfg(userns=0))
        self.assertIsInstance(r, SystemExit); self.assertNotEqual(r.code, 0)
        self.assertIn("--disable-userns", err); self.assertIn("userns=1", err)

    def test_privacy_without_hostname_refuses(self):
        r, err = self.build_without("--hostname", cfg(privacy=1))
        self.assertIsInstance(r, SystemExit); self.assertNotEqual(r.code, 0)
        self.assertIn("--hostname", err); self.assertIn("privacy=0", err)

    def test_nothing_is_refused_that_was_not_asked_for(self):     # the defaults still run on an old bubblewrap
        for missing in ("--disable-userns", "--hostname"):
            r, err = self.build_without(missing, cfg())
            self.assertIsInstance(r, list, missing); self.assertEqual(err, "")

    def test_supported_options_are_used(self):
        a = build(cfg(userns=0, privacy=1))
        self.assertIn("--disable-userns", a); self.assertIn("--hostname", a)

if __name__ == "__main__":
    unittest.main()
