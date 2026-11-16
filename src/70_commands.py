# ======== 70_commands: Command-line commands and entry point.

def cmd_install(args):
    usage = "usage: ajail install FILE.AppImage [--mount [--move]] [--trash]"
    opts, files = split_opts(args, ("--trash", "--mount", "--move"), usage)
    trash, mount, move = "--trash" in opts, "--mount" in opts, "--move" in opts
    if len(files) != 1: die(usage)
    if move and not mount: die("--move only works together with --mount")
    if move and trash: die("use either --move or --trash, not both")
    guard()
    if mount: need_squashfuse()
    app = os.path.realpath(files[0])
    if not os.path.isfile(app): die(f"not a file: {files[0]}")
    off = appimage_offset(app)
    name = sanitize(app)
    dest = f"{INSTALLED}/{name}"
    kind = "mount" if mount else "root"
    moved = False
    # A compressed install keeps the AppImage itself. By default it is copied (the original stays). With --move it is
    # moved into ajail's folder instead (no duplicate). Symlinks, removable or network drives and read-only folders are
    # always copied, even with --move.
    movable = move and can_move(app) and not os.path.islink(files[0])
    if installed_stamp(name) == file_stamp(app) and installed_kind(name) == kind:
        print(f"{name} is already installed" + ("" if mount else ", refreshing its launcher"))
        if not mount: install_launcher(name, f"{dest}/root")
    else:
        if running(name): die(f"{name} is running. Close it first")
        root = ensure_extracted(app, name, off)
        if not os.path.lexists(f"{root}/AppRun"): die("no AppRun found inside this AppImage")
        check_identity(name, root)
        os.makedirs(INSTALLED, mode=0o700, exist_ok=True)
        rm_tree(dest)
        if mount:
            os.makedirs(dest, mode=0o700)
            stamp = file_stamp(app)                    # taken first: after a move the original is gone
            try:
                moved = place_image(app, f"{dest}/image", movable)
            except OSError as e:
                rm_tree(dest)
                die(f"could not store the AppImage: {e.strerror or e}")
            open(f"{dest}/offset", "w").write(str(off))
            open(f"{dest}/stamp", "w").write(stamp)
            if moved: open(f"{dest}/origin", "w").write(app)     # lets 'ajail restore' put it back
            install_launcher(name, root)
            rm_tree(f"{EXTRACT}/{name}")
        else:
            os.replace(f"{EXTRACT}/{name}", dest)
            install_launcher(name, f"{dest}/root")
    os.makedirs(f"{CONF}/apps", exist_ok=True)
    open(f"{CONF}/apps/{name}.conf", "a").close()
    try:                                              # where the file came from, so ajail-gui can offer to trash it
        os.makedirs(SOURCES, exist_ok=True)
        open(f"{SOURCES}/{name}", "w").write(app)
    except OSError:
        pass
    log("installed (compressed" + (", moved)" if moved else ")") if mount else "installed", name)
    print(f"Installed {name}" + (" (compressed, mounted at launch)" if mount else ""))
    if mount and moved: print("Moved the AppImage into ajail")
    elif mount and move: print("ajail: copied the AppImage instead of moving it (removable or network drive, symlink, "
                               "or a folder you can't write to); the original was kept", file=sys.stderr)
    if trash:
        if shutil.which("gio") and subprocess.run(["gio", "trash", app]).returncode == 0:
            print("Moved the AppImage to the Trash")
        else:
            print("ajail: could not move the AppImage to the Trash", file=sys.stderr)

