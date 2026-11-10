# ======== 30_host: Host integration: desktop settings read for the sandbox, resource limits, stale-file sweep.

# Desktop settings the sandbox can't read: the title-bar button layout (minimize, maximize, close) and the cursor theme
# and size. ajail runs outside the sandbox, so it reads them on the host and gives the app only these settings, as a
# GSettings key file (GTK reads them from GSettings, and that beats a settings.ini). The app is pointed at it with
# GSETTINGS_BACKEND=keyfile. Nothing is hardcoded: no desktop setting found, nothing is shared.
LAYOUT_KEYS = (("org.gnome.desktop.wm.preferences", "button-layout"),     # GNOME (stored in dconf)
               ("org.cinnamon.muffin", "button-layout"),
               ("org.mate.Marco.general", "button-layout"))
LAYOUT_RE = re.compile(r"[a-z,:_]{0,64}")       # only button names, commas and the colon; anything else is ignored
CURSOR_SCHEMA = "org.gnome.desktop.interface"
CURSOR_THEME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,63}")    # a folder name: no '/', no quotes, no leading dot
CURSOR_SIZE_RE = re.compile(r"[0-9]{1,3}")
CURSOR_SIZE_MAX = 256

def gsettings_get(schema, key):
    """The raw gsettings value as text, or None (no gsettings, no such key, error, timeout)."""
    gs = shutil.which("gsettings")
    if not gs: return None
    try: r = subprocess.run([gs, "get", schema, key], capture_output=True, text=True, timeout=3)
    except Exception: return None
    return r.stdout.strip() if r.returncode == 0 else None

def gsettings_string(raw):
    """The text of a plain gsettings string 'like this', else None. No escapes, no other quote style."""
    if raw is None or len(raw) < 2 or raw[0] != "'" or raw[-1] != "'": return None
    return raw[1:-1]

def gtk_ini_layout():
    """gtk-decoration-layout from your own GTK settings.ini, or None."""
    for ver in ("gtk-4.0", "gtk-3.0"):
        try:
            with open(f"{HOME}/.config/{ver}/settings.ini") as f:
                for ln in f:
                    k, _, v = ln.partition("=")
                    if k.strip() == "gtk-decoration-layout" and LAYOUT_RE.fullmatch(v.strip()): return v.strip()
        except (OSError, UnicodeDecodeError):
            pass
    return None

def host_button_layout():
    """The button layout from the desktop settings (gsettings/dconf), else from your own GTK settings.ini, else None."""
    for schema, key in LAYOUT_KEYS:
        v = gsettings_get(schema, key)
        v = v.strip("'\"") if v is not None else None
        if v is not None and LAYOUT_RE.fullmatch(v): return v
    return gtk_ini_layout()

def host_cursor():
    """(theme, size) of the host's cursor from gsettings; None for whatever is missing or doesn't pass the checks.
    Size 0 means 'default' and counts as unset."""
    theme = gsettings_string(gsettings_get(CURSOR_SCHEMA, "cursor-theme"))
    if theme is not None and not CURSOR_THEME_RE.fullmatch(theme): theme = None
    size = gsettings_get(CURSOR_SCHEMA, "cursor-size")
    if size is not None and CURSOR_SIZE_RE.fullmatch(size) and 0 < int(size) <= CURSOR_SIZE_MAX: size = int(size)
    else: size = None
    return theme, size

def gsettings_keyfile_text():
    """Text of the key file: just the host's button layout and cursor theme/size. None if there is nothing to share.
    Always written under GNOME's schemas, which are the ones GTK reads, whichever desktop the values came from."""
    out = []
    layout = host_button_layout()
    if layout is not None: out.append(f"[org/gnome/desktop/wm/preferences]\nbutton-layout='{layout}'\n")
    theme, size = host_cursor()
    cur = ([f"cursor-theme='{theme}'\n"] if theme is not None else []) + ([f"cursor-size={size}\n"] if size is not None else [])
    if cur: out.append("[org/gnome/desktop/interface]\n" + "".join(cur))
    return "\n".join(out) or None

def make_gsettings_keyfile(d):
    """GSettings key file holding just the host's button layout and cursor, written into the per-run folder D.
    None if there is nothing to share."""
    text = gsettings_keyfile_text()
    if text is None: return None
    os.makedirs(d, mode=0o700, exist_ok=True)
    p = f"{d}/gsettings-keyfile"
    with open(p, "w") as f: f.write(text)
    return p

def limit_props(cfg):
    props = []
    if cfg["mem_max"]: props += ["-p", f"MemoryMax={cfg['mem_max']}M"]
    if cfg["cpu_max"]: props += ["-p", f"CPUQuota={cfg['cpu_max']}%"]
    if cfg["tasks_max"]: props += ["-p", f"TasksMax={cfg['tasks_max']}"]
    return props

def limit_prefix(cfg, name):
    """systemd-run --user --scope wrapper that applies the memory, CPU and process limits. Without systemd the app
    still runs, just without limits (a warning is shown): limits protect your session, they are not a privacy boundary."""
    props = limit_props(cfg)
    if not props: return []
    sr = shutil.which("systemd-run")
    ok = False
    if sr:
        probe = [sr, "--user", "--scope", "--quiet"] + props + ["--", "/usr/bin/true"]
        try: ok = subprocess.run(probe, capture_output=True, timeout=10).returncode == 0
        except Exception: ok = False
    if not ok:
        print("ajail: resource limits are not available here (needs systemd --user with cgroups); running without them",
              file=sys.stderr)
        log("resource limits unavailable", name)
        return []
    return [sr, "--user", "--scope", "--quiet", f"--unit=ajail-{name}-{os.getpid()}"] + props + ["--"]

def sweep_runtime():
    """Remove per-run files and sockets left behind by ajail processes that died."""
    d = f"{runtime_dir()}/ajail"
    try: entries = os.listdir(d)
    except OSError: return
    for e in entries:
        pid = e.split(".", 1)[0]
        if not pid.isdigit() or os.path.exists(f"/proc/{pid}"): continue
        p = f"{d}/{e}"
        if os.path.isdir(p) and not os.path.islink(p): shutil.rmtree(p, ignore_errors=True)
        elif e.endswith(".bus"):
            try: os.unlink(p)
            except OSError: pass
