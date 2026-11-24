# ======== 10_settings: Settings and state: config files, ui.conf, path checks, app list, running processes.

def empty():
    return {"s": {}, **{k: [] for k in LISTS}}

TALK_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*(\.\*)?")     # same as ajail's: no empty parts, so "a..b" is not a name

def talk_ok(n):
    """Same rule as ajail's talk_ok: a plain name, or NAME.* with at least three parts before the .* (so 'org.*' and
    'org.freedesktop.*' are refused). Keep the two in step; test_parse.py compares them."""
    return bool(TALK_RE.fullmatch(n)) and (not n.endswith(".*") or n.count(".") >= 3)

def path_for(scope):
    return CONF + "/default.conf" if scope is None else f"{APPS}/{scope}.conf"

def write_cfg(scope, raw):
    out = [f"{k}={v if isinstance(v, str) else int(v)}" for k, v in raw["s"].items()]      # gtk_portal holds text (auto, 1, 0); the window has no row for it but must keep it
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

_WL = []
def wayland_restricted():
    """Can this desktop make a restricted Wayland socket for an app (wp_security_context_v1)? Asked once, from ajail itself, so the
    answer comes from the same code that starts the apps. Unknown counts as no."""
    if not _WL: _WL.append(run([SELF, "wl-check"], timeout=5).stdout.strip() == "1")
    return _WL[0]

def resolved(v):
    """A default of None is automatic: for 'Wayland only if restricted' that means on where this desktop can restrict apps."""
    return wayland_restricted() if v is None else v

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
    extra = [p for p in extra if p not in DEFAULT_PORTALS]
    folders = set(G["allow"] + G["allow_ro"] + (A["allow"] + A["allow_ro"] if A else []))
    n = len(folders) + sum(1 for m in x.values() if m != "none")
    parts = [t for k, t in LABEL.items() if e[k]]
    if n:
        parts.append(f"{n} shared folder" + ("" if n == 1 else "s"))
    if extra:
        parts.append(f"{len(extra)} extra portal" + ("" if len(extra) == 1 else "s"))
    return " · ".join(parts) or "Fully isolated"

def open_path(path):
    """Open PATH with xdg-open. None when it started, else the reason it didn't. The process is reaped when it ends: no zombie."""
    try: p = subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e: return e.strerror or str(e)
    threading.Thread(target=p.wait, daemon=True).start()

def has_comment(v):
    """ajail reads " #..." in a settings line as a comment, so such a value could not be stored faithfully."""
    return bool(re.search(r"(^|\s)#", v))

ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")      # same rule as ajail's env= handling

def parse_env_entry(text):
    """(item, problem) for what is typed in a variable row: NAME=value. The problem is None when it can be stored."""
    name, eq, val = text.strip().partition("=")
    name, val = name.strip(), val.strip()
    if not eq: return None, "Type NAME=value, for example SDL_VIDEODRIVER=x11."
    if not ENV_NAME_RE.fullmatch(name): return None, "A variable name has letters, digits and _, and doesn't start with a digit."
    if has_comment(val): return None, "A value can't contain ' #': ajail would read the rest as a comment."
    return f"{name}={val}", None

def env_replace(items, item, old=None):
    """ITEMS with the variable ITEM (NAME=value) stored: in place of OLD (the entry a row showed) when given, else where the
    same name already stands, else at the end. One value per variable name: other entries with that name are dropped."""
    name = item.partition("=")[0]
    out, placed = [], False
    for v in items:
        if v == old or v.partition("=")[0] == name:
            if not placed: out.append(item); placed = True
            continue
        out.append(v)
    return out if placed else out + [item]

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

def proc_start(pid):
    """When PID started: field 22 of /proc/PID/stat (clock ticks since boot, as text), or None. A reused PID gets another one."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            rest = f.read().rsplit(b")", 1)[1].split()      # field 2, the name, may hold spaces and ')'
        return rest[19].decode()                            # rest[0] is field 3
    except (OSError, IndexError, UnicodeDecodeError):
        return None

def pid_app(pid):
    """The app name in RUNNING/PID, if that file was written by the process that is still running under this PID.
    The file holds "NAME\nSTARTTIME". Old files (the name only) can't be checked, so they count for nothing."""
    try:
        with open(f"{RUNNING}/{pid}") as f:
            name, _, start = f.read().partition("\n")
    except OSError:
        return None
    start = start.strip()
    return name.strip() if start and start == proc_start(pid) and live_ajail(pid) else None