def cmd_uninstall(args):
    usage = "usage: ajail uninstall NAME [--purge] [--discard-image]"
    opts, names = split_opts(args, ("--purge", "--discard-image"), usage)
    purge, discard = "--purge" in opts, "--discard-image" in opts
    if len(names) != 1: die(usage)
    name = clean(names[0])
    if running(name): die(f"{name} is running. Close it first")
    d = f"{INSTALLED}/{name}"
    if os.path.exists(f"{d}/origin") and os.path.isfile(f"{d}/image") and not discard:
        die(f"{name}'s AppImage was moved into ajail and uninstalling would delete it. Run 'ajail restore {name}' to put "
            f"it back (this also uninstalls), 'ajail restore {name} --to FOLDER' to put it in another folder, or add "
            f"--discard-image to delete it")
    rm_tree(f"{INSTALLED}/{name}")
    try: os.unlink(f"{APPLICATIONS}/ajail-{name}.desktop")
    except OSError: pass
    remove_icons(name)
    moved = layer_failed = False
    if purge:
        home = f"{HOMES}/{name}"
        if not os.path.lexists(home): moved = True
        elif os.path.isdir(home) and not os.path.islink(home):
            moved = trash_path(home)      # the data goes to the Trash, never deleted outright
            if not moved: print(f"ajail: could not move {home} to the Trash, so its data was kept", file=sys.stderr)
        if moved:                          # settings go with the data; if the data stayed, so do they
            rm_tree(f"{EXTRACT}/{name}"); forget_app_ui(name)
            for p in (f"{CONF}/apps/{name}.conf", f"{CONF}/ids/{name}", f"{SOURCES}/{name}"):
                try: os.unlink(p)
                except OSError: pass
    if purge:      # the writable layer (e.g. game saves) goes to the Trash too, like the private home
        layer = f"{LAYERS}/{name}"
        if os.path.islink(layer): os.unlink(layer)
        elif os.path.isdir(layer) and not trash_layer(layer):
            layer_failed = True
            print(f"ajail: could not move {layer} to the Trash, so it was kept", file=sys.stderr)
    refresh_desktop_db()
    log("uninstalled" + (" and purged" if moved else ""), name)
    print(f"Uninstalled {name}" + (" and moved its data to the Trash" if moved else ", its data was kept"))
    if layer_failed: sys.exit(1)       # the app is uninstalled, but say so loudly: its writable layer is still on disk

def cmd_restore(args):
    """Undo a compressed install that moved the AppImage: put the file back where it came from and uninstall the app
    (its data and settings are kept)."""
    keep, dest = "--keep" in args, None
    if "--to" in args:
        i = args.index("--to")
        if i + 1 >= len(args): die("--to needs a folder")
        dest = args[i + 1]; del args[i:i + 2]
    args = [x for x in args if x != "--keep"]
    if len(args) != 1: die("usage: ajail restore NAME [--keep] [--to FOLDER]")
    name = clean(args[0])
    if running(name) and not keep: die(f"{name} is running. Close it first")
    d = f"{INSTALLED}/{name}"
    try: origin = open(f"{d}/origin").read().strip()
    except OSError: die(f"{name} was not moved into ajail, so there is nothing to put back")
    img = f"{d}/image"
    if not os.path.isfile(img): die(f"{name} has no stored AppImage")
    target = os.path.join(os.path.realpath(dest), os.path.basename(origin)) if dest else origin     # --to: another folder
    if not os.path.isabs(target) or not os.path.isdir(os.path.dirname(target)):
        die("the folder " + ("does not exist" if dest else "it came from no longer exists") + f": {os.path.dirname(target)}"
            + ("" if dest else f". Use 'ajail restore {name} --to FOLDER' to pick another one"))
    if os.path.lexists(target): die(f"{target} already exists, so the AppImage was not put back")
    if keep:
        # Put a copy back and leave the app installed (it still needs its stored image). Nothing is left to protect, so
        # the marker goes and a later uninstall no longer refuses.
        try:
            place_image(img, target, False)
            os.chmod(target, os.stat(img).st_mode & 0o777)
            os.unlink(f"{d}/origin")
        except OSError as e: die(f"could not put the AppImage back: {e.strerror or e}")
        print(f"Put a copy of the AppImage back at {target}; {name} stays installed")
        return
    try: shutil.move(img, target)
    except OSError as e: die(f"could not put the AppImage back: {e.strerror or e}")
    print(f"Put the AppImage back at {target}")
    try: os.unlink(f"{d}/origin")             # the file is back: uninstalling no longer needs to protect it
    except OSError: pass
    cmd_uninstall([name])

