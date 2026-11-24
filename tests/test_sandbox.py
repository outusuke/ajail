#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Properties of the bwrap command and of the settings parser that must hold for every configuration.
They check what the sandbox does NOT get, so a change in src/20_core.py that opens something up fails here.
No sandbox or desktop needed."""
import contextlib, io, itertools, os, random, string, tempfile, unittest, unittest.mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

def build(c, live=False, **kw):
    # preview mode (NO_LOG) by default: like 'ajail preview', building the command creates no folders; live=True creates them
    old, A["NO_LOG"][0] = A["NO_LOG"][0], not live
    try: return _build(c, **kw)
    finally: A["NO_LOG"][0] = old

def _build(c, **kw):
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
                self.assertEqual("/dev/ntsync" in dsts, bool(c["ntsync"]))

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

    def test_wayland_socket_only_when_enabled(self):
        for c in self.configs():
            a = build(c)
            dsts = [d for _, _, d in binds(a)]
            on = bool(c["wayland"]) and not c["wayland_strict"]       # no restricted socket here, so "only if restricted" means none
            self.assertEqual("/run/user/1000/wayland-0" in dsts, on)
            env = setenv(a)
            self.assertEqual("WAYLAND_DISPLAY" in env, on)
            self.assertEqual(env.get("ELECTRON_OZONE_PLATFORM_HINT"), ("auto" if c["x11"] else "wayland") if on else "x11")

    def test_x11_alone_needs_no_wayland_session(self):
        def check(**kw):
            with unittest.mock.patch.dict(A["check_display"].__globals__, {"wayland_path": lambda: None}), \
                 unittest.mock.patch.dict(A["check_display"].__globals__, {"die": lambda m: (_ for _ in ()).throw(SystemExit(m))}):
                return A["check_display"](cfg(**kw))
        with self.assertRaises(SystemExit): check(wayland=1, x11=0)      # Wayland is the only display and it is missing
        check(wayland=1, x11=1)
        check(wayland=0, x11=1)
        check(wayland=0, x11=0)                                          # no display at all is a choice, not an error

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

    def test_pasta_can_open_the_bwrapinfo_file_too(self):
        inner = ["bwrap"]
        self.assertEqual(A["net_wrap"](inner, self.SECPATH, "/i.json"),
                         A["PASTA"] + ["/bin/sh", "-c", 'exec 9<"$1"; exec 8>"$2"; shift 2; exec "$@"', "sh", self.SECPATH, "/i.json"] + inner)
        self.assertEqual(A["net_wrap"](inner, None, "/i.json"),
                         A["PASTA"] + ["/bin/sh", "-c", 'exec 8>"$1"; shift; exec "$@"', "sh", "/i.json"] + inner)
        self.assertEqual(A["INFO_FD"], 8)

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

    def test_machine_id_is_private_stable_and_never_written_through_a_link(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(A, {"CONF": d}):
            first = A["machine_id"]("Foo")
            self.assertEqual(len(first.strip()), 32)
            self.assertEqual(A["machine_id"]("Foo"), first)                    # stable
            self.assertEqual(oct(os.stat(f"{d}/apps/Foo.machine-id").st_mode & 0o777), "0o600")
            target = f"{d}/planted"
            os.symlink(target, f"{d}/apps/Bar.machine-id")                     # a dangling link where the file would go
            A["machine_id"]("Bar")
            self.assertFalse(os.path.exists(target))

    def test_a_broken_machine_id_is_replaced_and_then_stable(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(A, {"CONF": d}):
            os.makedirs(f"{d}/apps")
            for bad in ("", "short\n"):
                with open(f"{d}/apps/Foo.machine-id", "w") as f: f.write(bad)
                first = A["machine_id"]("Foo")
                self.assertEqual(len(first.strip()), 32)
                self.assertEqual(A["machine_id"]("Foo"), first)
            self.assertEqual(os.listdir(f"{d}/apps"), ["Foo.machine-id"])

    def test_resolv_conf_points_at_the_forwarder_only_with_private_net(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict(A, {"machine_id": lambda n: "0" * 32 + "\n"}):
            gen = A["make_files"](cfg(net=1, net_private=1), d)
            with open(gen["resolv.conf"]) as f: self.assertEqual(f.read(), "nameserver 169.254.1.1\n")
            for c in (cfg(net=1, net_private=0), cfg(net=0, net_private=1)):
                self.assertNotIn("resolv.conf", A["make_files"](c, d))
            a = build(cfg(net=1, net_private=1), gen=gen)
        self.assertIn(("--ro-bind", gen["resolv.conf"], "/etc/resolv.conf"), binds(a))
        self.assertNotIn("/etc/resolv.conf", [s for _, s, _ in binds(a)])      # not also the host's own file

class PortalApp(unittest.TestCase):
    """With portals on, the portals must find the app: bwrapinfo.json, toolkit variables, and its own document folder."""
    def test_portals_tell_toolkits_and_bind_the_document_folder(self):
        a = build(cfg(portals=1), bus="/run/b.sock", info=7)
        self.assertEqual(a[a.index("--info-fd") + 1], "7")
        i = a.index("--bind-try")
        self.assertTrue(a[i + 1].endswith("/doc/by-app/io.github.outusuke.ajail.Run.t"))
        self.assertTrue(a[i + 2].endswith("/doc"))
        envs = setenv(a)
        self.assertEqual(envs["GTK_USE_PORTAL"], "1")
        self.assertEqual(envs["QT_QPA_PLATFORMTHEME"], "xdgdesktopportal")
        off = build(cfg(portals=0, tray=1), bus="/run/b.sock")
        self.assertNotIn("--info-fd", off); self.assertNotIn("GTK_USE_PORTAL", off)

    def test_gtk_portal_only_decides_the_gtk_variable(self):
        for v, gtk in (("1", True), ("auto", True), ("0", False)):      # "auto" reaching here unsettled counts as on, as before
            envs = setenv(build(cfg(portals=1, gtk_portal=v), bus="/run/b.sock", info=7))
            self.assertEqual("GTK_USE_PORTAL" in envs, gtk, v)
            self.assertEqual(envs["QT_QPA_PLATFORMTHEME"], "xdgdesktopportal", v)
        self.assertEqual(setenv(build(cfg(portals=1), bus="/run/b.sock", info=7))["GTK_USE_PORTAL"], "1")     # no gtk_portal in the config: on
        for v in ("1", "auto"):
            self.assertNotIn("GTK_USE_PORTAL", build(cfg(portals=0, tray=1, gtk_portal=v), bus="/run/b.sock"))   # forcing 1 does not bypass portals=0

class GtkPortalAuto(unittest.TestCase):
    """gtk_portal=auto: set GTK_USE_PORTAL unless the host's cursor size is 0 (the one confirmed crash); 1 and 0 never ask the host."""
    def settle(self, cursor, no_log=False, **kw):
        asked, logged = [], []
        def fake_get(schema, key): asked.append((schema, key)); return cursor
        with unittest.mock.patch.dict(A, {"gsettings_get": fake_get, "log": lambda m, n=None: logged.append((m, n))}):
            A["NO_LOG"][0] = no_log
            try: got = A["settle_gtk_portal"](cfg(**kw), "t")
            finally: A["NO_LOG"][0] = False
        return got, asked, logged

    def test_auto_is_on_unless_the_cursor_size_is_zero(self):
        for cursor in ("24", "1", "256", None, "", "uint32 0", "'x'"):       # a normal size, or nothing usable to read
            got, _, logged = self.settle(cursor, portals=1, gtk_portal="auto")
            self.assertEqual((got, logged), ("1", []), cursor)
        got, asked, logged = self.settle("0", portals=1, gtk_portal="auto")
        self.assertEqual(got, "0"); self.assertEqual(asked, [("org.gnome.desktop.interface", "cursor-size")])
        self.assertEqual(len(logged), 1); self.assertIn("cursor size is 0", logged[0][0]); self.assertEqual(logged[0][1], "t")

    def test_explicit_values_win_and_do_not_read_the_host(self):
        for v in ("1", "0"):
            got, asked, logged = self.settle("0", portals=1, gtk_portal=v)
            self.assertEqual((got, asked, logged), (v, [], []), v)

    def test_nothing_to_decide_without_portals(self):
        got, asked, logged = self.settle("0", portals=0, gtk_portal="auto")
        self.assertEqual((got, asked, logged), ("auto", [], []))

    def test_preview_decides_the_same_way_but_writes_no_log_line(self):
        got, _, logged = self.settle("0", no_log=True, portals=1, gtk_portal="auto")
        self.assertEqual((got, logged), ("0", []))

    def test_cursor_size_zero_is_told_apart_from_unset(self):
        # host_cursor() folds 0 into "unset"; the GTK_USE_PORTAL decision must not
        with unittest.mock.patch.dict(A, {"gsettings_get": lambda s, k: "0"}):
            self.assertEqual(A["host_cursor"]()[1], None); self.assertTrue(A["cursor_size_is_zero"]())
        with unittest.mock.patch.dict(A, {"gsettings_get": lambda s, k: None}):
            self.assertFalse(A["cursor_size_is_zero"]())

    def test_the_command_that_preview_prints_follows_the_decision(self):
        import contextlib, shlex
        d = tempfile.mkdtemp(); os.makedirs(f"{d}/apps")
        open(f"{d}/default.conf", "w").write("theme=0\nportals=1\n")
        for cursor, want in (("0", False), ("24", True)):
            out = io.StringIO()
            with unittest.mock.patch.dict(A, {"CONF": d, "gsettings_get": lambda s, k: cursor, "want_bus": lambda c: True}), \
                 unittest.mock.patch.dict(os.environ, SESSION, clear=True), contextlib.redirect_stdout(out):
                A["cmd_preview"]("t")
            self.assertEqual("GTK_USE_PORTAL" in setenv(shlex.split(out.getvalue())), want, cursor)

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

