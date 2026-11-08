#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Hostile-AppImage tests for ajail. Run as a normal user (ajail refuses root), from the folder holding ajail:
#     python3 test_hostile.py
# Needs bubblewrap and squashfs-tools; the tests are skipped if they are missing or sandboxes don't work here.
#
# ajail reads files from the AppImage OUTSIDE the sandbox (the app's .desktop file, its icon, the Name= used as
# identity) and keeps an unpacked copy on disk. Everything in an AppImage is attacker-controlled, so this builds
# images that try to make ajail copy, run or reveal something it shouldn't, and checks that nothing happens.
#
# Nothing touches your real home: the tests run a copy of ajail whose HOME is a temporary folder.
import json, os, re, shutil, stat, struct, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = "CANARY-7f3a91c2-do-not-copy"          # appears only in files that must never be copied or shown
UNSQ = "/usr/bin/unsquashfs"

# What every image runs as AppRun, inside the sandbox. It tries to reach the canaries through the app's own
# symlinks and by absolute path, and writes what it found to ~/probe-TAG.json (the app's private home).
APPRUN = r'''#!/usr/bin/python3
import json, os
TOKEN, SECRET, TAG = %(token)r[::-1], %(secret)r, %(tag)r
def read(p):
    try: return open(p, "rb").read(4096).decode("latin1")
    except OSError as e: return "ERR:" + (e.strerror or "")
rep = {"saw_canary": {}}
for p in ("/app/leak_abs", "/app/leak_rel", "/app/leak_dir/canary.txt", SECRET + "/canary.txt", SECRET + "/locked_file"):
    rep["saw_canary"][p] = TOKEN in read(p)
rep["home_entries"] = sorted(os.listdir(os.path.expanduser("~")))
try: open(os.path.expanduser("~/PWNED"), "w").write("x")
except OSError: pass
json.dump(rep, open(os.path.expanduser("~/probe-" + TAG + ".json"), "w"))
'''

PNG_HEAD = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 64, 64) + b"\x08\x06\0\0\0" + b"\0\0\0\0"


def tools_ok():
    if os.geteuid() == 0: return "ajail refuses to run as root; run this as a normal user"
    for t in ("bwrap", "mksquashfs"):
        if not shutil.which(t): return f"{t} is not installed"
    if not os.path.exists(UNSQ): return f"{UNSQ} is missing"
    r = subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib",
                        "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/bin", "/bin", "/usr/bin/true"],
                       capture_output=True)
    return None if r.returncode == 0 else "bubblewrap cannot create sandboxes here: " + r.stderr.decode()[:100]