def ui_get(key):
    try:
        for ln in open(f"{CONF}/ui.conf"):
            k, _, v = ln.strip().partition("=")
            if k == key: return v
    except OSError:
        pass
    return None

def open_mode(name=None):
    """'ask' or 'run' for double-clicking an AppImage: the app's own setting (open_mode.NAME), else the global one."""
    for key in ((f"open_mode.{name}",) if name else ()) + ("open_mode",):
        v = ui_get(key)
        if v in ("ask", "run"): return v
    return "ask"

def forget_app_ui(name):
    """Drop an app's remembered open/mount choices from ui.conf."""
    try:
        lines = open(f"{CONF}/ui.conf").read().splitlines()
        keep = [l for l in lines if l.partition("=")[0] not in (f"open_mode.{name}", f"mount.{name}")]
        if keep != lines: open(f"{CONF}/ui.conf", "w").write("".join(l + "\n" for l in keep))
    except OSError:
        pass

def open_file(arg, extra):
    """Entry point for double-click and 'Open with': offer Install or Run once, unless launched from a terminal."""
    gui = f"{os.path.dirname(SELF)}/ajail-gui"
    app = os.path.realpath(arg) if os.path.isfile(arg) else None
    name = sanitize(app) if app else None
    from_desktop = not (sys.stdin.isatty() or sys.stdout.isatty()) and bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    if (app and open_mode(name) == "ask" and not extra and os.path.exists(gui) and from_desktop
            and not (os.environ.get("AJAIL_ACTIVE") or os.geteuid() == 0)):
        if installed_stamp(name) == file_stamp(app): return run_installed(name, extra)
        p = subprocess.Popen([gui, "--install", app], start_new_session=True)
        try:
            if p.wait(2) != 0: run_app(arg, extra)
        except subprocess.TimeoutExpired:
            pass
        return
    # No prompt for this app. From the desktop: use its installed copy if it is the same file, otherwise run the file
    # unpacked in the sandbox. "Use less disk space" only applies to installs. Terminal launches never prompt.
    if app and from_desktop:
        if installed_stamp(name) == file_stamp(app): return run_installed(name, extra)
    run_app(arg, extra)

def need_root(cmd):
    if os.geteuid() != 0:
        os.execvp("pkexec", ["pkexec", SELF, cmd])

def cmd_on():
    need_root("on")
    if not os.path.exists(SELF) or os.stat(SELF).st_uid != 0: die(f"{SELF} must exist and be owned by root")
    for t in ("bwrap", "unsquashfs"):
        if not shutil.which(t): die(f"{t} missing: " + install_hint("bubblewrap", "squashfs-tools"))
    open("/etc/binfmt.d/appimage-jail.conf", "w").write(RULE + "\n")
    subprocess.run(["systemctl", "restart", "systemd-binfmt"], check=True)
    print("ON: AppImages now run sandboxed")

def cmd_off():
    need_root("off")
    try: open(f"{BM}/appimage-jail", "w").write("-1")
    except OSError: pass
    try: os.unlink("/etc/binfmt.d/appimage-jail.conf")
    except OSError: pass
    print("OFF")

