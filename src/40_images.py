# ======== 40_images: AppImage storage: mounting, moving, trashing, running-state.

def trash_path(p):
    """Move a file or folder to the Trash (needs gio). Returns False if that did not work."""
    return bool(shutil.which("gio")) and subprocess.run(["gio", "trash", p], capture_output=True).returncode == 0

def trash_layer(layer):
    """Move an app's writable /app layer to the Trash. The kernel's overlayfs 'work' dir is scratch space (mode 000
    inside, recreated at the next launch), which gio cannot copy when the Trash is on another mount, so it is removed
    first and only the saved data ('upper') goes to the Trash. Returns False if that did not work."""
    wk = f"{layer}/work"
    for r, ds, fs in os.walk(wk):
        for x in ds + [""]:
            try: os.chmod(os.path.join(r, x) if x else r, 0o700)
            except OSError: pass
    rm_tree(wk)
    for r, ds, fs in os.walk(layer):                 # saved files may be read-only too: make them movable
        for x in ds:
            try: os.chmod(os.path.join(r, x), os.stat(os.path.join(r, x)).st_mode | 0o700)
            except OSError: pass
    return trash_path(layer)

def rm_tree(p):
    if os.path.isdir(p) and not os.path.islink(p): shutil.rmtree(p, ignore_errors=True)

def file_stamp(path):
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}"

# Filesystems where a file is better copied than moved: the original lives on somebody else's disk.
NETFS = ("nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "afs", "ceph", "glusterfs")

def fs_type(path):
    """Filesystem type of the mount that holds PATH ('' if unknown)."""
    best, typ = "", ""
    try:
        for ln in open("/proc/self/mountinfo"):
            p = ln.split()
            if len(p) < 7 or "-" not in p: continue
            mp = p[4].replace("\\040", " ")
            if (path == mp or path.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best):
                best, typ = mp, p[p.index("-") + 1]
    except OSError:
        pass
    return typ

def can_move(app):
    """True if an install may take this AppImage out of its folder (like dragging an app into Applications).
    Files on removable drives, network shares and FUSE mounts, and files in folders you can't write to, are copied."""
    if any(app.startswith(b) for b in OUTSIDE): return False
    ft = fs_type(app)
    if ft in NETFS or ft.startswith("fuse."): return False
    return os.access(os.path.dirname(app), os.W_OK | os.X_OK)

def place_image(app, dst, move):
    """Put the AppImage at DST: moved if MOVE is set and it works, otherwise copied. A copy shares disk blocks on
    filesystems that support it (btrfs, XFS), so it costs no extra space there. Returns True if the file was moved."""
    if move:
        try:
            shutil.move(app, dst)
            return True
        except OSError:
            try: os.unlink(dst)           # a half-finished cross-device move leaves a partial file behind
            except OSError: pass
            if not os.path.isfile(app): raise
    r = subprocess.run(["cp", "--reflink=auto", "--", app, dst], capture_output=True)
    if r.returncode:
        shutil.copyfile(app, dst)
    return False

def installed_kind(name):
    d = f"{INSTALLED}/{name}"
    return "root" if os.path.isdir(f"{d}/root") else "mount" if os.path.isfile(f"{d}/image") else None

def need_squashfuse():
    if not (shutil.which("squashfuse") and shutil.which("fusermount3")):
        die("compressed installs need squashfuse: " + install_hint("squashfuse", "fuse3"))
    if not os.access("/dev/fuse", os.R_OK | os.W_OK):
        die("/dev/fuse is not accessible, so compressed installs cannot work on this system")

def sweep_mounts():
    """Unmount and remove mountpoints left behind by ajail processes that died."""
    try: entries = os.listdir(MOUNTS)
    except OSError: return
    for e in entries:
        pid = e.rsplit("-", 1)[-1]
        if pid.isdigit() and os.path.exists(f"/proc/{pid}"): continue
        subprocess.run(["fusermount3", "-uz", f"{MOUNTS}/{e}"], capture_output=True)
        try: os.rmdir(f"{MOUNTS}/{e}")
        except OSError: pass

def mount_image(name):
    d = f"{INSTALLED}/{name}"
    return mount_squashfs(f"{d}/image", open(f"{d}/offset").read().strip(), name)

def mount_squashfs(image, off, name):
    """Mount the squashfs inside an AppImage with squashfuse and return the mountpoint."""
    need_squashfuse()
    sweep_mounts()
    os.makedirs(MOUNTS, mode=0o700, exist_ok=True)
    mp = f"{MOUNTS}/{name}-{os.getpid()}"
    os.makedirs(mp, mode=0o700, exist_ok=True)
    # Show the files as yours (the image's own owner, often root, is not mapped in the sandbox, so the overlay of
    # app_writable would get "Permission denied"). Some squashfuse builds reject uid/gid: then mount as before.
    for opts in (f"offset={off},uid={os.getuid()},gid={os.getgid()}", f"offset={off}"):
        try:
            r = subprocess.run(["squashfuse", "-o", opts, image, mp],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess([], 1, "", "timed out")
        if not r.returncode: break
    if r.returncode:
        os.rmdir(mp)
        die(f"could not mount the image: {CTRL.sub('', r.stderr).strip() or r.returncode}")
    return mp

def unmount_image(mp):
    if subprocess.run(["fusermount3", "-u", mp], capture_output=True).returncode:
        subprocess.run(["fusermount3", "-uz", mp], capture_output=True)
    try: os.rmdir(mp)
    except OSError: pass

def installed_stamp(name):
    try: return open(f"{INSTALLED}/{name}/stamp").read()
    except OSError: return None

def live_ajail(pid):
    """True if this PID is really an ajail process (a stale pid file may point at a reused PID)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f: return b"ajail" in f.read()
    except OSError:
        return False

def proc_start(pid):
    """When PID started: field 22 of /proc/PID/stat (clock ticks since boot, as text), or None. A reused PID gets another one."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f: rest = f.read().rsplit(b")", 1)[1].split()     # field 2, the name, may hold spaces and ')'
        return rest[19].decode()                                                              # rest[0] is field 3
    except (OSError, IndexError, UnicodeDecodeError):
        return None

def pid_app(pid):
    """The app name in RUNNING/PID, if that file was written by the process that is still running under this PID.
    The file holds "NAME\nSTARTTIME". Old files (the name only) can't be checked, so they count for nothing."""
    try:
        with open(f"{RUNNING}/{pid}") as f: name, _, start = f.read().partition("\n")
    except OSError:
        return None
    start = start.strip()
    return name.strip() if start and start == proc_start(pid) and live_ajail(pid) else None

def running(name):
    try:
        return [f for f in os.listdir(RUNNING) if f.isdigit() and pid_app(f) == name]
    except OSError:
        return []