class SingleInstance(unittest.TestCase):
    def test_shares_a_private_tmpdir_per_app_and_keeps_tmp_private(self):
        d = tempfile.mkdtemp(); self.addCleanup(__import__("shutil").rmtree, d, True)
        si = f"{d}/ajail/single/t"
        with unittest.mock.patch.dict(A["bwrap_args"].__globals__, {"runtime_dir": lambda: d}):
            a = build(cfg(single_instance=1), live=True)
            self.assertEqual(os.stat(si).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(f"{d}/ajail").st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(f"{d}/ajail/single").st_mode & 0o777, 0o700)
            self.assertEqual(setenv(a)["TMPDIR"], si)
            self.assertIn(["--bind", si, si], [a[i:i + 3] for i in range(len(a) - 2)])
            self.assertIn(["--tmpfs", "/tmp"], [a[i:i + 2] for i in range(len(a) - 1)])      # /tmp is still private
            b = build(cfg())          # off by default: nothing shared, TMPDIR untouched
            self.assertNotIn("TMPDIR", setenv(b)); self.assertFalse(any("/single/" in x for x in b))

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

    def test_nothing_is_refused_that_was_not_asked_for(self):
        r, err = self.build_without("--hostname", cfg())
        self.assertIsInstance(r, list); self.assertEqual(err, "")

    def test_supported_options_are_used(self):
        a = build(cfg(userns=0, privacy=1))
        self.assertIn("--disable-userns", a); self.assertIn("--hostname", a)

