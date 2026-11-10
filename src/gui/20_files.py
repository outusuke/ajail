# ======== 20_files: Destructive file operations: trash an app's data, forget an app.

def trash_layer(layer):
    """Trash an app's writable layer. Its overlayfs 'work' dir is scratch (mode 000 inside, recreated at launch) and
    breaks gio's copy fallback, so remove it and trash only the saved data. Returns True on success."""
    wk = f"{layer}/work"
    for r, ds, fs in os.walk(wk):
        for x in ds + [""]:
            try:
                os.chmod(os.path.join(r, x) if x else r, 0o700)
            except OSError:
                pass
    shutil.rmtree(wk, ignore_errors=True)
    for r, ds, fs in os.walk(layer):
        for x in ds:
            try:
                p = os.path.join(r, x)
                os.chmod(p, os.stat(p).st_mode | 0o700)
            except OSError:
                pass
    return run(["gio", "trash", layer], timeout=120).returncode == 0

def forget_app(name):
    """Remove everything AppImage Jail keeps for an app that is not installed: its saved data (moved to the Trash, so a
    failure stops before anything else is touched), settings, machine-id, remembered identity and source, unpacked copy and
    remembered choices.
    The AppImage file itself is never touched. Returns an error message, or None when done."""
    if not NAME_RE.match(name):
        return "That is not a valid app name"
    if running_pids(name):
        return "It is running. Stop it first"
    if os.path.isdir(f"{INSTALLED}/{name}"):
        return "It is installed. Use Uninstall instead"
    home = f"{HOMES}/{name}"
    if os.path.islink(home):
        os.unlink(home)
    elif os.path.isdir(home):
        if run(["gio", "trash", home], timeout=120).returncode:
            return "Could not move the saved data to the Trash"
    layer = f"{LAYERS}/{name}"
    if os.path.islink(layer):
        os.unlink(layer)
    elif os.path.isdir(layer):
        if not trash_layer(layer):
            return "Could not move the saved layer to the Trash"
    for p in (f"{APPS}/{name}.conf", f"{APPS}/{name}.machine-id", f"{CONF}/ids/{name}", f"{SOURCES}/{name}"):
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
        except OSError as e:
            return f"Could not remove {os.path.basename(p)}: {e.strerror}"
    ext = f"{EXTRACT}/{name}"
    if os.path.islink(ext):
        os.unlink(ext)
    else:
        shutil.rmtree(ext, ignore_errors=True)
    ui_drop([f"open_mode.{name}", f"mount.{name}"])
    return None
