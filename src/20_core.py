# ======== 20_core: SECURITY CORE: everything that decides what the sandbox can touch. Read this file line by line.

def safe_path(p):
    """A file or folder an app may be given: non-hidden inside $HOME, or on a mounted drive."""
    try:
        p = os.path.realpath(p)
    except Exception:
        return None
    if "\n" in p or not os.path.exists(p):
        return None
    if p.startswith(HOME + "/"):
        return None if p[len(HOME) + 1:].split("/")[0].startswith(".") else p
    return p if any(p.startswith(b) for b in OUTSIDE) else None

def xdg_dir(kind):
    path = None
    try:
        for ln in open(f"{HOME}/.config/user-dirs.dirs"):
            m = re.match(rf'XDG_{kind.upper()}_DIR="(.*)"', ln.strip())
            if m: path = m.group(1).replace("$HOME", HOME)
    except OSError:
        pass
    return path or f"{HOME}/{XDG_DEFAULT[kind]}"

def empty_cfg():
    return {"s": {}, **{k: [] for k in LISTS}}

COMMENT = re.compile(r"(^|\s)#.*$")     # '#' starts a comment at the start of a line or after whitespace
TALK_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*(\.\*)?")     # no empty parts: "org..x" is not a name

def talk_ok(n):
    """A D-Bus name for talk=: a plain name, or NAME.* with at least three parts before the .* . So 'org.*' and
    'org.freedesktop.*' (which would reach secrets, systemd and more) are refused; 'org.example.App.*' is fine."""
    return bool(TALK_RE.fullmatch(n)) and (not n.endswith(".*") or n.count(".") >= 3)

def parse_cfg(path):
    """Read one settings file. Whitespace around keys and values is ignored, and '#' starts a comment (so
    'net = 0  # no network' works). A line that can't be used is skipped with a warning (terminal and log),
    never silently, so a typo can't leave an app with more access than you meant."""
    try:
        with open(path) as f: lines = f.read().splitlines()
    except OSError:
        return None
    d = empty_cfg()
    for no, raw in enumerate(lines, 1):
        ln = COMMENT.sub("", raw).strip()
        if not ln: continue
        def bad(why):
            warn(f"{path}:{no}: ignoring '{ln[:60]}': {why}")
        if "=" not in ln:
            bad("expected key=value"); continue
        k, _, v = (x.strip() for x in ln.partition("="))
        if k == "portal":
            # "portal=NAME" turns a portal on; "portal=-NAME" (in an app's file) turns off one the defaults turned on
            if v and v.lstrip("-") in EXTRA_PORTALS and v.count("-") <= 1: d[k].append(v)
            else: bad(f"unknown portal '{v}'")
        elif k in LISTS:
            if not v: bad("empty value")
            elif k == "talk" and not talk_ok(v): bad("not a usable D-Bus name (a wildcard needs three parts first, like org.example.App.*)")
            else: d[k].append(v)
        elif k in DEFAULTS:
            if v in ("0", "1"): d["s"][k] = int(v)
            else: bad("expected 0 or 1")
        elif k in NUM:
            if v.isdigit(): d["s"][k] = min(int(v), 10**7)
            else: bad("expected a whole number")
        elif k == "profile":
            if v == "strict": d["s"].update(net=0, sound=0)
            else: bad("only 'strict' is known")
        elif k == "flag":
            if v in LEGACY_FLAGS: d["s"][LEGACY_FLAGS[v][0]] = LEGACY_FLAGS[v][1]
            else: bad("unknown flag")
        else:
            bad("unknown setting")
    return d