class Base(unittest.TestCase):
    n = 0                                   # counts images across all test classes

    @classmethod
    def setUpClass(cls):
        why = tools_ok()
        if why: raise unittest.SkipTest(why)
        cls.T = os.path.realpath(tempfile.mkdtemp(prefix="ajail-hostile-"))
        os.chmod(cls.T, 0o700)
        cls.home, cls.rt = f"{cls.T}/home", f"{cls.T}/run"
        cls.secret, cls.outside = f"{cls.home}/secret", f"{cls.home}/outside_dir"
        for d in (cls.home, cls.rt, cls.secret, cls.outside, f"{cls.secret}/apps", f"{cls.home}/.config/ajail", f"{cls.T}/bin"):
            os.makedirs(d, exist_ok=True)
        os.chmod(cls.rt, 0o700)
        open(f"{cls.rt}/wayland-test", "w").close()                       # check_display only wants the file to exist
        open(f"{cls.home}/.config/ajail/default.conf", "w").write("net=0\nsound=0\ngpu=0\nportals=0\ntheme=0\n")
        w = lambda p, data, mode=0o600: (open(p, "wb").write(data), os.chmod(p, mode))
        w(f"{cls.secret}/canary.txt", TOKEN.encode())
        w(f"{cls.secret}/canary.png", PNG_HEAD + TOKEN.encode())
        w(f"{cls.secret}/apps/evil.png", PNG_HEAD + TOKEN.encode())
        w(f"{cls.secret}/canary.desktop", f"[Desktop Entry]\nName=CANARYNAME {TOKEN}\nIcon=evil\n".encode())
        w(f"{cls.secret}/locked_file", TOKEN.encode(), 0o400)
        # a copy of ajail that lives and works entirely inside the temporary folder
        src = open(f"{HERE}/ajail").read()
        for old, new in (('HOME    = os.path.realpath(_pw.pw_dir)', 'HOME    = os.environ["AJAIL_TEST_HOME"]'),
                         ('SELF    = "/usr/local/bin/ajail"', 'SELF    = os.environ["AJAIL_TEST_SELF"]')):
            assert old in src, f"test harness is out of date: {old!r} not found in ajail"
            src = src.replace(old, new, 1)
        cls.ajail_path = f"{cls.T}/bin/ajail"
        open(cls.ajail_path, "w").write(src)
        cls.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": cls.home,
                   "AJAIL_TEST_HOME": cls.home, "AJAIL_TEST_SELF": cls.ajail_path,
                   "XDG_RUNTIME_DIR": cls.rt, "WAYLAND_DISPLAY": "wayland-test"}
        cls.before = cls.snapshot_secrets()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "T", None):
            subprocess.run(["chmod", "-R", "u+rwX", cls.T], capture_output=True)
            shutil.rmtree(cls.T, ignore_errors=True)

    # ---- helpers ----
    @classmethod
    def snapshot_secrets(cls):
        snap = {}
        for d, _, fs in os.walk(cls.secret):
            for f in fs:
                p = f"{d}/{f}"; st = os.lstat(p)
                snap[p] = (stat.S_IMODE(st.st_mode), st.st_mtime_ns, open(p, "rb").read())
        snap["outside_dir"] = sorted(os.listdir(cls.outside))
        return snap

    def ajail(self, *args, timeout=90):
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, self.ajail_path, *args], capture_output=True, text=True,
                               errors="replace", timeout=timeout, env=self.env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            self.fail(f"ajail {' '.join(args)} hung (more than {timeout}s)")
        self.assertNotIn("Traceback", r.stdout + r.stderr, f"ajail crashed on: {' '.join(args)}\n{r.stderr}")
        r.seconds = time.time() - t0
        return r

    def image(self, name, files=None, desktop="default", apprun=True, tag=None, pseudo=()):
        """Build NAME.AppImage. `files(appdir)` adds the hostile content. Returns its path."""
        Base.n += 1
        tag = tag or f"t{Base.n}"
        d = f"{self.T}/img{Base.n}"; ad = f"{d}/AppDir"
        os.makedirs(ad)
        if apprun:
            open(f"{ad}/AppRun", "w").write(APPRUN % {"token": TOKEN[::-1], "secret": self.secret, "tag": tag})
            os.chmod(f"{ad}/AppRun", 0o755)
        if desktop == "default":
            desktop = f"[Desktop Entry]\nType=Application\nName={name} App\nIcon={name}\nCategories=Utility;\n"
        if desktop is not None: open(f"{ad}/{name}.desktop", "w").write(desktop)
        if files: files(ad)
        pf = f"{d}/pseudo"
        open(pf, "w").write("".join(pseudo))
        out = f"{d}/{name}.AppImage"
        subprocess.run(["mksquashfs", ad, f"{d}/fs.sqfs", "-noappend", "-no-progress", "-quiet", "-root-owned",
                        "-no-xattrs", "-pf", pf], check=True, capture_output=True)
        h = bytearray(64)                                         # same minimal ELF header as make-selftest
        h[0:4] = b"\x7fELF"; h[4] = 2; h[5] = 1; h[6] = 1; h[8:11] = b"AI\x02"
        struct.pack_into("<HHIQQQIHHHHHH", h, 16, 2, 62, 1, 0, 0, 64, 0, 64, 0, 0, 64, 0, 0)
        with open(out, "wb") as f: f.write(h); f.write(open(f"{d}/fs.sqfs", "rb").read())
        os.chmod(out, 0o755)
        out_tag = tag; self.tags = getattr(self, "tags", {}); self.tags[out] = out_tag
        return out

    # where ajail puts things (all under the temporary home)
    def p(self, rel): return f"{self.home}/{rel}"
    def launcher(self, name): return self.p(f".local/share/applications/ajail-{name}.desktop")
    def installed(self, name): return self.p(f".local/share/ajail/apps/{name}")
    def private_home(self, name): return self.p(f".appimage-homes/{name}")
    def extracted(self, name): return self.p(f".local/share/ajail/extracted/{name}")

    def assert_no_copy_or_leak(self):
        """No regular file ajail wrote may contain the canary or /etc/passwd, and the canaries must be untouched."""
        bad = []
        for top in (".local/share/icons", ".local/share/applications", ".local/share/ajail", ".config/ajail", ".local/state/ajail"):
            for d, _, fs in os.walk(self.p(top)):
                for f in fs:
                    path = f"{d}/{f}"
                    if os.path.islink(path) or not os.path.isfile(path): continue
                    if os.path.getsize(path) > 20_000_000: continue
                    data = open(path, "rb").read()
                    if TOKEN.encode() in data or b"CANARYNAME" in data or b"root:x:0:0" in data: bad.append(path)
        self.assertEqual(bad, [], "files that were copied from outside the AppImage")
        self.assertEqual(self.snapshot_secrets(), self.before, "something outside the AppImage was changed")
        self.assertFalse(os.path.exists(self.p("PWNED")), "an AppImage file ran outside the sandbox")

    def assert_launcher_clean(self, name):
        text = open(self.launcher(name), encoding="utf-8").read()
        self.assertLess(len(text), 8192, "launcher is oversized")
        for ch in text:
            self.assertFalse((ord(ch) < 32 and ch != "\n") or 0x7f <= ord(ch) <= 0x9f or ch in "  ",
                             f"control character {ch!r} in the launcher")
        lines = text.splitlines()
        self.assertEqual(lines[0], "[Desktop Entry]")
        self.assertEqual([l for l in lines if l.startswith("[")], ["[Desktop Entry]"])
        keys = [l.partition("=")[0] for l in lines[1:]]
        allowed = re.compile(r"(Type|Terminal|Exec|X-AJAIL-App|Icon|Name|GenericName|Comment|Keywords|Categories|MimeType|StartupWMClass)(\[[A-Za-z0-9_@.-]+\])?")
        for k in keys: self.assertTrue(allowed.fullmatch(k), f"unexpected key {k!r} in the launcher")
        self.assertEqual([l for l in lines if l.startswith("Exec")], [f"Exec={self.ajail_path} run {name} %U"])
        self.assertNotIn("TryExec", keys)
        rest = text.replace(name, "")                      # the sanitized name itself may spell anything
        self.assertNotIn("PWNED", rest); self.assertNotIn("evil", rest.lower())
        return text

    def probe(self, name, tag):
        with open(f"{self.private_home(name)}/probe-{tag}.json") as f: return json.load(f)

    def assert_probe_contained(self, name, tag):
        rep = self.probe(name, tag)
        self.assertEqual([p for p, saw in rep["saw_canary"].items() if saw], [], "the running app could read a canary")
        self.assertNotIn("secret", rep["home_entries"]); self.assertNotIn("outside_dir", rep["home_entries"])

    def install_and_run(self, name, img):
        r = self.ajail("install", img); self.assertEqual(r.returncode, 0, r.stderr)
        r = self.ajail("run", name); self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_probe_contained(name, self.tags[img])


