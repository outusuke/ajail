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
        elif k in CHOICES:
            if v in CHOICES[k]: d["s"][k] = v
            else: bad("expected " + ", ".join(CHOICES[k][:-1]) + " or " + CHOICES[k][-1])
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
    cfg = {**DEFAULTS, **NUM, **{k: v[0] for k, v in CHOICES.items()}, **g["s"], **a["s"]}
    for k in ("allow", "allow_ro", "env", "talk"):
        cfg[k] = list(dict.fromkeys(g[k] + a[k]))
    on = list(DEFAULT_PORTALS)
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
            r = subprocess.run(["bwrap", "--help"], capture_output=True, text=True, timeout=10)
            _has[opt] = opt in (r.stdout + r.stderr)
        except (OSError, subprocess.SubprocessError):
            _has[opt] = False
    return _has[opt]

BASE = ["--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin"]

def arch_block(nrs, sock, x32):
    """The checks for one ABI. Jumps stay inside the block; the last two instructions are allow and deny."""
    deny, fam, n = 0x00050000 | errno.EPERM, SOCKET_FAMILIES, len(nrs)
    if n + len(fam) > 240: raise ValueError("too many blocked system calls: a BPF jump offset is 8 bits")
    b = len(fam) + 3                                            # length of the socket part below
    ins = [(0x20, 0, 0, 0)]                                     # load syscall number
    # x32 calls share the x86_64 audit arch and only differ by bit 30 of the number: deny them all
    if x32: ins += [(0x35, n + b + 1, 0, 0x40000000)]
    ins += [(0x15, 0, b - 1, sock), (0x20, 0, 0, 16)]           # socket(): load the family (low half of arg 0)
    ins += [(0x15, n + b - 3 - i, 0, f) for i, f in enumerate(fam)]    # listed family: jump to allow
    ins += [(0x06, 0, 0, deny)]                                 # any other family: deny
    ins += [(0x15, n - i, 0, nr) for i, nr in enumerate(nrs)]   # listed syscall: jump to deny
    return ins + [(0x06, 0, 0, 0x7FFF0000), (0x06, 0, 0, deny)] # allow, deny

def seccomp_file(harden=False, allow_32bit=False):
    """Syscall blocklist (like Flatpak's), generated as a raw BPF program. No extra packages needed."""
    machine = platform.machine()
    arch = {"x86_64": (0xC000003E, SYSCALLS_X86_64), "aarch64": (0xC00000B7, SYSCALLS_AARCH64)}.get(machine)
    if not arch:
        return None
    audit, nrs = arch
    if harden: nrs = nrs + SYSCALLS_EXTRA[machine]
    blocks = [(audit, arch_block(nrs, SOCKET_NR[machine], True))]
    if machine == "x86_64" and allow_32bit:
        blocks.append((I386_AUDIT, arch_block(SYSCALLS_I386 + (SYSCALLS_I386_EXTRA if harden else []), I386_SOCKET_NR, False)))
    ins, start = [(0x20, 0, 0, 4)], len(blocks) + 2             # load arch; the blocks follow the arch checks and the deny
    for audit, blk in blocks:
        ins += [(0x15, start - len(ins) - 1, 0, audit)]         # this ABI: jump to its block
        start += len(blk)
    ins += [(0x06, 0, 0, 0x00050000 | errno.EPERM)]             # any other ABI: deny
    for _, blk in blocks: ins += blk
    try:
        tf = tempfile.TemporaryFile()
        tf.write(b"".join(struct.pack("=HBBI", *i) for i in ins)); tf.flush(); tf.seek(0)
        return tf
    except OSError as e:
        log(f"seccomp disabled: {e}")
        return None

def want_bus(cfg):
    return bool(cfg["portals"] or cfg["tray"] or cfg["gnome_shell"] or cfg["kde"] or cfg["talk"])