def stop_app(name):
    """SIGTERM every running process of the app NAME. A PID can be reused between listing the processes and signalling
    one, so each PID is first pinned with a pidfd and only then checked against its pid file (see ajail's stop_app)."""
    use_fd = hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")
    for pid in running_pids(name):
        fd = None
        try:
            if use_fd:
                try:
                    fd = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                except OSError:
                    pass                                  # no pidfd on this kernel: plain kill below
            if pid_app(str(pid)) != name:
                continue
            if fd is None:
                os.kill(pid, signal.SIGTERM)
            else:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
        except OSError:
            pass
        finally:
            if fd is not None:
                os.close(fd)

def running_pids(name=None):
    out = {}
    if os.path.isdir(RUNNING):
        for f in os.listdir(RUNNING):
            if f.isdigit():
                n = pid_app(f)
                if n:
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

# Hints for the Troubleshooting page: when an app's last run ended with an error and its log matches a known failure, suggest
# the one setting that usually fixes it. Each entry is (pattern, setting, wanted value, title, why, only while these hold).
# A hint is shown only if the setting is not already as wanted. Nothing is ever changed without a click.
LOG_HINTS = [
    (r"no usable sandbox|suid sandbox|setuid sandbox|failed to move to a new (user )?namespace|clone.{0,40}operation not permitted",
     "userns", 1, "Allow the app's own sandbox",
     "The app tried to create its own sandbox and could not. Electron and Chromium apps need this.", {}),
    (r"read-only file system",
     "app_writable", 1, "Let the app save into its own folder",
     "The app tried to write inside its own files, which are read-only. Needs bubblewrap 0.11 or newer.", {}),
    (r"libegl warning|mesa: error|/dev/dri|failed to create (an )?(egl|gl)|no (suitable|available|compatible) (gpu|vulkan|adapter)",
     "gpu", 1, "Turn on graphics acceleration", "The app could not find a graphics device.", {}),
    (r"libegl warning|mesa: error|/dev/dri|failed to create (an )?(egl|gl)|no (suitable|available|compatible) (gpu|vulkan|adapter)",
     "gpu_render_only", 0, "Turn off \"Graphics: render only\"",
     "The app may need a graphics device that this mode hides.", {"gpu": 1}),
    (r"failed to connect to (the )?wayland|wayland.{0,30}(connection|display).{0,30}(fail|refused)",
     "wayland", 1, "Turn on Wayland", "The app looked for Wayland, which is off. Better than X11 where it works.", {}),
    (r"cannot open display|could not connect to display|unable to open display|platform plugin .?xcb|failed to connect to (the )?wayland",
     "x11", 1, "Turn on X11 compatibility", "The app looks for X11. This weakens isolation, so use it only for this app.", {}),
    (r"temporary failure in name resolution|name or service not known|network is unreachable",
     "net", 1, "Allow internet", "The app tried to use the network.", {}),
    (r"/dev/video\d|no (such )?(video )?(capture )?device.{0,30}camera|camera.{0,30}(not found|no device)",
     "camera", 1, "Allow the camera", "The app tried to open a camera.", {}),
    # A wgpu app whose shader needs a feature the chosen graphics adapter lacks. OpenGL keeps the card and often gets past
    # it; for iced apps the processor renderer is the second choice. Both are listed, OpenGL first.
    (r"wgpu error: validation error[\s\S]*requires capability",
     "env", "WGPU_BACKEND=gl", "Draw with OpenGL instead of Vulkan (a guess)",
     "The app's graphics library chose a Vulkan device that lacks a feature the app needs. OpenGL keeps the graphics card and often works.", {}),
    (r"wgpu error: validation error[\s\S]*iced_wgpu|iced_wgpu[\s\S]*wgpu error: validation error",
     "env", "ICED_BACKEND=tiny-skia", "If that does not help: draw on the processor (a guess)",
     "The app draws with iced, which can skip the graphics card entirely. Slower, but it needs nothing from the card.", {}),
    (r"32-bit app and 'allow 32-bit apps' is off",
     "allow_32bit", 1, "Allow 32-bit apps",
     "The app is 32-bit, which is blocked while this is off. Turning it on weakens the network-socket filter for 32-bit apps, so use it only for this app.", {}),
    # A silent crash (segfault, abort, trap) while user namespaces are off: Chrome, Chromium and Electron die like this when
    # they cannot make their own sandbox, often with only a core dump and no message. Shown only while "userns" is off.
    (r"\] EXIT code=(13[2-6]|139|159)\b",
     "userns", 1, "Allow the app's own sandbox (a guess)",
     "The app crashed and left no reason in the log. Chrome, Chromium and Electron crash like this when they cannot create their own sandbox, and user namespaces are off for this app.", {"userns": 0}),
    # A guess, last so any specific X11 message above wins: a crash right after start, with nothing in the log saying why
    # (installers often send stderr to /dev/null). A GUI that only speaks X11 dies like this.
    (r"aborted \(core dumped\)|\] EXIT code=134\b",
     "x11", 1, "Try X11 compatibility (a guess)",
     "The app crashed right after starting and left no reason in the log. It may be a GUI that needs X11. This weakens isolation, so use it only for this app.", {}),
]