class Symlinks(Base):
    def test_symlinks_pointing_outside_are_never_followed(self):
        def files(ad):
            os.symlink(f"{self.secret}/canary.png", f"{ad}/.DirIcon")                     # icon: absolute link to a valid PNG
            os.symlink(f"{self.secret}/canary.png", f"{ad}/Linky.png")
            os.symlink("/etc/passwd", f"{ad}/Linky.svg")
            os.symlink(f"{self.secret}/canary.png", f"{ad}/Linky.xpm")
            os.symlink("../../../../../../../../etc/passwd", f"{ad}/rel.png")             # relative escape
            os.makedirs(f"{ad}/usr/share/icons/hicolor")
            os.symlink(self.secret, f"{ad}/usr/share/icons/hicolor/64x64")                # a whole icon folder, linked out
            os.symlink(f"{self.secret}/canary.txt", f"{ad}/leak_abs")
            os.symlink("../../../../../../../../../../" + self.secret.lstrip("/") + "/canary.txt", f"{ad}/leak_rel")
            os.symlink(self.secret, f"{ad}/leak_dir")
            os.symlink(self.outside, f"{ad}/out")                                          # for chmod -R / rm -r to try
            os.symlink(f"{self.secret}/locked_file", f"{ad}/chmod_victim")
            os.symlink(f"{self.secret}/canary.desktop", f"{ad}/a-first.desktop")          # sorts before the real desktop file
        img = self.image("Linky", files, desktop="[Desktop Entry]\nName=Linky App\nIcon=evil\n")
        self.install_and_run("Linky", img)
        self.assert_launcher_clean("Linky")
        self.assertEqual(os.listdir(self.p(".local/share/icons/hicolor")) if os.path.isdir(self.p(".local/share/icons/hicolor")) else [], [],
                         "an icon was copied although every candidate points outside the app")
        r = self.ajail("uninstall", "Linky", "--purge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_no_copy_or_leak()
        self.assertFalse(os.path.exists(self.installed("Linky")))

    def test_run_once_with_links_leaves_nothing(self):
        def files(ad):
            os.symlink(self.secret, f"{ad}/leak_dir"); os.symlink(f"{self.secret}/canary.txt", f"{ad}/leak_abs")
            os.symlink(self.outside, f"{ad}/out")
        img = self.image("Once", files)
        r = self.ajail("run-file", "--once", img); self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_probe_contained("Once", self.tags[img])
        self.assertFalse(os.path.exists(self.extracted("Once")), "run once left the unpacked copy behind")
        self.assert_no_copy_or_leak()

    def test_loops_do_not_hang_or_crash(self):
        def files(ad):
            os.symlink("b.png", f"{ad}/Loop.png"); os.symlink("Loop.png", f"{ad}/b.png")
            os.symlink(".DirIcon", f"{ad}/.DirIcon")
            os.symlink("loop.desktop", f"{ad}/loop.desktop")
            os.symlink("Loop.svg", f"{ad}/Loop.svg")
            os.makedirs(f"{ad}/usr/share/icons/hicolor/64x64")
            os.symlink("../64x64", f"{ad}/usr/share/icons/hicolor/64x64/apps")              # directory loop
            os.symlink(".", f"{ad}/self")
        img = self.image("Loop", files, desktop=None)
        r = self.ajail("install", img, timeout=60); self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_launcher_clean("Loop")
        r = self.ajail("run", "Loop"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ajail("uninstall", "Loop").returncode, 0)

    def test_apprun_that_is_a_loop_fails_cleanly(self):
        def files(ad): os.symlink("AppRun", f"{ad}/AppRun")
        img = self.image("Spin", files, apprun=False)
        self.ajail("install", img, timeout=60)               # either result is fine as long as nothing hangs or crashes
        r = self.ajail("run-file", "--once", img, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assert_no_copy_or_leak()


class Desktop(Base):
    def test_desktop_file_cannot_inject_keys_or_commands(self):
        text = ("[Desktop Entry]\nType=Application\nName=Inject\n"
                "Name[de]=Inj\rExec=touch ${HOME}/PWNED\n"                      # CR splits the line
                "Exec=sh -c 'touch $HOME/PWNED'\nTryExec=/usr/bin/id\nPath=/\n"
                "Comment=hello\x85Exec=evil\n"                                   # NEL, U+2028 and friends split lines too
                "Keywords=a;b Exec=evil2\n"
                "GenericName=gen\x0bExec=evil3\n"
                "Categories=Utility;\nIcon=../../../../etc/passwd\nX-Evil=1\nActions=A;\nMimeType=text/plain;\n"
                "Comment[fr]=ctrl\x01char\nComment[es]=del\x7fchar\nComment[it]=c1\x9bchar\n"
                "[Desktop Action A]\nName=A\nExec=touch $HOME/PWNED2\n")
        img = self.image("Inject", desktop=text)
        self.install_and_run("Inject", img)
        launcher = self.assert_launcher_clean("Inject")
        self.assertIn("Name=Inject", launcher)
        self.assert_no_copy_or_leak()

    def test_icon_names_cannot_walk_out(self):
        for i, icon in enumerate(("/etc/passwd", "../../../../etc/passwd", "a/../../b", "x*", "x?y", "[a-z]", "..", "a b", "$(id)")):
            img = self.image("Icony", desktop=f"[Desktop Entry]\nName=Icony\nIcon={icon}\n", tag=f"i{i}")
            r = self.ajail("install", img); self.assertEqual(r.returncode, 0, f"{icon!r}: {r.stderr}")
            self.assert_launcher_clean("Icony")
            self.assertEqual(self.ajail("uninstall", "Icony").returncode, 0)
        self.assert_no_copy_or_leak()

    def test_file_names_cannot_inject(self):
        img = self.image("tmp", desktop="[Desktop Entry]\nName=Odd\n")
        for i, odd in enumerate(("evil name';touch PWNED;#-1.2-x86_64.AppImage", "a\nExec=x.AppImage", "..hidden.AppImage",
                                 "$(id)`id`.AppImage", "ünï.AppImage", "-rf.AppImage", "x" * 240 + ".AppImage")):
            dst = f"{self.T}/odd{i}/{odd}"; os.makedirs(os.path.dirname(dst)); shutil.copy(img, dst)
            name = self.ajail("name", dst).stdout.strip()
            self.assertRegex(name, r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", odd)
            r = self.ajail("install", dst); self.assertEqual(r.returncode, 0, f"{odd!r}: {r.stderr}")
            self.assert_launcher_clean(name)
            self.ajail("uninstall", name)
        self.assert_no_copy_or_leak()

    def test_fifos_do_not_hang(self):
        def files(ad):
            os.mkfifo(f"{ad}/.DirIcon"); os.mkfifo(f"{ad}/Fifo.png"); os.mkfifo(f"{ad}/a-fifo.desktop")
        img = self.image("Fifo", files, desktop=None)
        r = self.ajail("install", img, timeout=60); self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_launcher_clean("Fifo")


class Sizes(Base):
    def test_huge_icons_and_desktop_files_are_bounded(self):
        def files(ad):
            open(f"{ad}/.DirIcon", "wb").write(PNG_HEAD + b"\0" * 6_000_000)                    # valid header, over the 5 MB limit
            open(f"{ad}/Big.png", "wb").write(PNG_HEAD[:16] + struct.pack(">I", 100000) + PNG_HEAD[20:] + b"\0" * 1000)   # absurd width
            open(f"{ad}/Big.svg", "wb").write(b"<svg>" + b" " * 6_000_000)
            open(f"{ad}/a-huge.desktop", "w").write("[Desktop Entry]\nName=" + "A" * 70_000 + "\nComment=" + "B" * 5_000_000 + "\n")
        img = self.image("Big", files, desktop=None)
        r = self.ajail("install", img, timeout=60); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(r.seconds, 30)
        for d, _, fs in os.walk(self.p(".local/share/icons")):
            for f in fs: self.assertLess(os.path.getsize(f"{d}/{f}"), 5_000_000, f"{f}: oversized icon copied")
        self.assert_launcher_clean("Big")
        ids = self.p(".config/ajail/ids/Big")
        if os.path.exists(ids): self.assertLess(os.path.getsize(ids), 4096, "an oversized Name= was stored as the app identity")


class Permissions(Base):
    def test_unreadable_folders_are_installed_and_removed(self):
        def files(ad):
            os.makedirs(f"{ad}/locked/inner"); open(f"{ad}/locked/inner/f", "w").write("x"); open(f"{ad}/locked/g", "w").write("x")
        img = self.image("Locked", files, pseudo=("locked m 000 0 0\n", "locked/inner m 000 0 0\n", "locked/g m 000 0 0\n"))
        self.install_and_run("Locked", img)
        r = self.ajail("uninstall", "Locked"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.installed("Locked")), "uninstall left files behind")
        r = self.ajail("run-file", "--once", img); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.extracted("Locked")), "run once left files behind")
        self.assert_no_copy_or_leak()