# Desktop portals an app may call when "Notifications and portals" is on. xdg-dbus-proxy runs on the host, so the
# desktop sees the app as an ordinary unsandboxed program: a portal that is not listed here is simply unreachable.
# DEFAULT_PORTALS are on unless portal=-NAME; the other EXTRA_PORTALS are off unless portal=NAME.
# Clipboard only works with RemoteDesktop or InputCapture; Background can register an autostart that runs outside the sandbox.
# Never reachable: the rest of the Documents portal and anything else not listed; with portals on, the app
# sees only its own folder of picked files (doc/by-app/ID), see portal_app_id.
# Camera follows the Camera permission; Screenshot and ScreenCast (both show the user a prompt) have their own switches.
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_DOCS_NAME = "org.freedesktop.portal.Documents"
PORTAL_DOCS_PATH = "/org/freedesktop/portal/documents"
PORTAL_BASIC = ("Settings", "OpenURI", "FileChooser", "Notification", "Print", "Inhibit", "Account",
                "Email", "Trash", "NetworkMonitor", "ProxyResolver", "GameMode", "MemoryMonitor",
                "PowerProfileMonitor", "Realtime")

def extra_portals(cfg):
    """The extra portals enabled for this app: allowlisted names only, no duplicates."""
    return [p for p in dict.fromkeys(cfg["portal"]) if p in EXTRA_PORTALS]

# Basic portals that can be switched off one by one (portal_NAME=0): setting -> portal interfaces. The file chooser and Settings stay in the base.
PORTAL_TOGGLES = {"portal_notification": ("Notification",), "portal_openuri": ("OpenURI",), "portal_print": ("Print",),
                  "portal_trash": ("Trash",), "portal_email": ("Email",), "portal_inhibit": ("Inhibit",),
                  "portal_network": ("NetworkMonitor", "ProxyResolver")}

def portal_rules(cfg):
    """xdg-dbus-proxy rules for Notifications and the allowed portals (see PORTAL_BASIC above)."""
    off = {i for k, v in PORTAL_TOGGLES.items() if not cfg.get(k, 1) for i in v}
    ifaces = [i for i in PORTAL_BASIC if i not in off and not (i == "Account" and cfg["privacy"])]     # Account hands out the real name
    if cfg["camera"]: ifaces.append("Camera")
    if cfg["portal_screenshot"]: ifaces.append("Screenshot")
    if cfg["portal_screencast"]: ifaces.append("ScreenCast")
    ifaces += [p for p in extra_portals(cfg) if p not in ifaces and p != "FileTransfer"]
    rules = (["--talk=org.freedesktop.Notifications"] if cfg.get("portal_notification", 1) else []) + [f"--see={PORTAL_NAME}"]
    rules += [f"--call={PORTAL_NAME}=org.freedesktop.portal.{i}.*" for i in ifaces]
    if "FileTransfer" in extra_portals(cfg):
        rules += [f"--see={PORTAL_DOCS_NAME}", f"--call={PORTAL_DOCS_NAME}=org.freedesktop.portal.FileTransfer.*@{PORTAL_DOCS_PATH}"]
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

# GNOME Shell integration (gnome_shell=1). The browser's connector (gnome-browser-connector, run by the browser inside the sandbox) talks to the
# Shell as org.gnome.Shell at /org/gnome/Shell, interface org.gnome.Shell.Extensions. The rules name that interface and path only: org.gnome.Shell,
# the interface of Eval, stays closed, and nothing is --talk. The connector is a GApplication that registers org.gnome.BrowserConnector on the session
# bus when it starts and exits at once if the bus refuses, so that one name is --own: the app runs the connector itself, and no service outside the
# sandbox is reached.
GNOME_SHELL_OWN = "org.gnome.BrowserConnector"
GNOME_SHELL = "org.gnome.Shell"
GNOME_SHELL_PATH = "/org/gnome/Shell"
GNOME_SHELL_EXT = "org.gnome.Shell.Extensions"
GNOME_SHELL_CALLS = ("ListExtensions", "GetExtensionInfo", "GetExtensionErrors", "InstallRemoteExtension")     # InstallRemoteExtension: the Shell asks you before it installs
GNOME_SHELL_MANAGE = ("EnableExtension", "DisableExtension", "UninstallExtension")                             # gnome_shell_manage=1 adds these