def load_cfg(name):
    """Built-in defaults < default.conf < apps/NAME.conf. Lists add up, except xdg (per folder)."""
    g = parse_cfg(f"{CONF}/default.conf") or empty_cfg()
    a = parse_cfg(f"{CONF}/apps/{name}.conf") or empty_cfg()
    cfg = {**DEFAULTS, **NUM, **g["s"], **a["s"]}
    for k in ("allow", "allow_ro", "env", "talk"):
        cfg[k] = list(dict.fromkeys(g[k] + a[k]))
    on = []
    for p in g["portal"] + a["portal"]:          # in order, so an app's "-NAME" undoes the default's NAME
        if p.startswith("-"):
            if p[1:] in on: on.remove(p[1:])
        elif p not in on: on.append(p)
    cfg["portal"] = on
    xd = {}
    for x in g["xdg"] + a["xdg"]:
        k, _, m = x.partition(":"); xd[k] = m
    cfg["xdg"] = [f"{k}:{m}" if m else k for k, m in xd.items()]
    return cfg

def share_list(cfg):
    out = [("--bind", p) for p in cfg["allow"]] + [("--ro-bind", p) for p in cfg["allow_ro"]]
    for x in cfg["xdg"]:
        kind, _, mode = x.partition(":")
        if kind in XDG_DEFAULT and mode != "none":
            out.append(("--ro-bind" if mode == "ro" else "--bind", xdg_dir(kind)))
    return out

def compat_env(environ):
    """The variables 'compat_env' lets through: those of COMPAT_ENV and anything starting with a COMPAT_ENV_PREFIXES
    entry, but only if set. Names are matched exactly (case-sensitive), so 'mesa_x' or 'XMESA_X' do not qualify.
    Never anything else, whatever the value looks like."""
    return {k: v for k, v in environ.items() if k in COMPAT_ENV or k.startswith(COMPAT_ENV_PREFIXES)}

def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"

def wayland_path():
    wl = os.environ.get("WAYLAND_DISPLAY", "")
    if not wl: return None
    p = wl if wl.startswith("/") else f"{runtime_dir()}/{wl}"
    return p if os.path.exists(p) else None

_has = {}
def bwrap_has(opt):
    if opt not in _has:
        try:
            r = subprocess.run(["bwrap", "--help"], capture_output=True, text=True)
            _has[opt] = opt in (r.stdout + r.stderr)
        except Exception:
            _has[opt] = False
    return _has[opt]

BASE = ["--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin"]

def seccomp_file(harden=False):
    """Syscall blocklist (like Flatpak's), generated as a raw BPF program. No extra packages needed."""
    arch = {"x86_64": (0xC000003E, SYSCALLS_X86_64), "aarch64": (0xC00000B7, SYSCALLS_AARCH64)}.get(platform.machine())
    if not arch:
        return None
    audit, nrs = arch
    if harden: nrs = nrs + SYSCALLS_EXTRA[platform.machine()]
    deny = 0x00050000 | errno.EPERM
    ins = [(0x20, 0, 0, 4),                    # load arch
           (0x15, 1, 0, audit),                # native arch? skip the next instruction
           (0x06, 0, 0, deny),                 # other ABI (32-bit/x32): deny
           (0x20, 0, 0, 0)]                    # load syscall number
    n = len(nrs)
    # x32 calls share the x86_64 audit arch and only differ by bit 30 of the number: deny them all (skips the list + allow)
    ins += [(0x35, n + 1, 0, 0x40000000)]                       # number >= 0x40000000: jump to deny
    ins += [(0x15, n - i, 0, nr) for i, nr in enumerate(nrs)]   # listed syscall: jump to deny
    ins += [(0x06, 0, 0, 0x7FFF0000), (0x06, 0, 0, deny)]       # allow, deny
    try:
        tf = tempfile.TemporaryFile()
        tf.write(b"".join(struct.pack("=HBBI", *i) for i in ins)); tf.flush(); tf.seek(0)
        return tf
    except OSError as e:
        log(f"seccomp disabled: {e}")
        return None

def want_bus(cfg):
    return bool(cfg["portals"] or cfg["tray"] or cfg["talk"])