def cmd_check():
    def line(k, m): print(f"{k:5} {m}")
    def yes(c, ok, no, lvl="INFO"): line("OK" if c else lvl, ok if c else no)
    yes(shutil.which("bwrap"), "bubblewrap installed", "bubblewrap missing", "WARN")
    yes(os.path.exists(UNSQ), "squashfs-tools installed", "squashfs-tools missing", "WARN")
    r = subprocess.run(["bwrap"] + BASE + ["--unshare-all", "/usr/bin/true"], capture_output=True)
    line("OK" if r.returncode == 0 else "WARN", "unprivileged sandboxes work" if r.returncode == 0
         else "bwrap test failed: " + r.stderr.decode(errors="replace").strip()[:150])
    if r.returncode != 0 and apparmor_userns_hint(): line("WARN", apparmor_userns_hint())
    yes(wayland_path(), "Wayland socket found", "no Wayland socket visible to this process")
    if wayland_path():
        yes(wl_supported(wayland_path()), "compositor can restrict apps (security context): apps get a restricted Wayland socket",
            "compositor has no wp_security_context_v1: apps get the full Wayland socket ('Wayland only if restricted' would turn Wayland off)")
    yes(os.path.exists("/dev/dri"), "GPU devices (/dev/dri) present", "no /dev/dri")
    if glob.glob("/dev/nvidia*"): line("OK", "NVIDIA devices will be shared when Graphics is on")
    yes(shutil.which("xdg-dbus-proxy"), "xdg-dbus-proxy installed (notifications, portals, tray)",
        "xdg-dbus-proxy missing: " + install_hint("xdg-dbus-proxy"))
    yes(seccomp_file(), "seccomp filter available", "seccomp filter not available on this CPU architecture")
    yes(bwrap_has("--disable-userns"), "bubblewrap supports disabling nested sandboxes",
        "bubblewrap too old for 'Allow the app's own sandbox = off'")
    yes(bwrap_has("--hostname"), "bubblewrap can hide the computer name", "bubblewrap too old for 'Hide personal details' to hide the computer name")
    yes(shutil.which("pasta"), "pasta installed ('Keep localhost private' available)",
        "pasta missing, so 'Keep localhost private' cannot be used: " + install_hint("passt"))
    sr = shutil.which("systemd-run")
    lim = bool(sr) and subprocess.run([sr, "--user", "--scope", "--quiet", "-p", "TasksMax=infinity", "--", "/usr/bin/true"],
                                      capture_output=True).returncode == 0
    yes(lim, "resource limits available (systemd --user)", "resource limits unavailable: needs systemd --user with cgroups")
    on = os.path.exists(f"{BM}/appimage-jail")
    line("OK" if on else "INFO", "forcing is ON" if on else "forcing is OFF")
    try:
        for n in os.listdir(BM):
            if n in ("register", "status", "appimage-jail"): continue
            if "4149" in open(f"{BM}/{n}").read().lower():
                line("WARN", f"another AppImage handler is registered: {n} (may conflict)")
    except OSError:
        pass
    if not shutil.which("notify-send"): line("INFO", "notify-send missing (no failure popups)")
    ok = shutil.which("squashfuse") and shutil.which("fusermount3") and os.access("/dev/fuse", os.R_OK | os.W_OK)
    line("OK" if ok else "INFO", "compressed installs (experimental) available" if ok
         else "compressed installs unavailable: needs squashfuse, fuse3 and access to /dev/fuse")

def cmd_preview(n):
    NO_LOG[0] = True
    name = clean(n) if n else "NAME"
    cfg = load_cfg(name)
    bus = f"{runtime_dir()}/ajail/PID.bus" if want_bus(cfg) else None
    root = f"{INSTALLED}/{name}/root"
    if installed_kind(name) == "mount": root = f"{MOUNTS}/{name}"
    elif not os.path.isdir(root): root = f"{EXTRACT}/{name}/root"
    priv = private_net(cfg)
    gset = f"{runtime_dir()}/ajail/PID.d/gsettings-keyfile" if cfg["theme"] and gsettings_keyfile_text() is not None else None
    wp = wayland_path() if cfg["wayland"] else None
    wl = f"{runtime_dir()}/ajail/PID.wl" if wp and wl_supported(wp) else None
    cmd = bwrap_args(name, root, cfg, ["/app/AppRun"], bus, SEC_FD if priv else None, (), preview_files(cfg), gset, wl)
    if priv: cmd = net_wrap(cmd, f"{runtime_dir()}/ajail/PID.d/seccomp.bpf")
    props = limit_props(cfg)
    if props: cmd = ["systemd-run", "--user", "--scope", "--quiet"] + props + ["--"] + cmd
    print(shlex.join(cmd))