def gnome_shell_rules(cfg):
    """Let the app list, inspect and (after your confirmation in the Shell) install GNOME Shell extensions, and read the Shell's version.
    gnome_shell_manage=1 adds enabling, disabling and uninstalling. Properties.Get and GetAll are read-only (no Set, so UserExtensionsEnabled can't
    be changed); the broadcasts are the signals the connector listens to. Nothing else of org.gnome.Shell is reachable."""
    if not cfg["gnome_shell"]: return []
    p = GNOME_SHELL_PATH; props = "org.freedesktop.DBus.Properties"
    calls = GNOME_SHELL_CALLS + (GNOME_SHELL_MANAGE if cfg["gnome_shell_manage"] else ())
    return ([f"--own={GNOME_SHELL_OWN}"] + [f"--call={GNOME_SHELL}={GNOME_SHELL_EXT}.{m}@{p}" for m in calls]
            + [f"--call={GNOME_SHELL}={props}.Get@{p}", f"--call={GNOME_SHELL}={props}.GetAll@{p}"]
            + [f"--broadcast={GNOME_SHELL}={GNOME_SHELL_EXT}.{s}@{p}" for s in ("ExtensionStateChanged", "ExtensionStatusChanged")]
            + [f"--broadcast={GNOME_SHELL}={props}.PropertiesChanged@{p}"])

# KDE Plasma integration (kde=1). The browser's Plasma integration (plasma-browser-integration-host, run by the browser inside the sandbox) does four
# things on the session bus, and nothing here is --talk: (1) it owns org.kde.plasma.browser_integration, where KRunner finds its tab and history
# runners (KRunner calls in, so no rule is needed for that) and, when that name is taken, the same name with -PID; (2) it owns
# org.mpris.MediaPlayer2.plasma-browser-integration (the same -PID fallback) for the media controls; (3) it shows downloads as progress jobs:
# KUiServerV2JobTracker calls org.kde.JobViewServer.requestView, which answers with an object path /org/kde/notificationmanager/jobs/JobView_N,
# then calls update and terminate on that object and listens to its cancel, suspend and resume signals; (4) with "inhibit suspend while downloading"
# on in the extension (off in Chrome by default) it calls Inhibit and UnInhibit on org.freedesktop.PowerManagement.Inhibit (Powerdevil).
# The host's other plugins stay closed: KDE Connect (it can run commands you saved), the Purpose share menu, and the old org.kde.kuiserver and JobViewV2.
KDE_NAMES = ("org.kde.plasma.browser_integration", "org.mpris.MediaPlayer2.plasma-browser-integration")
KDE_JOBS = "org.kde.JobViewServer"
KDE_JOB_PATH = "/org/kde/notificationmanager/jobs/*"
KDE_INHIBIT = "org.freedesktop.PowerManagement.Inhibit"
KDE_INHIBIT_PATH = "/org/freedesktop/PowerManagement/Inhibit"

def kde_rules(cfg):
    """Let the browser's Plasma integration show downloads as jobs in Plasma, answer KRunner and the media controls, and keep the computer awake
    while it downloads. Per-method rules and the host's own names only: org.kde.JobViewServer is never --talk, and the object paths are the ones Plasma hands out."""
    if not cfg["kde"]: return []
    own = [f"--own={n}{s}" for n in KDE_NAMES for s in [""] + [f"-{p}" for p in TRAY_PIDS]]
    jobs = [f"--call={KDE_JOBS}=org.kde.JobViewServerV2.requestView@/JobViewServer"]
    jobs += [f"--call={KDE_JOBS}=org.kde.JobViewV3.{m}@{KDE_JOB_PATH}" for m in ("update", "terminate")]
    jobs += [f"--broadcast={KDE_JOBS}=org.kde.JobViewV3.{s}@{KDE_JOB_PATH}" for s in ("updateRequested", "suspendRequested", "resumeRequested", "cancelRequested")]
    return own + jobs + [f"--call={KDE_INHIBIT}={KDE_INHIBIT}.{m}@{KDE_INHIBIT_PATH}" for m in ("Inhibit", "UnInhibit")]

