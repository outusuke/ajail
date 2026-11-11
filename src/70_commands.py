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
    yes(wayland_path(), "Wayland socket found", "no Wayland socket visible to this process")
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
    cmd = bwrap_args(name, root, cfg, ["/app/AppRun"], bus, SEC_FD if priv else None, (), preview_files(cfg), gset)
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
    for pid in pids:
        try: os.kill(int(pid), signal.SIGTERM)
        except OSError: pass
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
    elif c == "list" and len(a) == 1: cmd_list()
    elif c == "stop": cmd_stop(a[1:])
    elif c == "name" and len(a) > 1: print(sanitize(a[1]))
    elif c == "install": cmd_install(a[1:])
    elif c == "uninstall": cmd_uninstall(a[1:])
    elif c == "restore": cmd_restore(a[1:])
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