def app_list():
    """Every app ajail knows about: installed, with a settings file, or with saved data."""
    names = set()
    for d, ext in ((f"{CONF}/apps", ".conf"), (HOMES, ""), (INSTALLED, "")):
        try: entries = os.listdir(d)
        except OSError: continue
        for f in entries:
            if ext and not f.endswith(ext): continue      # e.g. NAME.machine-id belongs to an app but is not an app
            n = f[:-len(ext)] if ext else f
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", n): names.add(n)
    return sorted(names)

def cmd_list():
    for n in app_list():
        state = "running" if running(n) else "installed" if installed_kind(n) else "data only"
        print(f"{n:32} {state}")

def cmd_stop(args):
    if len(args) != 1: die("usage: ajail stop NAME")
    name = clean(args[0])
    pids = running(name)
    if not pids: die(f"{name} is not running")
    stop_app(name)
    print(f"Stopping {name}")

def cmd_dump(arg):
    """JSON of the raw settings, for ajail-gui."""
    NO_LOG[0] = True          # the GUI calls this on every refresh; warnings must not flood the log
    g = parse_cfg(f"{CONF}/default.conf") or empty_cfg()
    if arg == "--all":
        apps = {}
        try:
            for f in os.listdir(f"{CONF}/apps"):
                if f.endswith(".conf"): apps[f[:-5]] = parse_cfg(f"{CONF}/apps/{f}") or empty_cfg()
        except OSError:
            pass
        out = {"global": g, "apps": apps}
    else:
        out = {"global": g, "app": (parse_cfg(f"{CONF}/apps/{clean(arg)}.conf") or empty_cfg()) if arg else None}
    print(json.dumps(out))

# ---- export / import: every settings file in one text file, to back up or move to another machine.
EXPORT_MAGIC = "# ajail settings export"
EXPORT_MARK = re.compile(r"# \[(default\.conf|ui\.conf|apps/[A-Za-z0-9][A-Za-z0-9._-]*\.conf)\]")
EXPORT_MAX = 1 << 20       # a settings file is a few lines; refuse to read anything bigger as a bundle

def export_files():
    """[(label, path)] for default.conf and every apps/NAME.conf that exists."""
    out = []
    if os.path.isfile(f"{CONF}/default.conf"): out.append(("default.conf", f"{CONF}/default.conf"))
    try: names = sorted(os.listdir(f"{CONF}/apps"))
    except OSError: names = []
    for f in names:
        if f.endswith(".conf") and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", f[:-5]) and os.path.isfile(f"{CONF}/apps/{f}"):
            out.append((f"apps/{f}", f"{CONF}/apps/{f}"))
    return out

# The choices ajail-gui keeps in ui.conf: KEY=VALUE, and open_mode.NAME / mount.NAME for one app's own choice.
UI_VALUES = {"mount": ("0", "1"), "open_mode": ("ask", "run"), "install_original": ("ask", "trash", "keep", "move")}
UI_PER_APP = ("open_mode", "mount")

def ui_pair(line):
    """(key, value) when LINE is a ui.conf choice ajail-gui knows with a value it accepts, else None."""
    key, eq, val = line.strip().partition("=")
    base, dot, name = key.partition(".")
    if not eq or base not in UI_VALUES or val not in UI_VALUES[base]: return None
    if dot and not (base in UI_PER_APP and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name)): return None
    return key, val

def ui_choices(target="--all"):
    """{key: value} of the ui.conf choices that belong to TARGET: all of them, the global ones, or one app's own."""
    try:
        with open(f"{CONF}/ui.conf", errors="replace") as f: lines = f.read().splitlines()
    except OSError: return {}
    out = {}
    for l in lines:
        p = ui_pair(l)
        if p: out.setdefault(*p)            # the first line of a key counts, as ui_get reads it
    if target == "--default": return {k: v for k, v in out.items() if "." not in k}
    if target != "--all": return {k: v for k, v in out.items() if k in (f"open_mode.{target}", f"mount.{target}")}
    return out