# Screen readers (Orca) don't read the screen: each app publishes its widgets on the accessibility bus, a second D-Bus apart from the session
# bus, and the reader reads them there. With a11y=1 the app gets a filtered copy of that bus, with the rules Flatpak uses.
A11Y_REGISTRY = "org.a11y.atspi.Registry"
A11Y_ROOT = "/org/a11y/atspi/accessible/root"
A11Y_REG_PATH = "/org/a11y/atspi/registry"
A11Y_DEC_PATH = "/org/a11y/atspi/registry/deviceeventcontroller"

def a11y_rules():
    """What the accessibility proxy lets through. The app may plug its widgets into the registry (Socket.Embed), ask who listens for events
    and keys, and hear that a reader started or stopped listening. There is no --talk, so it can't call anything else: not the other apps'
    widgets, and not GenerateKeyboardEvent or the other calls that send keys and clicks. --sloppy-names: the peers on this bus use unique names."""
    r = A11Y_REGISTRY
    return ["--sloppy-names",
            f"--call={r}=org.a11y.atspi.Socket.Embed@{A11Y_ROOT}",
            f"--call={r}=org.a11y.atspi.Socket.Unembed@{A11Y_ROOT}",
            f"--call={r}=org.a11y.atspi.Registry.GetRegisteredEvents@{A11Y_REG_PATH}",
            f"--call={r}=org.a11y.atspi.DeviceEventController.GetKeystrokeListeners@{A11Y_DEC_PATH}",
            f"--call={r}=org.a11y.atspi.DeviceEventController.GetDeviceEventListeners@{A11Y_DEC_PATH}",
            f"--call={r}=org.a11y.atspi.DeviceEventController.NotifyListenersSync@{A11Y_DEC_PATH}",
            f"--call={r}=org.a11y.atspi.DeviceEventController.NotifyListenersAsync@{A11Y_DEC_PATH}",
            f"--broadcast={r}=org.a11y.atspi.Registry.EventListenerRegistered@{A11Y_REG_PATH}",      # so the app emits only what a reader asked for
            f"--broadcast={r}=org.a11y.atspi.Registry.EventListenerDeregistered@{A11Y_REG_PATH}"]

# System-bus services that run for the whole computer. Each switch opens one fixed name, with no free-form list.
SYSTEM_BUS_SOCKET = "/run/dbus/system_bus_socket"
SYSTEM_BUS_NAMES = {"bluetooth": "org.bluez", "battery": "org.freedesktop.UPower", "avahi": "org.freedesktop.Avahi"}

def system_bus_rules(cfg):
    return [f"--talk={n}" for k, n in SYSTEM_BUS_NAMES.items() if cfg.get(k)]

def system_bus_address():
    addr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "")
    if addr.startswith("unix:"): return addr
    return f"unix:path={SYSTEM_BUS_SOCKET}" if os.path.exists(SYSTEM_BUS_SOCKET) else None

def a11y_bus_address():
    """Where your accessibility bus is: $AT_SPI_BUS_ADDRESS if it is set, else what org.a11y.Bus says (asking also starts the bus, which a
    session only starts when something needs it), else the socket in your runtime folder. None when there is none."""
    addr = os.environ.get("AT_SPI_BUS_ADDRESS", "")
    if addr.startswith("unix:"): return addr
    ask = (("gdbus", ["gdbus", "call", "--session", "--dest", "org.a11y.Bus", "--object-path", "/org/a11y/bus", "--method", "org.a11y.Bus.GetAddress"]),
           ("busctl", ["busctl", "--user", "call", "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress"]))
    for tool, cmd in ask:
        if not shutil.which(tool): continue
        try: r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError): continue
        m = re.search(r"unix:[^'\"\s)]+", r.stdout) if r.returncode == 0 else None
        if m: return m.group(0)
    socks = sorted(glob.glob(f"{runtime_dir()}/at-spi/bus_*"))
    return f"unix:path={socks[0]}" if socks else None

