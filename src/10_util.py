# ======== 10_util: Utilities: logging, notifications, name cleaning, install hints.

def distro_family():
    ids = []
    try:
        for ln in open(OS_RELEASE):
            k, _, v = ln.strip().partition("=")
            if k in ("ID", "ID_LIKE"): ids += v.strip('"').lower().split()
    except OSError:
        return None
    for fam, keys in (("fedora", ("fedora", "rhel")), ("debian", ("debian", "ubuntu")),
                      ("arch", ("arch",))):
        if any(k in ids for k in keys): return fam
    return None

def install_hint(*pkgs):
    """'sudo dnf install foo' style hint for this distro, or a generic one if it isn't recognised."""
    fam = distro_family()
    if fam not in INSTALL_CMD: return "install " + ", ".join(pkgs) + " with your package manager"
    return f"{INSTALL_CMD[fam]} " + " ".join(pkgs)

# Control characters (C0 except tab/newline, DEL and C1) are stripped from app output before it reaches a terminal or the log.
CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

def die(msg, code=1):
    print(f"ajail: {msg}", file=sys.stderr); sys.exit(code)

def split_opts(args, allowed, usage):
    """Split a command's arguments into (set of options, other arguments). An option (anything starting with '--') that
    is not in ALLOWED is an error, so a typo like --trsh or --purg is never silently ignored."""
    opts, rest = set(), []
    for x in args:
        if x.startswith("--"):
            if x not in allowed: die(f"unknown option '{x[:40]}'\n{usage}")
            opts.add(x)
        else: rest.append(x)
    return opts, rest

def log(msg, name=None):
    try:
        os.makedirs(STATE, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
            tail = open(LOG, errors="replace").readlines()[-500:]
            open(LOG, "w").writelines(tail)
        with open(LOG, "a") as f:
            f.write(f"[{time.strftime('%F %T')}] " + (f"[{name}] " if name else "") + f"{msg}\n")
    except OSError:
        pass

LOG_RUN_MAX = 2_000_000    # bytes of one app run's output kept in the log
LOG_LINE_MAX = 65_536     # longest log line: longer output is split, so one unbroken stream can't pile up in memory
NO_LOG = [False]      # set by 'preview' so that showing a command never writes to the log

def warn(msg, name=None):
    """A warning for the terminal and the log, so it also shows up on ajail-gui's Log page (menu launches have no terminal)."""
    print(f"ajail: {msg}", file=sys.stderr)
    if not NO_LOG[0]: log(f"WARNING {msg}", name)

def notify(msg, critical=False):
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-u", "critical" if critical else "normal", "ajail", msg],
                           timeout=5, check=False)
        except Exception:
            pass

_act = []
def notify_failure(name):
    """Critical notification; clicking it (or its 'Open log' button) opens ajail-gui on this app's log."""
    msg = f"{name} failed to start. Click to open the log."
    if not shutil.which("notify-send"): return
    if not _act:
        try: _act.append("--action" in subprocess.run(["notify-send", "--help"], capture_output=True, text=True).stdout)
        except Exception: _act.append(False)
    if not _act[0]:
        notify(f"{name} failed to start. Open ajail-gui > Log.", critical=True); return
    try:
        if os.fork(): return                      # parent carries on and exits
    except OSError:
        return
    try:                                          # child waits for the click, detached from the terminal
        os.setsid()
        dn = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2): os.dup2(dn, fd)
        r = subprocess.run(["notify-send", "-u", "critical", "-A", "default=Open log", "--wait", "ajail", msg],
                           capture_output=True, text=True, timeout=900)
        if r.stdout.strip() == "default":
            subprocess.Popen([f"{os.path.dirname(SELF)}/ajail-gui", "--log", name], start_new_session=True)
    except Exception:
        pass
    finally:
        os._exit(0)

def clean(n):
    return (re.sub(r"[^A-Za-z0-9._-]", "_", n).lstrip("._-")[:64]) or "app"

def sanitize(path):
    n = re.sub(r"\.appimage$", "", os.path.basename(path), flags=re.I)
    s = re.sub(r"[-_](?:v?\d[\w.+~]*|x86[-_]64|amd64|x64|aarch64|arm64|linux\w*)(?=[-_]|$)", "", n, flags=re.I)
    return clean(s or n)