def ui_merged(new):
    """The text of ui.conf with the choices NEW put in: a key already there changes in place, the others, and any line
    ajail does not know, stay as they are."""
    try:
        with open(f"{CONF}/ui.conf", errors="replace") as f: lines = f.read().splitlines()
    except OSError: lines = []
    left, out = dict(new), []
    for l in lines:
        k = l.strip().partition("=")[0]
        if k not in new: out.append(l)
        elif k in left: out.append(f"{k}={left.pop(k)}")        # a repeated key is dropped: one line per key
    out += [f"{k}={v}" for k, v in left.items()]
    return "".join(l + "\n" for l in out)

def export_text(target):
    """The bundle for TARGET: '--all', '--default' or an app name. Dies when there is nothing to export."""
    want = None if target == "--all" else "default.conf" if target == "--default" else f"apps/{target}.conf"
    files = [(l, p) for l, p in export_files() if want in (None, l)]
    ui = ui_choices(target)
    if not files and not ui: die("no settings to export" if want is None else f"no settings file for {target}: the defaults apply to it")
    out = [EXPORT_MAGIC]
    for label, path in files:
        try:
            with open(path, errors="replace") as f: lines = f.read().splitlines()
        except OSError as e: die(f"could not read {path}: {e.strerror or e}")
        out.append(f"# [{label}]")
        # A comment that looks like a section marker would split the file on import: keep it a plain comment.
        out += [("#" + l) if EXPORT_MARK.fullmatch(l.strip()) or l.strip() == EXPORT_MAGIC else l for l in lines]
    if ui: out += ["# [ui.conf]"] + [f"{k}={v}" for k, v in sorted(ui.items())]
    return "\n".join(out) + "\n"

def write_private(path, text, replace):
    """Write TEXT to PATH (mode 600, all or nothing). An existing file is only replaced when REPLACE is true."""
    if os.path.lexists(path) and not replace: die(f"{path} already exists; --force replaces it")
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f: f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        try: os.unlink(tmp)
        except OSError: pass
        die(f"could not write {path}: {e.strerror or e}")

def cmd_export(args):
    """ajail export [NAME | --default | --all] [-o FILE] [--force]"""
    force, out, target, i = False, None, None, 0
    while i < len(args):
        x = args[i]
        if x == "--force": force = True
        elif x == "-o":
            if i + 1 >= len(args): die(f"{x} needs a file name")
            out = args[i + 1]; i += 1
        elif x in ("--default", "--all") or not x.startswith("-"):
            if target: die("usage: ajail export [NAME | --default | --all] [-o FILE] [--force]")
            target = x
        else: die(f"unknown option {x}")
        i += 1
    target = target or "--all"
    if not target.startswith("-") and clean(target) != target: die(f"'{target}' is not an app name")
    text = export_text(target)
    if out is None: sys.stdout.write(text); return
    write_private(out, text, force)
    n = text.count("\n# [")
    print(f"Exported {n} settings file{'s' if n != 1 else ''} to {out}")