def portal_app_id(name):
    """The application id the desktop portals know this app by: io.github.outusuke.ajail.Run.NAME, with anything outside letters, digits and _
    turned into _ and a p put in front of a digit (an id part may not start with one). The Documents portal keeps the files you
    picked for it under doc/by-app/ID."""
    part = re.sub(r"[^A-Za-z0-9_]", "_", name) or "_"
    return "io.github.outusuke.ajail.Run." + ("p" + part if part[0].isdigit() else part)

PROXY_ROOT_SKIP = ("proc", "dev", "sys", "home", "root", "mnt", "media", "srv", "boot", "lost+found")

def top_binds(skip=()):
    """One read-only bind for each top-level folder of this computer (links stay links), for the proxy's small root.
    The new root is then a filesystem a file can be added to, such as /.flatpak-info: bwrap can't make a file on a read-only /."""
    a = []
    try: top = sorted(os.listdir("/"))
    except OSError: top = []
    for e in top:
        p = "/" + e
        if e in skip: continue
        if os.path.islink(p): a += ["--symlink", os.readlink(p), p]
        elif os.path.isdir(p): a += ["--ro-bind", p, p]
    return a

def app_info_text(app, instance=0):
    """The .flatpak-info the portal reads from the proxy. It wants an [Instance] group too, or it refuses every call."""
    return f"[Application]\nname={app}\n\n[Instance]\ninstance-id={instance}\n"

def proxy_sandbox(info, rt):
    """bwrap arguments for a small root to run xdg-dbus-proxy in: the system read-only, your runtime folder read-write (the bus is
    there, and the proxy makes its own socket there), your home and other private folders not at all, and the app's .flatpak-info
    at /.flatpak-info. xdg-desktop-portal reads that file from the process that talks to it, which is the proxy, so it knows the
    app as an app of its own and the file chooser hands out files through the document portal, not as real paths the sandbox
    can't see."""
    a = ["bwrap", "--die-with-parent", "--new-session", "--clearenv", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
         "--proc", "/proc", "--dev", "/dev"]
    return a + top_binds(PROXY_ROOT_SKIP) + ["--bind", rt, rt, "--ro-bind", info, "/.flatpak-info"]

def write_app_info(path, app):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f: f.write(app_info_text(app, os.getpid()))