HINT_LISTS = ("env",)        # lists a hint may add to; the button appends NAME=value to the app's own file

def effective_lists(G, A):
    """The list values an app runs with: default.conf and the app's own file add up."""
    return {k: list(G.get(k, [])) + (list(A.get(k, [])) if A else []) for k in HINT_LISTS}

def effective_switches(G, A):
    """The switch values an app runs with: built-in defaults, then default.conf, then the app's own file."""
    return {**DEFAULTS, **G["s"], **(A["s"] if A else {})}

def desktop_hidden(key, on, desktop=None):
    """True if KEY is for another desktop than the one running (XDG_CURRENT_DESKTOP, e.g. "KDE" or "ubuntu:GNOME") and is not ON.
    Nothing is hidden when the desktop is not GNOME or KDE or is not known, and a switch that is on always stays visible, so it can be turned off."""
    want = DESKTOP_ONLY.get(key)
    have = {w.upper() for w in re.split(r"[:;]", os.environ.get("XDG_CURRENT_DESKTOP", "") if desktop is None else desktop)} & {"GNOME", "KDE"}
    return bool(want and have and want not in have and not on)

def folded_desktops(keys):
    """The desktops (as DESKTOP_ONLY names them) that have more than DESKTOP_FOLD of KEYS, the rows that are shown. Few rows are easier to reach left open."""
    count = {}
    for k in keys:
        if k in DESKTOP_ONLY: count[DESKTOP_ONLY[k]] = count.get(DESKTOP_ONLY[k], 0) + 1
    return {d for d, n in count.items() if n > DESKTOP_FOLD}

def last_start(lines):
    """Index of the most recent START line, or -1 if there is none."""
    for i in range(len(lines) - 1, -1, -1):
        if "] START allow=[" in lines[i]:
            return i
    return -1

def latest_run_lines(lines):
    """The most recent run with its START line: from the last START to the end (everything, when no START is logged)."""
    return lines[max(last_start(lines), 0):]

def last_run(lines):
    """(lines of the most recent run, its exit code or None). A run starts at a START line and ends at an EXIT line."""
    run_lines = lines[last_start(lines) + 1:]
    code = None
    for ln in reversed(run_lines):
        m = re.search(r"\] EXIT code=(-?\d+)", ln)
        if m:
            code = int(m.group(1))
            break
    return run_lines, code