# Desktop portals an app may call when "Notifications and portals" is on. xdg-dbus-proxy runs on the host, so the
# desktop sees the app as an ordinary unsandboxed program: a portal that is not listed here is simply unreachable.
# Off unless asked for with portal=NAME (EXTRA_PORTALS): Location, GlobalShortcuts, RemoteDesktop (input injection),
# InputCapture, Wallpaper, DynamicLauncher, Secret. Never reachable: Documents and anything else not listed.
# Camera follows the Camera permission; Screenshot and ScreenCast (both show the user a prompt) have their own switches.
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_BASIC = ("Settings", "OpenURI", "FileChooser", "Notification", "Print", "Inhibit", "Background", "Account",
                "Email", "Trash", "NetworkMonitor", "ProxyResolver", "GameMode", "MemoryMonitor",
                "PowerProfileMonitor", "Realtime")

def extra_portals(cfg):
    """The opt-in portals enabled for this app: allowlisted names only, no duplicates."""
    return [p for p in dict.fromkeys(cfg["portal"]) if p in EXTRA_PORTALS]

def portal_rules(cfg):
    """xdg-dbus-proxy rules for Notifications and the allowed portals (see PORTAL_BASIC above)."""
    ifaces = list(PORTAL_BASIC)
    if cfg["camera"]: ifaces.append("Camera")
    if cfg["portal_screenshot"]: ifaces.append("Screenshot")
    if cfg["portal_screencast"]: ifaces.append("ScreenCast")
    ifaces += [p for p in extra_portals(cfg) if p not in ifaces]
    rules = ["--talk=org.freedesktop.Notifications", f"--see={PORTAL_NAME}"]
    rules += [f"--call={PORTAL_NAME}=org.freedesktop.portal.{i}.*" for i in ifaces]
    rules += [f"--call={PORTAL_NAME}=org.freedesktop.portal.Request.*@/org/freedesktop/portal/desktop/request/*",
              f"--call={PORTAL_NAME}=org.freedesktop.portal.Session.*@/org/freedesktop/portal/desktop/session/*",
              f"--call={PORTAL_NAME}=org.freedesktop.DBus.Properties.*@/org/freedesktop/portal/desktop",
              "--broadcast=org.freedesktop.portal.*=@/org/freedesktop/portal/*"]
    return rules

# Tray icons (StatusNotifierItem) own a name like org.kde.StatusNotifierItem-PID-N. xdg-dbus-proxy only takes a wildcard at
# the end after a dot ("org.kde.*"), so rather than allowing every org.kde name, the likely names are listed. The PID is the
# one inside the sandbox (it has its own PID namespace), so it is small. An app whose main process has a higher PID gets no
# tray icon: the self-test shows this, and the range can be raised here.
TRAY_PIDS = range(2, 65)
TRAY_IDS = range(1, 4)

def tray_rules():
    return ["--talk=org.kde.StatusNotifierWatcher"] + [
        f"--own=org.kde.StatusNotifierItem-{p}-{i}" for p in TRAY_PIDS for i in TRAY_IDS]