def start_bus(cfg, name):
    """Start xdg-dbus-proxy for this app. With portals on, the proxy runs in a small sandbox that tells the portals which app it
    is; if that fails it runs plainly. Returns (process, pipe, socket) or None (a warning says why)."""
    if not want_bus(cfg): return None
    px = shutil.which("xdg-dbus-proxy")
    if not px:
        warn("xdg-dbus-proxy missing, so notifications/portals are off. Install: " + install_hint("xdg-dbus-proxy"), name)
        return None
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime_dir()}/bus"
    d = f"{runtime_dir()}/ajail"
    os.makedirs(d, mode=0o700, exist_ok=True)
    sock = f"{d}/{os.getpid()}.bus"
    info = sock + ".info"
    rules = ["--filter"]
    if cfg["portals"]:
        rules += portal_rules(cfg)
    if cfg["tray"]:
        rules += tray_rules() + ["--own=org.mpris.MediaPlayer2.*"]
    rules += gnome_shell_rules(cfg) + kde_rules(cfg)
    rules += [f"--talk={n}" for n in cfg["talk"] if talk_ok(n)]
    for wrapped in ((True, False) if cfg["portals"] and shutil.which("bwrap") else (False,)):
        if wrapped:
            try: write_app_info(info, portal_app_id(name))
            except OSError: continue
        r, w = os.pipe()
        cmd = [px, f"--fd={w}", addr, sock] + rules
        if wrapped: cmd = proxy_sandbox(info, runtime_dir()) + cmd
        try:
            proc = subprocess.Popen(cmd, pass_fds=[w], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as e:
            os.close(r); os.close(w)
            if wrapped: continue
            _unlink_all(sock, info)
            warn(f"could not start xdg-dbus-proxy ({e}); running without D-Bus", name)
            log("xdg-dbus-proxy failed to start", name)
            return None
        os.close(w)
        ok = False
        try:
            ok = bool(select.select([r], [], [], 3)[0]) and bool(os.read(r, 1))
        except OSError:
            pass
        b = (proc, r, sock)
        if ok: return b
        stop_bus(b)
        if wrapped: warn("the portal proxy could not start as an app of its own; the file chooser may give paths the app can't see", name)
    _unlink_all(sock, info)
    print("ajail: could not start xdg-dbus-proxy; running without D-Bus", file=sys.stderr)
    log("xdg-dbus-proxy failed to start", name)
    return None

def start_a11y(name):
    """Start xdg-dbus-proxy on your accessibility bus with the a11y rules. Returns (process, pipe, socket) like start_bus, or None (a warning says why)."""
    px = shutil.which("xdg-dbus-proxy")
    if not px:
        warn("xdg-dbus-proxy missing, so a11y is off. Install: " + install_hint("xdg-dbus-proxy"), name)
        return None
    addr = a11y_bus_address()
    if not addr:
        warn("a11y=1, but your accessibility bus was not found (is at-spi2-core installed and a desktop session running?); running without it", name)
        return None
    d = f"{runtime_dir()}/ajail"
    os.makedirs(d, mode=0o700, exist_ok=True)
    sock = f"{d}/{os.getpid()}.a11y"
    r, w = os.pipe()
    try:
        proc = subprocess.Popen([px, f"--fd={w}", addr, sock, "--filter"] + a11y_rules(), pass_fds=[w],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        os.close(r); os.close(w)
        _unlink_all(sock)
        warn(f"could not start xdg-dbus-proxy ({e}); running without a11y", name)
        return None
    os.close(w)
    ok = False
    try: ok = bool(select.select([r], [], [], 3)[0]) and bool(os.read(r, 1))
    except OSError: pass
    b = (proc, r, sock)
    if ok: return b
    stop_bus(b)
    warn("could not start xdg-dbus-proxy on the accessibility bus; running without a11y", name)
    return None

def start_system(cfg, name):
    """Start xdg-dbus-proxy on the system bus with one --talk for each switch that is on. Returns (process, pipe, socket) like start_bus, or None (a warning says why)."""
    px = shutil.which("xdg-dbus-proxy")
    if not px:
        warn("xdg-dbus-proxy missing, so Bluetooth, battery and network discovery are off. Install: " + install_hint("xdg-dbus-proxy"), name)
        return None
    addr = system_bus_address()
    if not addr:
        warn("a system bus switch is on, but the system bus was not found; running without it", name)
        return None
    d = f"{runtime_dir()}/ajail"
    os.makedirs(d, mode=0o700, exist_ok=True)
    sock = f"{d}/{os.getpid()}.sys"
    r, w = os.pipe()
    try:
        proc = subprocess.Popen([px, f"--fd={w}", addr, sock, "--filter"] + system_bus_rules(cfg), pass_fds=[w],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        os.close(r); os.close(w)
        _unlink_all(sock)
        warn(f"could not start xdg-dbus-proxy ({e}); running without the system bus", name)
        return None
    os.close(w)
    ok = False
    try: ok = bool(select.select([r], [], [], 3)[0]) and bool(os.read(r, 1))
    except OSError: pass
    b = (proc, r, sock)
    if ok: return b
    stop_bus(b)
    warn("could not start xdg-dbus-proxy on the system bus; running without it", name)
    return None

def _unlink_all(*paths):
    for p in paths:
        try: os.unlink(p)
        except OSError: pass

def stop_bus(b):
    if not b: return
    proc, r, sock = b
    try: os.close(r)
    except OSError: pass
    try:
        proc.terminate()
        try: proc.wait(2)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait(2)       # a stuck proxy must not outlive us
    except (OSError, subprocess.SubprocessError): pass
    _unlink_all(sock, sock + ".info")

def private_net(cfg):
    """'Keep localhost private': the app gets its own network (via pasta) that reaches the internet but not this computer."""
    return bool(cfg["net"] and cfg["net_private"])

# pasta gives the sandbox its own network namespace. Nothing is forwarded in either direction, the gateway address is
# not mapped to this computer (--no-map-gw), and DNS goes through a forwarder address that pasta answers itself.
DNS_FWD = "169.254.1.1"
PASTA = ["pasta", "--quiet", "--config-net", "--no-map-gw", "--dns-forward", DNS_FWD,
         "-t", "none", "-u", "none", "-T", "none", "-U", "none", "--"]
SEC_FD = 9      # pasta closes inherited descriptors, so the seccomp filter is re-opened from a file inside it
INFO_FD = 8     # and the file bwrap writes its --info-fd to likewise (one digit: dash, the usual /bin/sh, can't open "10>file")

def net_wrap(inner, secpath=None, infopath=None):
    """The words that go in front of bwrap to run it inside pasta. A shell first opens SECPATH (to read) as descriptor 9 and
    INFOPATH (to write) as descriptor 8, which bwrap then inherits."""
    files = ([(SEC_FD, "<", secpath)] if secpath else []) + ([(INFO_FD, ">", infopath)] if infopath else [])
    if not files: return PASTA + inner
    opens = "; ".join(f'exec {fd}{op}"${i}"' for i, (fd, op, _) in enumerate(files, 1))
    shift = "shift" if len(files) == 1 else f"shift {len(files)}"
    return PASTA + ["/bin/sh", "-c", f'{opens}; {shift}; exec "$@"', "sh"] + [p for _, _, p in files] + inner

def work_dir():
    return f"{runtime_dir()}/ajail/{os.getpid()}.d"

def machine_id(name):
    """Stable random machine-id per app (D-Bus and SDL want one; the real one would let apps fingerprint you)."""
    p = f"{CONF}/apps/{name}.machine-id"
    try:
        with open(p) as f: v = f.read().strip()
        if len(v) == 32: return v + "\n"
    except OSError: pass
    import uuid
    v = uuid.uuid4().hex
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f: f.write(v + "\n")
        os.replace(tmp, p)      # O_EXCL on p itself kept a bad file, so every launch got a new id
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
    # Done in Python (no xauth needed, and many Wayland desktops don't install it). An entry is kept when it is for this
    # display number AND for this machine (family Local with our host name, or family Wild), so a cookie for display :0
    # on another host is not shared. Entries with no display number (as Xwayland's own auth file uses) are kept too.
    d = work_dir()
    if not os.path.isdir(d):
        return None
    num = disp[1:].split(".")[0].encode()
    me = os.fsencode(socket.gethostname())
    try:
        with open(src, "rb") as f: data = f.read(1_000_000)
    except OSError:
        return None
    keep, i = [], 0
    while i + 2 <= len(data):
        start = i
        fam = int.from_bytes(data[i:i+2], "big"); i += 2      # family: 256 Local, 65535 Wild
        fields = []
        for _ in range(4):                        # address, display number, auth name, auth data
            if i + 2 > len(data): return None     # truncated or not an Xauthority file: not shared
            n = int.from_bytes(data[i:i+2], "big"); i += 2
            fields.append(data[i:i+n]); i += n
        if i > len(data): return None
        mine = fam == 65535 or (fam == 256 and fields[0] == me)
        if fields[1] == b"" or (fields[1] == num and mine): keep.append(data[start:i])
    if not keep:
        return None
    dst = f"{d}/Xauthority"
    try:
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f: f.write(b"".join(keep))
    except OSError:
        return None
    return dst

def bwrap_args(name, root, cfg, inner, bus=None, sec=None, files=(), gen=None, gset=None, wl=None, info=None, a11y=None, system=None):
    """wl: a socket the compositor made for this app with a Wayland security context (see 25_wayland). With it the app gets
    that restricted socket in place of the real one. 'Wayland only if restricted' (wayland_strict) without it: no Wayland.
    info: the descriptor bwrap writes its bwrapinfo.json to, which the portals need to find the app (see launch).
    a11y: the socket of the filtered accessibility bus (see start_a11y), for a screen reader.
    system: the socket of the filtered system bus (see start_system), for bluetooth, battery and avahi."""
    rt = runtime_dir()
    wp = wayland_path() if cfg["wayland"] else None
    if wp and cfg["wayland_strict"] and not wl: wp = None
    a = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    if not cfg["userns"]:
        # --disable-userns needs an explicit --unshare-user ("--unshare-all" only tries one). If a user namespace can't be
        # made, the app does not start: asked for stricter isolation, it never silently gets less. The same goes for an
        # old bubblewrap that can't do it: the app is not started with less than was asked for.
        if bwrap_has("--disable-userns"): a += ["--unshare-user", "--disable-userns"]
        else: die("this bubblewrap is too old for --disable-userns (needs 0.8+), so 'Allow the app's own sandbox = off' can't "
                  "be honored. Update bubblewrap, or turn that option back on for this app (userns=1).")
    if cfg["privacy"]:
        if bwrap_has("--hostname"): a += ["--hostname", "ajail"]
        else: die("this bubblewrap is too old for --hostname, so 'Hide personal details' can't hide the computer name. "
                  "Update bubblewrap, or turn that option off for this app (privacy=0).")
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
           "ELECTRON_OZONE_PLATFORM_HINT": ("auto" if cfg["x11"] else "wayland") if wp else "x11"}
    for k in ("LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_TIME", "TZ", "TERM",
              "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XCURSOR_THEME", "XCURSOR_SIZE"):
        if k in os.environ: env[k] = os.environ[k]
    if wp:
        a += ["--ro-bind", wl or wp, wp]
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
    if cfg["ntsync"]:
        a += ["--dev-bind-try", "/dev/ntsync", "/dev/ntsync"]
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
        if cfg["portals"]:      # a toolkit uses the file chooser portal only when told to; a theme that compat_env passes through (gtk3, qt6ct) would open Qt's own dialog, so it is replaced; env= still wins (no /.flatpak-info for the app: glycin would then run its image loaders through flatpak-spawn, which fails, and GTK aborts)
            if cfg.get("gtk_portal") != "0": env["GTK_USE_PORTAL"] = "1"      # "auto" is settled by the caller (settle_gtk_portal); unsettled it counts as on
            env["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
            if info is not None: a += ["--info-fd", str(info)]
    if a11y and cfg["a11y"]:
        a += ["--ro-bind", a11y, f"{rt}/at-spi/bus"]
        env["AT_SPI_BUS_ADDRESS"] = f"unix:path={rt}/at-spi/bus"
        env.update(A11Y_ENV)
    if system and any(cfg[k] for k in SYSTEM_BUS_NAMES):
        a += ["--ro-bind", system, SYSTEM_BUS_SOCKET]
        env["DBUS_SYSTEM_BUS_ADDRESS"] = f"unix:path={SYSTEM_BUS_SOCKET}"
    if cfg["portals"]:      # the files you pick in a chooser appear here, only this app's own (bind-try: nothing when there is no document portal)
        a += ["--bind-try", f"{rt}/doc/by-app/{portal_app_id(name)}", f"{rt}/doc"]
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
    if cfg["single_instance"]:      # a second launch of the same app finds the first one's lock/socket files (Chrome keeps its singleton socket in $TMPDIR); /tmp stays private
        si = f"{rt}/ajail/single/{name}"
        if not NO_LOG[0]:
            for d in (f"{rt}/ajail", f"{rt}/ajail/single", si): os.makedirs(d, mode=0o700, exist_ok=True)
        a += ["--bind", si, si]
        env["TMPDIR"] = si          # set before compat_env and env=, so an env=TMPDIR=... of yours still wins
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