def log_hints(lines, current, lists=None):
    """Suggestions for an app, as [(setting, wanted value, title, why)]. Only for a last run that ended with an error;
    LINES are that app's log lines, CURRENT its effective switches (see effective_switches), LISTS its effective lists
    (see effective_lists). A hint on a list key (env) wants "NAME=value"."""
    run_lines, code = last_run(lines)
    if code in (None, 0):
        return []
    text = "".join(run_lines[-300:])[-200_000:]
    out, seen = [], set()
    for pat, key, want, title, why, needs in LOG_HINTS:
        if (key, want) in seen:
            continue
        if key in HINT_LISTS:                      # already there (a variable counts when it is set at all: chosen on purpose)
            have = (lists or {}).get(key, [])
            if want in have or any(h.partition("=")[0] == want.partition("=")[0] for h in have):
                continue
        elif current.get(key) == want:
            continue
        if any(current.get(k) != v for k, v in needs.items()):
            continue
        if re.search(pat, text, re.I):
            out.append((key, want, title, why))
            seen.add((key, want))
    return out

# Colors for the Log page. Pure parsing here; the window layer maps each kind to a text color.
LOG_LINE_RE = re.compile(r"^(\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] )?(\[[^\]]+\] )?(.*?)(\n?)$", re.S)
LOG_KINDS = [      # first match wins: (pattern, kind)
    (r"^EXIT code=0\b", "exit_ok"),
    (r"^EXIT code=", "exit_err"),
    (r"^START\b", "info"),
    (r"^(first run|installed|uninstalled)\b", "ok"),
    (r"^WARNING\b|\bwarning\b", "warn"),
    (r"^ALSA lib\b", "dim"),
    (r"\bpanicked\b|\bfatal\b|\berror\b|\bfailed\b|\bcannot\b|\bdenied\b|not permitted|^Caused by|^note: ", "err"),
]

def log_line_parts(line):
    """Split one log line into (timestamp, [name] tag, body, kind, newline); kind is one of exit_ok, exit_err, info, ok, warn, dim,
    err or '' (plain). Timestamp and tag may be empty: an app's own output has no timestamp."""
    ts, name, body, nl = LOG_LINE_RE.match(line).groups()
    kind = ""
    for pat, k in LOG_KINDS:
        if re.search(pat, body, re.I):
            kind = k
            break
    return ts or "", name or "", body, kind, nl

START_RE = re.compile(r"^START allow=\[([^\]]*)\] limits=\[([^\]]*)\]")
DIVIDER = "\u2500" * 32

def switch_title(key):
    """What the log calls a switch: its title in the settings window, else its key."""
    for _g, _d, items in SECTIONS:
        for k, t, _s in items:
            if k == key: return t
    return key

def start_changes(allowed, limits=""):
    """What a START line says, shortened to the switches that differ from the built-in defaults: "+X11 -Internet", or "(defaults)".
    ALLOWED are the names of the switches that were on; LIMITS is the limits text of the line, shown only when something is limited.
    wayland_strict has no fixed default (it is automatic), so it is left out."""
    on = set(allowed)
    diff = [f"+{switch_title(k)}" for k, d in DEFAULTS.items() if d is not None and k in on and not d]
    diff += [f"-{switch_title(k)}" for k, d in DEFAULTS.items() if d and k not in on]
    if limits: diff.append(f"limits {limits}")
    return " ".join(diff) or "(defaults)"

def short_ts(ts, today):
    """A timestamp "[2026-11-19 10:41:00] " without its date when it is TODAY's ("YYYY-MM-DD")."""
    return "[" + ts[len(today) + 2:] if ts.startswith(f"[{today} ") else ts

def log_rows(lines, today=None):
    """LINES laid out for the log page: one row per line, each a list of (text, tag) chunks, tag "" for plain text, without the newline.
    Today's timestamps lose their date, a START line shows only what differs from the defaults (dimmed), and a rule
    comes before every run except the first row, so runs do not run together."""
    today = today or time.strftime("%F")
    rows = []
    for line in lines:
        ts, name, body, kind, _nl = log_line_parts(line)
        chunks = [(short_ts(ts, today), "ts"), (name, "name")]
        m = START_RE.match(body)
        if m:
            if rows: rows.append([(DIVIDER, "dim")])
            chunks += [("START", "info"), (" " + start_changes([k for k in m.group(1).split(",") if k], m.group(2)), "dim")]
        else:
            chunks.append((body, kind))
        rows.append([c for c in chunks if c[0]])
    return rows
