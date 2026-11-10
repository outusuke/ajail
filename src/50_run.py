# ======== 50_run: Launch path: guard, run_app, run_installed, launch. Second-tier review (it handles the app's output streams).

def guard():
    if os.geteuid() == 0: die("refusing to run apps as root")
    if os.environ.get("AJAIL_ACTIVE"): die("recursion detected, aborting. Run: ajail off")
    if not shutil.which("bwrap"): die("bubblewrap missing: " + install_hint("bubblewrap"))

def check_display(cfg):
    if not cfg["x11"] and not wayland_path():
        die("no Wayland session found. Enable 'X11 compatibility' for this app in ajail-gui.")

def run_app(arg, extra, once=False, mount=False):
    guard()
    app = os.path.realpath(arg)
    if not os.path.isfile(app): die(f"not a file: {arg}")
    off = appimage_offset(app)
    name = sanitize(app)
    cfg = load_cfg(name)
    check_display(cfg)
    if mount:
        # Nothing is unpacked: the AppImage itself is mounted for this run (squashfuse, outside the sandbox).
        mp = mount_squashfs(app, off, name)
        try:
            launch(name, mp, cfg, extra, source=app)
        finally:
            unmount_image(mp)
        return
    launch(name, ensure_extracted(app, name, off), cfg, extra, source=app, once=once)

def run_installed(name, extra):
    guard()
    name = clean(name)
    kind = installed_kind(name)
    if not kind: die(f"'{name}' is not installed")
    cfg = load_cfg(name)
    check_display(cfg)
    if kind == "root":
        launch(name, f"{INSTALLED}/{name}/root", cfg, extra)
        return
    mp = mount_image(name)
    try:
        launch(name, mp, cfg, extra)
    finally:
        unmount_image(mp)

def launch(name, root, cfg, extra, source=None, once=False):
    if not os.path.lexists(f"{root}/AppRun"): die("no AppRun found inside this AppImage")
    check_identity(name, root)
    os.makedirs(HOMES, mode=0o700, exist_ok=True)
    os.makedirs(f"{HOMES}/{name}", mode=0o700, exist_ok=True); os.chmod(f"{HOMES}/{name}", 0o700)
    os.makedirs(RUNNING, exist_ok=True); os.makedirs(SOURCES, exist_ok=True)
    if source:
        try: open(f"{SOURCES}/{name}", "w").write(source)
        except OSError: pass
    priv = private_net(cfg)
    if priv and not shutil.which("pasta"):
        die("'Keep localhost private' needs pasta: " + install_hint("passt") + ". Or turn that option off for this app.")
    sweep_runtime()
    bus = start_bus(cfg, name)
    sec = seccomp_file(cfg["harden"])
    if sec is None and platform.machine() not in ("x86_64", "aarch64"):
        warn(f"no system-call filter for the {platform.machine()} architecture; running without it", name)
    elif sec is None:                             # a filter exists for this CPU but could not be made: never run without it silently
        stop_bus(bus)
        die("could not create the system-call filter (see the log); not starting the app without it")
    wd, gen, secpath, gset = None, {}, None, None
    if True:
        wd = work_dir(); gen = make_files(cfg, wd, name)
    if cfg["theme"]: gset = make_gsettings_keyfile(wd)
    if priv and sec:
        secpath = f"{wd}/seccomp.bpf"
        sec.seek(0)
        with open(secpath, "wb") as f: f.write(sec.read())
    secfd = (SEC_FD if secpath else None) if priv else (sec.fileno() if sec else None)
    inner = bwrap_args(name, root, cfg, ["/app/AppRun"] + extra, bus[2] if bus else None,
                       secfd, forward_files(extra), gen, gset)
    cmd = limit_prefix(cfg, name) + (net_wrap(inner, secpath) if priv else inner)
    on = ",".join(k for k in DEFAULTS if cfg[k])
    lim = ",".join(f"{k}={cfg[k]}" for k in NUM if cfg[k])
    log(f"START allow=[{on}] limits=[{lim}] seccomp={'on' if sec else 'off'} dbus={'on' if bus else 'off'}", name)
    start = time.time()
    tty = sys.stdout.isatty()                     # to a terminal, stdout is filtered too; to a pipe or file it stays untouched
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE if tty else None, stderr=subprocess.PIPE, text=True, errors="replace",
                         pass_fds=[sec.fileno()] if sec and not priv else ())
    pidfile = f"{RUNNING}/{os.getpid()}"
    open(pidfile, "w").write(name)
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, lambda *_: p.terminate())
    def tee():
        try:
            with open(LOG, "a") as lf:
                n = 0                             # a chatty app must not fill the disk: cap what one run adds to the log
                dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
                pend = ""                         # the unfinished line, kept short: a flood without newlines must not fill memory
                while True:
                    data = os.read(p.stderr.fileno(), 4096)
                    text = CTRL.sub("", dec.decode(data, final=not data))     # an app must not inject terminal escape sequences (OSC 52 etc.)
                    try: sys.stderr.write(text); sys.stderr.flush()
                    except Exception: pass
                    lines = (pend + text).split("\n"); pend = lines.pop()
                    lines = [x + "\n" for x in lines]
                    while len(pend) > LOG_LINE_MAX: lines.append(pend[:LOG_LINE_MAX] + "\n"); pend = pend[LOG_LINE_MAX:]
                    if not data and pend: lines.append(pend + "\n"); pend = ""
                    for line in lines:
                        if n < LOG_RUN_MAX:
                            n += len(line)
                            lf.write(f"[{name}] {line}")
                            if n >= LOG_RUN_MAX: lf.write(f"[{name}] (more output not logged)\n")
                            lf.flush()
                    if not data: break
        except Exception:
            pass
    def pump():                                   # chunks, not lines, so a prompt without a newline shows at once
        try:
            dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                data = os.read(p.stdout.fileno(), 4096)
                text = dec.decode(data, final=not data)
                try: sys.stdout.write(CTRL.sub("", text)); sys.stdout.flush()
                except Exception: pass
                if not data: break
        except Exception:
            pass
    t = threading.Thread(target=tee, daemon=True); t.start()
    t2 = threading.Thread(target=pump, daemon=True) if tty else None
    if t2: t2.start()
    rc = p.wait(); t.join(2)
    if t2: t2.join(2)
    stop_bus(bus)
    if wd: shutil.rmtree(wd, ignore_errors=True)
    try: os.unlink(pidfile)
    except OSError: pass
    if once and not running(name): rm_tree(f"{EXTRACT}/{name}")
    log(f"EXIT code={rc}", name)
    if rc != 0 and time.time() - start < 10:
        notify_failure(name)
    sys.exit(rc)