class SeccompList(unittest.TestCase):
    """What the generated filter blocks, read back from the BPF program itself."""
    def blocked(self, harden):
        import platform
        f = A["seccomp_file"](harden); data = f.read(); f.close()
        audit = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}[platform.machine()]
        return {nr for nr in range(512) if self.run_bpf(data, nr, audit, 1) == 0x00050001}

    @staticmethod
    def run_bpf(data, nr, arch, arg0=0):
        import struct
        ins = [struct.unpack("=HBBI", data[i:i + 8]) for i in range(0, len(data), 8)]
        pc, acc = 0, 0
        while True:
            code, jt, jf, k = ins[pc]
            if code == 0x20: acc = {0: nr, 4: arch, 16: arg0 & 0xFFFFFFFF}[k]; pc += 1
            elif code == 0x15: pc += 1 + (jt if acc == k else jf)
            elif code == 0x35: pc += 1 + (jt if acc >= k else jf)
            elif code == 0x06: return k
            else: raise AssertionError(f"unexpected instruction {code:#x}")

    def test_the_default_filter_has_the_original_list_and_nothing_more(self):
        import platform
        base = {"x86_64": A["SYSCALLS_X86_64"], "aarch64": A["SYSCALLS_AARCH64"]}.get(platform.machine())
        if base is None: self.skipTest("no filter for this CPU")
        self.assertEqual(self.blocked(False), set(base))

    def test_harden_adds_io_uring_the_new_mount_calls_and_pidfd_getfd(self):
        import platform
        if platform.machine() not in A["SYSCALLS_EXTRA"]: self.skipTest("no filter for this CPU")
        extra = self.blocked(True) - self.blocked(False)
        for nr in (425, 426, 427,                        # io_uring_setup, _enter, _register
                   428, 429, 430, 431, 432, 433, 442,    # open_tree, move_mount, fsopen, fsconfig, fsmount, fspick, mount_setattr
                   438):                                 # pidfd_getfd
            self.assertIn(nr, extra, nr)
        self.assertTrue(self.blocked(False) < self.blocked(True))      # harden only ever adds
        self.assertTrue(extra.isdisjoint(self.blocked(False)))

    def test_socket_is_allowed_for_unix_inet_inet6_and_netlink_only(self):
        allow, deny = 0x7FFF0000, 0x00050001
        for machine, audit, other in (("x86_64", 0xC000003E, 0xC00000B7), ("aarch64", 0xC00000B7, 0xC000003E)):
            for harden in (False, True):
                with unittest.mock.patch.object(A["platform"], "machine", return_value=machine):
                    f = A["seccomp_file"](harden); data = f.read(); f.close()
                nr = A["SOCKET_NR"][machine]
                for fam in (1, 2, 10, 16):
                    self.assertEqual(self.run_bpf(data, nr, audit, fam), allow, f"{machine} family {fam}")
                for fam in (0, 3, 17, 21, 30, 31, 38, 40, 44, 255):    # AF_PACKET, RDS, TIPC, BLUETOOTH, ALG, VSOCK, XDP...
                    self.assertEqual(self.run_bpf(data, nr, audit, fam), deny, f"{machine} family {fam}")
                self.assertEqual(self.run_bpf(data, nr, audit, (1 << 32) | 38), deny, "only the low half of the argument counts, as in the kernel")
                self.assertEqual(self.run_bpf(data, nr, audit, (1 << 32) | 1), allow)
                self.assertEqual(self.run_bpf(data, nr, other, 1), deny, "the other ABI (32-bit) is refused")
                self.assertEqual(self.run_bpf(data, nr | 0x40000000, audit, 1), deny, "x32 is refused")
                self.assertEqual(self.run_bpf(data, 0, audit, 38), allow, "other calls ignore the first argument")
                base = {"x86_64": A["SYSCALLS_X86_64"], "aarch64": A["SYSCALLS_AARCH64"]}[machine]
                for blocked in base + (A["SYSCALLS_EXTRA"][machine] if harden else []):
                    self.assertEqual(self.run_bpf(data, blocked, audit, 1), deny, f"{machine} {blocked}")

    def test_i386_is_refused_unless_allow_32bit_is_on(self):
        with unittest.mock.patch.object(A["platform"], "machine", return_value="x86_64"):
            for args in ((False,), (True,), (False, False)):
                f = A["seccomp_file"](*args); data = f.read(); f.close()
                self.assertEqual(self.run_bpf(data, 0, A["I386_AUDIT"], 1), 0x00050001, args)

    def test_i386_is_filtered_like_the_native_abi_and_socketcall_is_allowed(self):
        allow, deny, audit = 0x7FFF0000, 0x00050001, A["I386_AUDIT"]
        for harden in (False, True):
            with unittest.mock.patch.object(A["platform"], "machine", return_value="x86_64"):
                f = A["seccomp_file"](harden, True); data = f.read(); f.close()
            blocked = A["SYSCALLS_I386"] + (A["SYSCALLS_I386_EXTRA"] if harden else [])
            for nr in blocked: self.assertEqual(self.run_bpf(data, nr, audit, 1), deny, nr)
            self.assertEqual(self.run_bpf(data, A["I386_SOCKETCALL_NR"], audit, 38), allow, "its arguments are in memory, so it is allowed whole")
            self.assertEqual(self.run_bpf(data, 359, 0xC000003E, 38), allow, "x86_64 359 is not socket")
            for nr in (0, 1, 3, 4, 5, 192, 435, 0x40000000): self.assertEqual(self.run_bpf(data, nr, audit, 1), allow, nr)
            if not harden: self.assertEqual(self.run_bpf(data, A["SYSCALLS_I386_EXTRA"][0], audit, 1), allow)
            sock = A["I386_SOCKET_NR"]
            for fam in (1, 2, 10, 16): self.assertEqual(self.run_bpf(data, sock, audit, fam), allow, fam)
            for fam in (0, 3, 17, 38, 40, 255): self.assertEqual(self.run_bpf(data, sock, audit, fam), deny, fam)
            self.assertEqual(self.run_bpf(data, sock, audit, (1 << 32) | 38), deny)
            self.assertEqual(self.run_bpf(data, 0, 0x40000001, 0), deny, "an unknown ABI is refused")
        with unittest.mock.patch.object(A["platform"], "machine", return_value="aarch64"):
            f = A["seccomp_file"](False, True); data = f.read(); f.close()
        self.assertEqual(self.run_bpf(data, 0, audit, 0), deny, "aarch64 does not accept i386")

    def test_i386_numbers_match_the_kernel_headers(self):
        path = "/usr/include/x86_64-linux-gnu/asm/unistd_32.h"
        if not os.path.exists(path): self.skipTest("no i386 kernel header (linux-libc-dev)")
        import re
        with open(path) as f: nrs = {m[1]: int(m[2]) for m in re.finditer(r"#define __NR_(\w+)\s+(\d+)\s*$", f.read(), re.M)}
        names = ("kexec_load perf_event_open bpf syslog uselib acct quotactl add_key keyctl request_key move_pages mbind get_mempolicy "
                 "set_mempolicy migrate_pages open_by_handle_at name_to_handle_at").split()
        extra = ("io_uring_setup io_uring_enter io_uring_register userfaultfd process_vm_readv process_vm_writev kcmp pidfd_getfd open_tree "
                 "move_mount fsopen fsconfig fsmount fspick mount_setattr").split()
        self.assertEqual(A["SYSCALLS_I386"], [nrs[n] for n in names])
        self.assertEqual(A["SYSCALLS_I386_EXTRA"], [nrs[n] for n in extra])
        self.assertEqual((A["I386_SOCKET_NR"], A["I386_SOCKETCALL_NR"]), (nrs["socket"], nrs["socketcall"]))

    def test_the_two_lists_are_the_same_length_on_both_cpus_apart_from_the_cpu_specific_calls(self):
        e = A["SYSCALLS_EXTRA"]
        self.assertEqual(len(e["x86_64"]), len(e["aarch64"]))
        self.assertEqual(len(set(e["x86_64"])), len(e["x86_64"])); self.assertEqual(len(set(e["aarch64"])), len(e["aarch64"]))


if __name__ == "__main__":
    unittest.main()