def start_bus(cfg, name):
    if not want_bus(cfg): return None
    px = shutil.which("xdg-dbus-proxy")
    if not px:
        warn("xdg-dbus-proxy missing, so notifications/portals are off. Install: " + install_hint("xdg-dbus-proxy"), name)
        return None
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime_dir()}/bus"
    d = f"{runtime_dir()}/ajail"
    os.makedirs(d, mode=0o700, exist_ok=True)
    sock = f"{d}/{os.getpid()}.bus"
    rules = ["--filter"]
    if cfg["portals"]:
        rules += portal_rules(cfg)
    if cfg["tray"]:
        rules += tray_rules() + ["--own=org.mpris.MediaPlayer2.*"]
    rules += [f"--talk={n}" for n in cfg["talk"] if talk_ok(n)]
    r, w = os.pipe()
    proc = subprocess.Popen([px, f"--fd={w}", addr, sock] + rules, pass_fds=[w],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    ok = False
    try:
        ok = bool(select.select([r], [], [], 3)[0]) and bool(os.read(r, 1))
    except OSError:
        pass
    b = (proc, r, sock)
    if not ok:
        stop_bus(b)
        print("ajail: could not start xdg-dbus-proxy; running without D-Bus", file=sys.stderr)
        log("xdg-dbus-proxy failed to start", name)
        return None
    return b

def stop_bus(b):
    if not b: return
    proc, r, sock = b
    try: os.close(r)
    except OSError: pass
    try: proc.terminate(); proc.wait(2)
    except Exception: pass
    try: os.unlink(sock)
    except OSError: pass

def private_net(cfg):
    """'Keep localhost private': the app gets its own network (via pasta) that reaches the internet but not this computer."""
    return bool(cfg["net"] and cfg["net_private"])

# pasta gives the sandbox its own network namespace. Nothing is forwarded in either direction, the gateway address is
# not mapped to this computer (--no-map-gw), and DNS goes through a forwarder address that pasta answers itself.
DNS_FWD = "169.254.1.1"
PASTA = ["pasta", "--quiet", "--config-net", "--no-map-gw", "--dns-forward", DNS_FWD,
         "-t", "none", "-u", "none", "-T", "none", "-U", "none", "--"]
SEC_FD = 9      # pasta closes inherited descriptors, so the seccomp filter is re-opened from a file inside it

def net_wrap(inner, secpath=None):
    if secpath:
        return PASTA + ["/bin/sh", "-c", f'exec {SEC_FD}<"$1"; shift; exec "$@"', "sh", secpath] + inner
    return PASTA + inner

def work_dir():
    return f"{runtime_dir()}/ajail/{os.getpid()}.d"

def machine_id(name):
    """Stable random machine-id per app (D-Bus and SDL want one; the real one would let apps fingerprint you)."""
    p = f"{CONF}/apps/{name}.machine-id"
    try:
        v = open(p).read().strip()
        if len(v) == 32: return v + "\n"
    except OSError: pass
    import uuid
    v = uuid.uuid4().hex
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(v + "\n")
    except OSError: pass
    return v + "\n"

def make_files(cfg, d, name="app"):
    """Files bound over /etc for this run: minimal passwd/group/hosts (privacy) and a resolv.conf for private net."""
    out = {}
    os.makedirs(d, mode=0o700, exist_ok=True)
    def put(n, text):
        with open(f"{d}/{n}", "w") as f: f.write(text)
        out[n] = f"{d}/{n}"
    if cfg["privacy"]:
        gid = os.getgid()
        try: gname = grp.getgrgid(gid).gr_name
        except KeyError: gname = USER
        put("passwd", f"root:x:0:0:root:/root:/usr/sbin/nologin\n{USER}:x:{os.getuid()}:{gid}::{HOME}:"
                      f"{_pw.pw_shell or '/bin/sh'}\nnobody:x:65534:65534:Nobody:/:/usr/sbin/nologin\n")
        put("group", f"root:x:0:\n{gname}:x:{gid}:\nnogroup:x:65534:\n")
        put("hosts", "127.0.0.1 localhost\n::1 localhost\n")
    if private_net(cfg):
        put("resolv.conf", f"nameserver {DNS_FWD}\n")
    put("machine-id", machine_id(name))
    return out

def preview_files(cfg):
    d = f"{runtime_dir()}/ajail/PID.d"
    n = (["passwd", "group", "hosts"] if cfg["privacy"] else []) + (["resolv.conf"] if private_net(cfg) else []) + ["machine-id"]
    return {x: f"{d}/{x}" for x in n}

def scoped_xauth(src, disp):
    """A copy of the XAUTHORITY file holding only the cookie for DISPLAY, or None (then it is not shared at all)."""
    # Done in Python (no xauth needed, and many Wayland desktops don't install it). Entries are kept when they are
    # for this display number or have no display number (wildcard entries, as Xwayland's own auth file uses).
    d = work_dir()
    if not os.path.isdir(d):
        return None
    num = disp[1:].split(".")[0].encode()
    try:
        with open(src, "rb") as f: data = f.read(1_000_000)
    except OSError:
        return None
    keep, i = [], 0
    while i + 2 <= len(data):
        start = i
        i += 2                                    # family
        fields = []
        for _ in range(4):                        # address, display number, auth name, auth data
            if i + 2 > len(data): return None     # truncated or not an Xauthority file: not shared
            n = int.from_bytes(data[i:i+2], "big"); i += 2
            fields.append(data[i:i+n]); i += n
        if i > len(data): return None
        if fields[1] in (num, b""): keep.append(data[start:i])
    if not keep:
        return None
    dst = f"{d}/Xauthority"
    try:
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f: f.write(b"".join(keep))
    except OSError:
        return None
    return dst

def bwrap_args(name, root, cfg, inner, bus=None, sec=None, files=(), gen=None, gset=None):
    rt = runtime_dir()
    a = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    if not cfg["userns"]:
        if bwrap_has("--disable-userns"): a.append("--disable-userns")
        else: warn("this bubblewrap is too old for --disable-userns; ignoring", name)
    if cfg["privacy"]:
        if bwrap_has("--hostname"): a += ["--hostname", "ajail"]
        else: warn("this bubblewrap is too old for --hostname; the computer name stays visible", name)
    if private_net(cfg):
        a += ["--uid", str(os.getuid()), "--gid", str(os.getgid())]     # keep your own ids inside pasta's namespace
    if cfg["net"]: a.append("--share-net")
    if sec is not None: a += ["--seccomp", str(sec)]
    a += BASE + ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run",
                 "--dir", "/var", "--symlink", "../tmp", "/var/tmp", "--dir", "/var/lib/dbus",
                 "--symlink", "/etc/machine-id", "/var/lib/dbus/machine-id", "--perms", "0700", "--dir", rt]
    gen = gen or {}
    for p in ETC:
        if p not in gen: a += ["--ro-bind-try", f"/etc/{p}", f"/etc/{p}"]
    for n, src in gen.items():
        a += ["--ro-bind", src, f"/etc/{n}"]
    env = {"HOME": HOME, "USER": USER, "LOGNAME": USER, "PATH": "/usr/local/bin:/usr/bin:/usr/sbin",
           "XDG_RUNTIME_DIR": rt, "APPDIR": "/app", "AJAIL_ACTIVE": "1",
           "ELECTRON_OZONE_PLATFORM_HINT": "auto" if cfg["x11"] else "wayland"}
    for k in ("LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_TIME", "TZ", "TERM",
              "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XCURSOR_THEME", "XCURSOR_SIZE"):
        if k in os.environ: env[k] = os.environ[k]
    wp = wayland_path()
    if wp:
        a += ["--ro-bind", wp, wp]
        env["WAYLAND_DISPLAY"] = os.environ["WAYLAND_DISPLAY"]; env["XDG_SESSION_TYPE"] = "wayland"
    if cfg["x11"]:
        disp = os.environ.get("DISPLAY", "")
        if disp.startswith(":"):
            sock = f"/tmp/.X11-unix/X{disp[1:].split('.')[0]}"
            a += ["--ro-bind-try", sock, sock]; env["DISPLAY"] = disp
        xa = os.environ.get("XAUTHORITY")
        if xa and os.path.isfile(xa):
            # Only this display's cookie is shared, not every cookie in the file (those can include remote displays).
            scoped = scoped_xauth(xa, disp)
            if scoped: a += ["--ro-bind", scoped, xa]; env["XAUTHORITY"] = xa
            else: warn("could not read just this display's X cookie from XAUTHORITY; not sharing it (X11 apps may fail to connect)", name)
    if cfg["sound"]:
        for s in ("pipewire-0", "pulse/native"):
            a += ["--ro-bind-try", f"{rt}/{s}", f"{rt}/{s}"]
        env["PULSE_SERVER"] = f"unix:{rt}/pulse/native"
    if cfg["gpu"]:
        if cfg["gpu_render_only"]:     # render nodes are enough to draw; the card* nodes can also drive the display
            nodes = sorted(glob.glob("/dev/dri/renderD*"))
            if not nodes: warn("no GPU render node found, so the app gets no graphics device", name)
            for p in nodes: a += ["--dev-bind", p, p]
        else:
            a += ["--dev-bind-try", "/dev/dri", "/dev/dri"]
        for p in glob.glob("/dev/nvidia*"):
            a += ["--dev-bind", p, p]
    if cfg["camera"]:
        for p in glob.glob("/dev/video*") + glob.glob("/dev/media*"):
            a += ["--dev-bind", p, p]
    if cfg["input"]:
        a += ["--dev-bind-try", "/dev/input", "/dev/input", "--ro-bind-try", "/run/udev", "/run/udev"]
    if cfg["gpu"] or cfg["camera"] or cfg["input"]:
        a += ["--ro-bind-try", "/sys/dev/char", "/sys/dev/char"]
        for p in glob.glob("/sys/devices/pci*") or ["/sys/devices"]:
            a += ["--ro-bind", p, p]
    if cfg["printing"]:
        a += ["--ro-bind-try", "/run/cups/cups.sock", "/run/cups/cups.sock"]
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
    sa = os.environ.get("SSH_AUTH_SOCK", "")
    if cfg["ssh_agent"] and sa and os.path.exists(sa):
        a += ["--ro-bind", sa, sa]; env["SSH_AUTH_SOCK"] = sa
    if bus:
        a += ["--ro-bind", bus, f"{rt}/bus"]
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
        if cfg["portals"]: env["AJAIL_PORTALS"] = ",".join(extra_portals(cfg))     # read by the self-test
        if cfg["tray"]: env["AJAIL_TRAY"] = f"{TRAY_PIDS[0]}-{TRAY_PIDS[-1]}"      # read by the self-test
    if cfg["app_writable"]:
        # /app stays the app's own read-only files; what the app writes goes to a private layer that survives restarts
        up, wk = f"{LAYERS}/{name}/upper", f"{LAYERS}/{name}/work"
        if not NO_LOG[0]:
            for d in (up, wk): os.makedirs(d, mode=0o700, exist_ok=True)
        a += ["--overlay-src", root, "--overlay", up, wk, "/app"]
    else:
        a += ["--ro-bind", root, "/app"]
    if cfg["tmp_home"]: a += ["--perms", "0700", "--tmpfs", HOME]      # erased when the app closes
    else: a += ["--bind", f"{HOMES}/{name}", HOME]
    if cfg["theme"]:
        for rel in THEME:
            a += ["--ro-bind-try", f"{HOME}/{rel}", f"{HOME}/{rel}"]
        if gset:     # the host's button layout and cursor, read-only (see gsettings_keyfile_text)
            a += ["--ro-bind", gset, f"{HOME}/.config/glib-2.0/settings/keyfile"]
            env["GSETTINGS_BACKEND"] = "keyfile"
    for opt, p in share_list(cfg):
        ok = safe_path(p)
        if ok: a += [opt, ok, ok]
        else: warn(f"skipping unsafe or missing path '{p}'", name)
    for f in files:
        a += ["--bind", f, f]
    if cfg["compat_env"]:     # before env= below, so an explicit env= line still wins; never replaces a variable ajail set itself
        for k, v in compat_env(os.environ).items(): env.setdefault(k, v)
    for item in cfg["env"]:
        k, _, v = item.partition("=")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k): env[k] = v
    for k, v in env.items():
        a += ["--setenv", k, v]
    return a + ["--chdir", HOME] + inner