def parse_bundle(text):
    """{label: file text} from an export. Dies when the text is not one of ours."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != EXPORT_MAGIC: die(f"not an ajail settings export (the first line must be '{EXPORT_MAGIC}')")
    secs, cur = {}, None
    for l in lines[1:]:
        m = EXPORT_MARK.fullmatch(l.strip())
        if m:
            if m.group(1) in secs: die(f"{m.group(1)} appears twice in the export")
            cur = secs[m.group(1)] = []
        elif cur is None:
            if l.strip() and not l.lstrip().startswith("#"): die("the export has settings before its first [section]")
        else: cur.append(l)
    if not secs: die("the export holds no settings")
    return {k: "\n".join(v).strip("\n") + "\n" for k, v in secs.items()}

def cmd_import(args):
    """ajail import FILE|- [--force]: put the settings of an export back. Nothing is written when any file would be replaced
    without --force."""
    force = "--force" in args
    rest = [x for x in args if x != "--force"]
    bad = [x for x in rest if x.startswith("-") and x != "-"]
    if bad: die(f"unknown option {bad[0]}")
    if len(rest) != 1: die("usage: ajail import FILE [--force]")
    if os.environ.get("AJAIL_ACTIVE"): die("not from inside a sandbox")
    try:
        if rest[0] == "-": raw = sys.stdin.read(EXPORT_MAX + 1)
        else:
            with open(rest[0], errors="replace") as f: raw = f.read(EXPORT_MAX + 1)
    except OSError as e: die(f"could not read {rest[0]}: {e.strerror or e}")
    if len(raw) > EXPORT_MAX or "\0" in raw: die("that is not an ajail settings export")
    secs = parse_bundle(raw)
    todo, same, clash = [], 0, []
    for label, text in secs.items():
        path = f"{CONF}/{label}"
        if label == "ui.conf":              # not replaced as a file: each choice is put into the one that is there
            new = {}
            for l in text.splitlines():
                if not l.strip() or l.lstrip().startswith("#"): continue
                p = ui_pair(l)
                if not p: die(f"the ui.conf section has a line that is not one of ajail-gui's choices: {l.strip()[:60]}")
                new[p[0]] = p[1]
            have = ui_choices()
            if all(have.get(k) == v for k, v in new.items()): same += 1; continue
            if any(k in have and have[k] != v for k, v in new.items()): clash.append(label)
            todo.append((path, ui_merged(new)))
            continue
        try:
            with open(path, errors="replace") as f: old = f.read()
        except OSError: old = None
        if old is None: todo.append((path, text))
        elif old.strip("\n") == text.strip("\n"): same += 1
        # A file without a single setting (blank or comments only, as "Reset all settings" leaves them) is nothing to lose.
        elif not any(l.strip() and not l.lstrip().startswith("#") for l in old.splitlines()): todo.append((path, text))
        else: clash.append(label); todo.append((path, text))
    if clash and not force:
        die("these settings already exist and differ: " + ", ".join(clash) + ". Nothing was imported; --force replaces them")
    try: os.makedirs(f"{CONF}/apps", mode=0o700, exist_ok=True)
    except OSError as e: die(f"could not create {CONF}/apps: {e.strerror or e}")
    for path, text in todo: write_private(path, text, True)
    print(f"Imported {len(todo)} settings file{'s' if len(todo) != 1 else ''}" + (f", {same} already the same" if same else ""))

def cmd_desktop(args):
    """ajail desktop --install-menu: put ajail-gui in the app menu. The launcher is written by the installed ajail-gui itself, and
    only a root-owned copy counts (see menu_exec in ajail-gui), so nothing you can write to is ever started from the menu."""
    if args not in (["--install-menu"], ["--remove-menu"]): die("usage: ajail desktop --install-menu | --remove-menu")
    apps = f"{HOME}/.local/share/applications"
    if args[0] == "--remove-menu":
        gone = False
        for p in (f"{apps}/org.ajail.Gui.desktop", f"{HOME}/.local/share/icons/hicolor/scalable/apps/org.ajail.Gui.svg"):
            try:
                os.unlink(p)
                gone = True
            except OSError:
                pass
        if gone and shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", apps], stderr=subprocess.DEVNULL)
        print("removed AppImage Jail from the app menu" if gone else "nothing to remove")
        return
    for d in ("/usr/local/bin", "/usr/bin"):
        try:
            st = os.stat(d + "/ajail-gui")
        except OSError:
            continue
        if st.st_uid == 0 and not st.st_mode & 0o022:
            sys.exit(subprocess.run([d + "/ajail-gui", "--install-menu"]).returncode)
    die("ajail-gui is not installed (a root-owned copy is needed). Install it first: sudo install -m 755 -o root -g root ajail-gui /usr/local/bin/")

HELP = """ajail on | off | status | check   force on/off for all AppImages, or check the setup
ajail preview [NAME]             show the bwrap command that would run
ajail list                       show your apps and which ones are running
ajail stop NAME                  stop a running app
ajail name FILE.AppImage         show the name ajail uses for this file
ajail install FILE.AppImage      unpack it and add it to the app menu; the file is left where it is (--trash moves it to
                                 the Trash; --mount keeps it compressed and mounts it at launch, experimental; with
                                 --mount, --move moves the file into ajail's folder instead of copying it, and falls
                                 back to a copy on a removable or network drive)
