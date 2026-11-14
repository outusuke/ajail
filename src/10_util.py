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
    print(f"ajail: {msg}", file=sys.stderr)
    if LOG_NAME[0] and not NO_LOG[0]: log(f"ERROR {msg}", LOG_NAME[0])     # a start from the app menu has no terminal; the log is where it shows
    sys.exit(code)

def exit_code(rc):
    """The shell's way: 128 + the signal number when the process was killed by a signal."""
    return 128 - rc if rc < 0 else rc

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

LOG_KEEP = 500             # lines kept when the file passes LOG_MAX
LOG_MAX = 1_000_000        # bytes
LOG_RUN_MAX = 2_000_000    # bytes of one app run's output kept in the log
LOG_LINE_MAX = 65_536     # longest log line: longer output is split, so one unbroken stream can't pile up in memory
NO_LOG = [False]      # set by 'preview' so that showing a command never writes to the log
LOG_NAME = [None]     # set when a run starts: errors from then on are logged under this name. None: not logging

def open_log():
    """The log, opened for appending (owner-only, never through a link), or None. Never replaced or renamed: several ajails
    share it, and a handle that outlives a replaced file would keep writing into a file nobody reads."""
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        fd = os.open(LOG, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a+", errors="replace")
    except OSError:
        return None

def log_write(f, text):
    """Append TEXT to the open log F under a lock. When the file is over LOG_MAX it is cut down to its last LOG_KEEP lines
    first, in place (truncate, not replace), so another ajail cannot cut a line in half and no handle is left on an old
    file. App output goes through here too, so a chatty app cannot grow the log past the limit."""
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            if os.fstat(f.fileno()).st_size > LOG_MAX:
                f.seek(0); tail = f.readlines()[-LOG_KEEP:]
                f.truncate(0); f.writelines(tail)
            f.write(text); f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass

def log(msg, name=None):
    """Add a line to the log. Only you can read it (it holds what apps print). Control characters and line breaks are
    removed first, so a message that quotes an app's text can't inject escape sequences or fake log lines."""
    f = open_log()
    if not f: return
    with f:
        msg = CTRL.sub("", msg).replace("\n", " ")
        log_write(f, f"[{time.strftime('%F %T')}] " + (f"[{name}] " if name else "") + f"{msg}\n")

def warn(msg, name=None):
    """A warning for the terminal and the log, so it also shows up on ajail-gui's Log page (menu launches have no terminal)."""
    print(f"ajail: {msg}", file=sys.stderr)
    if not NO_LOG[0]: log(f"WARNING {msg}", name)

def notify(msg, critical=False):
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-u", "critical" if critical else "normal", "ajail", msg],
                           timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            pass

def notify_click(plain, clicked, label, gui_args, urgency="critical"):
    """A desktop notification. Where notify-send can do actions, it says CLICKED and a click on the LABEL button opens
    ajail-gui with GUI_ARGS; the waiting for the click happens in a detached child, so ajail can exit at once. Without
    actions (an old notify-send, or no ajail-gui to open) it is a plain popup saying PLAIN. Nothing happens without
    notify-send."""
    ns = shutil.which("notify-send")
    if not ns: return
    gui = shutil.which("ajail-gui") or f"{os.path.dirname(SELF)}/ajail-gui"
    try: actions = "--action" in subprocess.run([ns, "--help"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError): actions = False
    if not actions or not os.access(gui, os.X_OK):
        notify(plain, critical=urgency == "critical"); return
    try:
        if os.fork(): return                      # parent carries on and exits
    except OSError:
        return
    try:                                          # child waits for the click, detached from the terminal
        os.setsid()
        dn = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2): os.dup2(dn, fd)
        r = subprocess.run([ns, "-u", urgency, "-A", f"default={label}", "--wait", "ajail", clicked],
                           capture_output=True, text=True, timeout=900)
        if r.stdout.strip() == "default": subprocess.Popen([gui, *gui_args], start_new_session=True)
    except Exception:
        pass
    finally:
        os._exit(0)

# Signals that mean the app itself crashed (nobody stopped it), and how a notification says it.
CRASH_SIGNALS = {"SIGSEGV": "segmentation fault", "SIGABRT": "aborted", "SIGBUS": "bus error", "SIGILL": "illegal instruction",
                 "SIGFPE": "arithmetic error", "SIGTRAP": "trap", "SIGSYS": "bad system call"}
STOP_CODES = (129, 130, 143)    # shell exit codes of SIGHUP, SIGINT, SIGTERM: someone stopped it, nothing went wrong

def crash_signal(rc):
    """The name of the crash signal (SIGSEGV, SIGABRT, ...) that killed the app, or None. RC is the wait status of what
    ajail started: negative when a signal killed it directly, 128 + the signal when bwrap reports it for the app inside."""
    sig = -rc if rc < 0 else rc - 128 if rc > 128 else 0
    try: name = signal.Signals(sig).name
    except ValueError: return None
    return name if name in CRASH_SIGNALS else None

def notify_crash(name, sig):
    """A critical notification that the app crashed (killed by SIG), however long it had been running. Where
    notify-send can do actions, clicking it opens ajail-gui on this app's log."""
    what = f"crashed ({CRASH_SIGNALS[sig]}, {sig})"
    notify_click(f"{name} {what}. Open ajail-gui > Log.", f"{name} {what}. Click to open the log.", "Open log", ["--log", name])

def notify_ended(name, code):
    """A normal-urgency notification that an app started without a terminal (app menu, file manager) ended with an
    error after running for a while. Where it can, clicking it opens the log."""
    what = f"stopped with an error (exit code {code})"
    notify_click(f"{name} {what}. Open ajail-gui > Log.", f"{name} {what}. Click to open the log.", "Open log", ["--log", name], "normal")

def notify_failure(name):
    """Critical notification; clicking it (or its 'Open log' button) opens ajail-gui on this app's log."""
    notify_click(f"{name} failed to start. Open ajail-gui > Log.", f"{name} failed to start. Click to open the log.", "Open log", ["--log", name])

JOURNAL_SOCK = "/run/systemd/journal/stdout"

def journal_tag(ident, sock=JOURNAL_SOCK):
    """Started from a file manager or the app menu, our output goes to the journal under the launcher's name. Reconnect
    those streams, and only those, so lines carry IDENT (the app's name) instead. Anything else is left alone."""
    mine = os.environ.get("JOURNAL_STREAM", "")      # not always set (GNOME's launcher doesn't), so the socket's address counts too
    def is_journal(fd):
        st = os.fstat(fd)
        if mine == f"{st.st_dev}:{st.st_ino}": return True
        if st.st_mode & 0o170000 != 0o140000: return False      # not a socket
        try:
            with socket.socket(fileno=os.dup(fd)) as c: return c.getpeername() == sock
        except OSError: return False
    try:
        fds = [fd for fd in (1, 2) if is_journal(fd)]
        if not fds: return
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with s:
            s.connect(sock)
            s.sendall(f"{ident.replace(chr(10), ' ')}\n\n6\n0\n0\n0\n0\n".encode())      # name, unit, priority, then four flags off
            s.shutdown(socket.SHUT_RD)
            for f in (sys.stdout, sys.stderr):
                try: f.flush()
                except (OSError, ValueError): pass
            for fd in fds: os.dup2(s.fileno(), fd)
        st = os.fstat(fds[0])
        os.environ["JOURNAL_STREAM"] = f"{st.st_dev}:{st.st_ino}"
    except OSError:
        pass

def clean(n):
    return (re.sub(r"[^A-Za-z0-9._-]", "_", n).lstrip("._-")[:64]) or "app"

def sanitize(path):
    n = re.sub(r"\.appimage$", "", os.path.basename(path), flags=re.I)
    s = re.sub(r"[-_](?:v?\d[\w.+~]*|x86[-_]64|amd64|x64|aarch64|arm64|linux\w*)(?=[-_]|$)", "", n, flags=re.I)
    return clean(s or n)