class Identity(Base):
    """The private home is keyed by file name. An AppImage that merely has the same name as an installed app must
    not inherit its data, whether it lies about its Name=, hides behind a link, or has no .desktop file at all."""
    def setUp(self):
        Base.n += 1
        self.legit = self.image("Vault", desktop="[Desktop Entry]\nName=Vault App\n", tag="legit")
        r = self.ajail("install", self.legit); self.assertEqual(r.returncode, 0, r.stderr)
        self.stamp = open(f"{self.installed('Vault')}/stamp").read()

    def tearDown(self):
        self.ajail("uninstall", "Vault", "--purge")
        for f in (self.p(".config/ajail/ids/Vault"),):
            if os.path.exists(f): os.unlink(f)

    def impostor_refused(self, img):
        for args in (("install", img), ("run-file", "--once", img)):
            r = self.ajail(*args)
            self.assertNotEqual(r.returncode, 0, f"{args[0]}: the impostor was accepted")
        self.assertEqual(open(f"{self.installed('Vault')}/stamp").read(), self.stamp, "the installed app was replaced")
        self.assertFalse(os.path.exists(f"{self.private_home('Vault')}/probe-{self.tags[img]}.json"),
                         "the impostor ran with the real app's private home")

    def test_different_name_is_refused(self):
        self.impostor_refused(self.image("Vault", desktop="[Desktop Entry]\nName=Something Else\n", tag="imp1"))

    def test_only_a_link_instead_of_a_desktop_file_is_refused(self):
        def files(ad): os.symlink(f"{self.secret}/canary.desktop", f"{ad}/a.desktop")
        self.impostor_refused(self.image("Vault", files, desktop=None, tag="imp2"))

    def test_no_desktop_file_at_all_is_refused(self):
        self.impostor_refused(self.image("Vault", desktop=None, tag="imp3"))

    def test_name_with_line_breaks_is_stable(self):
        img = self.image("Weird", desktop="[Desktop Entry]\nName=Wei rd\rApp\x85!\nIcon=x\n", tag="w1")
        self.assertEqual(self.ajail("install", img).returncode, 0)
        self.assertEqual(self.ajail("run", "Weird").returncode, 0)                  # the stored identity still matches
        self.assertEqual(self.ajail("run-file", "--once", img).returncode, 0)
        self.assertEqual(self.ajail("install", img).returncode, 0)                  # and a reinstall is not seen as an impostor
        self.ajail("uninstall", "Weird", "--purge")


if __name__ == "__main__":
    unittest.main(verbosity=2)