def forward_files(extra):
    """Files/folders passed on the command line (double-click, Open with) are shared for this run."""
    out = []
    for x in extra:
        p = urllib.parse.unquote(x[7:]) if x.startswith("file://") else x
        if p.startswith("-") or not os.path.exists(p): continue
        sp = safe_path(p)
        if sp and sp not in out: out.append(sp)
    return out

def appimage_offset(path):
    with open(path, "rb") as f:
        h = f.read(64)
        if len(h) < 64 or h[:4] != b"\x7fELF": die("not an AppImage (not an ELF file)")
        if h[8:11] == b"AI\x01": die("type 1 AppImages are not supported")
        if h[8:11] != b"AI\x02": die("not a type 2 AppImage")
        e = "<" if h[5] == 1 else ">"
        if h[4] == 2:
            shoff, = struct.unpack_from(e + "Q", h, 0x28); sz, num = struct.unpack_from(e + "HH", h, 0x3A)
        else:
            shoff, = struct.unpack_from(e + "I", h, 0x20); sz, num = struct.unpack_from(e + "HH", h, 0x2E)
        off = shoff + sz * num
        f.seek(off)
        if f.read(4) not in (b"hsqs", b"sqsh"): die("could not find the squashfs image inside this file")
    return off

def ensure_extracted(app, name, off):
    st = os.stat(app); stamp = f"{st.st_size}:{st.st_mtime_ns}"
    d = f"{EXTRACT}/{name}"
    os.makedirs(EXTRACT, mode=0o700, exist_ok=True)
    with open(f"{EXTRACT}/.{name}.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            if open(f"{d}/stamp").read() == stamp and os.path.isdir(f"{d}/root"):
                return f"{d}/root"
        except OSError:
            pass
        if not os.path.exists(UNSQ): die("unsquashfs missing: " + install_hint("squashfs-tools"))
        print(f"ajail: preparing {name} (first run or updated)...", file=sys.stderr)
        notify(f"Preparing {name} (first run or updated)")
        shutil.rmtree(d, ignore_errors=True)
        tmp = f"{EXTRACT}/{name}.tmp{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, mode=0o700)
        cmd = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"] + BASE + \
              ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
               "--ro-bind", app, "/in.AppImage", "--bind", tmp, "/out",
               UNSQ, "-no-progress", "-no-xattrs", "-o", str(off), "-d", "/out/root", "/in.AppImage"]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            die("extraction failed:\n" + CTRL.sub("", r.stderr[-800:]))     # file names in it come from the AppImage
        subprocess.run(["chmod", "-R", "u+rwX", f"{tmp}/root"], check=False)
        open(f"{tmp}/stamp", "w").write(stamp)
        os.replace(tmp, d)
        return f"{d}/root"

