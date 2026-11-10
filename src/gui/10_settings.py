# ======== 10_settings: Settings and state: config files, ui.conf, path checks, app list, running processes.

def empty():
    return {"s": {}, **{k: [] for k in LISTS}}

def path_for(scope):
    return CONF + "/default.conf" if scope is None else f"{APPS}/{scope}.conf"

def write_cfg(scope, raw):
    out = [f"{k}={int(v)}" for k, v in raw["s"].items()]
    for k in LISTS:
        out += [f"{k}={v}" for v in raw[k]]
    path = path_for(scope)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def run(cmd, timeout=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]} not available")

def dump(arg=""):
    r = run([SELF, "dump"] + ([arg] if arg else []))
    try:
        d = json.loads(r.stdout)
    except ValueError:
        d = {}
    d.setdefault("global", empty())
    return d

def summary(G, A):
    e = {**DEFAULTS, **G["s"], **(A["s"] if A else {})}
    x = {}
    for src in (G, A):
        for it in (src["xdg"] if src else []):
            k, _, m = it.partition(":")
            x[k] = m
    extra = []
    for p in G.get("portal", []) + (A.get("portal", []) if A else []):     # "-NAME" in an app turns one off
        if p.startswith("-"):
            if p[1:] in extra: extra.remove(p[1:])
        elif p not in extra: extra.append(p)
    folders = set(G["allow"] + G["allow_ro"] + (A["allow"] + A["allow_ro"] if A else []))
    n = len(folders) + sum(1 for m in x.values() if m != "none")
    parts = [t for k, t in LABEL.items() if e[k]]
    if n:
        parts.append(f"{n} shared folder" + ("" if n == 1 else "s"))
    if extra:
        parts.append(f"{len(extra)} extra portal" + ("" if len(extra) == 1 else "s"))
    return " · ".join(parts) or "Fully isolated"

def has_comment(v):
    """ajail reads " #..." in a settings line as a comment, so such a value could not be stored faithfully."""
    return bool(re.search(r"(^|\s)#", v))

def path_ok(p):
    p = os.path.realpath(p)
    if "\n" in p or has_comment(p) or not os.path.exists(p):
        return None
    if p.startswith(HOME + "/"):
        return None if p[len(HOME) + 1:].split("/")[0].startswith(".") else p
    return p if any(p.startswith(b) for b in OUTSIDE) else None

def ui_read():
    d = {}
    try:
        for ln in open(UICONF):
            k, _, v = ln.strip().partition("=")
            if k:
                d[k] = v
    except OSError:
        pass
    return d

def ui_set(key, value):
    """Change one key in ui.conf and keep the others."""
    d = ui_read()
    d[key] = value
    os.makedirs(CONF, exist_ok=True)
    tmp = UICONF + ".tmp"
    with open(tmp, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in d.items()))
    os.replace(tmp, UICONF)

def get_open_mode():
    v = ui_read().get("open_mode")
    return v if v in ("ask", "run") else "ask"

def set_open_mode(v):
    ui_set("open_mode", v)

# What happens to the AppImage file after installing. "ask" (the default) asks once the install is done, so nothing is
# trashed before you have seen that it worked. "move" only exists with "Use less disk space" (experimental).
ORIGINAL = {"ask": "Ask me", "trash": "Move to Trash", "keep": "Keep it", "move": "Move into app folder"}

def original_choices(mount):
    return ["ask", "trash", "keep"] + (["move"] if mount else [])

def original_hint(choice):
    """A short note under the dropdown for the choices that need one."""
    return {"ask": "Asks after each install. You can restore a trashed file from the Trash.",
            "move": "No duplicate. Experimental: the file then only exists inside AppImage Jail."}.get(choice, "")

def get_install_original():
    """The remembered choice; "ask" unless the person picked something else."""
    v = ui_read().get("install_original")
    return v if v in ORIGINAL else "ask"

def app_open_mode(name):
    """'ask' or 'run' for double-clicking this app's AppImage: its own setting, else the global default."""
    v = ui_read().get(f"open_mode.{name}")
    return v if v in ("ask", "run") else get_open_mode()

def app_mount(name):
    """Whether this app uses 'Use less disk space': its own choice (mount.NAME), else the global default (mount)."""
    ui = ui_read()
    v = ui.get(f"mount.{name}")
    if v not in ("0", "1"):
        v = ui.get("mount")
    return v == "1"

def app_names():
    names = set()
    for d, ext in ((APPS, ".conf"), (HOMES, ""), (INSTALLED, "")):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if ext and not f.endswith(ext):
                    continue                # e.g. NAME.machine-id belongs to an app but is not an app
                n = f[:-len(ext)] if ext else f
                if NAME_RE.match(n):
                    names.add(n)
    return sorted(names)

def live_ajail(pid):
    """True if this PID is really an ajail process, so a stale pid file never makes Stop signal a reused PID."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"ajail" in f.read()
    except OSError:
        return False

def running_pids(name=None):
    out = {}
    if os.path.isdir(RUNNING):
        for f in os.listdir(RUNNING):
            if f.isdigit() and live_ajail(f):
                try:
                    n = open(f"{RUNNING}/{f}").read().strip()
                except OSError:
                    continue
                out.setdefault(n, []).append(int(f))
    return out if name is None else out.get(name, [])

def ui_drop(keys):
    """Remove keys from ui.conf and keep the others."""
    d = ui_read()
    if not any(k in d for k in keys):
        return
    for k in keys:
        d.pop(k, None)
    tmp = UICONF + ".tmp"
    with open(tmp, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in d.items()))
    os.replace(tmp, UICONF)