ajail run [--compat-env] NAME [args]
                                 run an installed app (--compat-env passes a short list of graphics and toolkit
                                 variables from your session into the sandbox for this run; the permission
                                 compat_env=1 does it every time)
ajail uninstall NAME [--purge]   remove it from the app menu (--purge also moves its data to the Trash); refuses to
                                 delete an AppImage that was moved into ajail (use restore, or --discard-image)
ajail restore NAME [--keep] [--to FOLDER]
                                 undo a compressed install that moved the file: put it back and uninstall
                                 (--keep puts a copy back and leaves the app installed; --to picks the folder)
ajail run-file [--once] [--mount] [--compat-env] FILE
                                 run an AppImage without offering to install it (--once deletes its unpacked copy on exit;
                                 --mount mounts the file instead of unpacking it, experimental; ignored with --once)
ajail export [NAME | --default | --all] [-o FILE] [--force]
                                 write settings (default: all of them) as one text file, to stdout or FILE
ajail import FILE [--force]     load an export back; --force replaces settings that differ
ajail desktop --install-menu     add ajail-gui to your app menu (--remove-menu takes it out again); needs ajail-gui
                                 installed in /usr/local/bin
ajail FILE.AppImage [args]       open an AppImage: offers Install or Run once when double-clicked,
                                 otherwise runs it sandboxed (files in args are shared for that run)
Settings and everything else: ajail-gui"""

def main():
    a = sys.argv[1:]
    c = a[0] if a else "help"
    if c in ("help", "-h", "--help"): print(HELP)
    elif c == "on": cmd_on()
    elif c == "off": cmd_off()
    elif c == "status": print("Forcing: ON" if os.path.exists(f"{BM}/appimage-jail") else "Forcing: OFF")
    elif c == "check": cmd_check()
    elif c == "preview": cmd_preview(a[1] if len(a) > 1 else "")
    elif c == "dump": cmd_dump(a[1] if len(a) > 1 else "")
    elif c == "wl-check": print(int(bool(wayland_path() and wl_supported(wayland_path()))))      # for ajail-gui: can this desktop restrict Wayland apps?
    elif c == "list" and len(a) == 1: cmd_list()
    elif c == "stop": cmd_stop(a[1:])
    elif c == "export": cmd_export(a[1:])
    elif c == "import": cmd_import(a[1:])
    elif c == "name" and len(a) > 1: print(sanitize(a[1]))
    elif c == "install": cmd_install(a[1:])
    elif c == "uninstall": cmd_uninstall(a[1:])
    elif c == "restore": cmd_restore(a[1:])
    elif c == "desktop": cmd_desktop(a[1:])
    elif c == "run" and len(a) > 1 and a[1] == "--compat-env" and len(a) > 2: run_installed(a[2], a[3:], True)
    elif c == "run" and len(a) > 1: run_installed(a[1], a[2:])
    elif c == "run-file":
        rest, flags = a[1:], set()
        while rest and rest[0] in ("--once", "--mount", "--compat-env"):
            flags.add(rest.pop(0))
        if not rest: die("usage: ajail run-file [--once] [--mount] [--compat-env] FILE [args]")
        run_app(rest[0], rest[1:], "--once" in flags, "--mount" in flags and "--once" not in flags, "--compat-env" in flags)   # once never mounts
    else: open_file(c, a[1:])

main()