def check_identity(name, root):
    """The private home is keyed by file name. Remember what the app calls itself and refuse
    a different app that merely has the same file name (it would inherit data and permissions)."""
    ident = ""
    try:
        for f in sorted(glob.glob(f"{glob.escape(root)}/*.desktop"))[:1]:
            # Hostile content: stay inside the app, regular files only, and read a bounded amount.
            if not inside(root, f) or not os.path.isfile(f): continue
            with open(f, errors="replace") as fh: text = fh.read(65536)
            m = re.search(r"^Name=(.+)$", text, re.M)
            ident = CTRL.sub("", m.group(1)).strip() if m else ""
    except OSError:
        pass
    ident = ident[:IDENT_MAX]
    p = f"{CONF}/ids/{name}"
    try: old = open(p).read().strip()
    except OSError: old = None
    if not ident:
        # No usable name (no .desktop file, or only links and unreadable files). That must not be a way around the
        # check: an app that already has an identity only runs again if the file can still prove it is that app.
        if old is not None:
            die(f"'{name}' was first used by an app called '{old}', but this file has no readable .desktop file "
                f"to say what it is.\nIt would share that app's private data and permissions. Rename the file, or "
                f"delete {p} if it really is the same app.")
        return
    if old is None:
        os.makedirs(f"{CONF}/ids", exist_ok=True)
        open(p, "w").write(ident + "\n")
        log(f"first run, identity '{ident}'", name)
        notify(f"New app '{name}' runs with your default permissions. Adjust them in ajail-gui.")
    elif old != ident:
        die(f"'{name}' was first used by an app called '{old}', but this file calls itself '{ident}'.\n"
            f"It would share that app's private data and permissions. Rename the file, or delete {p} "
            f"if it really is the same app.")

def inside(root, p):
    r, q = os.path.realpath(root), os.path.realpath(p)
    return q == r or q.startswith(r + "/")
