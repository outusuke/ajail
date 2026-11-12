# ======== 60_desktop: Desktop entries and icons for installed apps.

DESKTOP_LINE_MAX = 1000     # longer lines of an app's .desktop file are not copied into its launcher
DESKTOP_KEEP = re.compile(r"(Name|GenericName|Comment|Keywords)(\[[A-Za-z0-9_@.-]+\])?=|(Categories|MimeType|StartupWMClass)=")

def read_desktop(root):
    """Name, categories and the like from the app's own .desktop file. Exec and similar keys are never copied."""
    keep, icon = [], None
    for f in sorted(glob.glob(f"{glob.escape(root)}/*.desktop"))[:1]:
        if not inside(root, f): continue
        if not os.path.isfile(f): continue          # no FIFOs or devices
        try:
            with open(f, errors="replace") as fh: text = fh.read(65536)
        except OSError: continue
        for ln in text.splitlines():
            ln = ln.strip()
            if ln.startswith("["):
                if ln != "[Desktop Entry]": break
                continue
            if CTRL.search(ln) or len(ln) > DESKTOP_LINE_MAX: continue     # control characters (incl. DEL and C1) and huge lines
            if ln.startswith("Icon="): icon = ln[5:]
            elif DESKTOP_KEEP.match(ln): keep.append(ln)
    return keep, icon

ICON_SIZES = (16, 22, 24, 32, 36, 48, 64, 72, 96, 128, 192, 256, 512)      # the size folders the hicolor theme lists
SVG_HEAD = 16384        # an SVG may start with a long XML prolog, comments or a doctype before <svg

def icon_size_dir(w):
    """The hicolor folder for a PNG w pixels wide. The theme only looks in the sizes it lists, so a 100, 300 or 1024 pixel icon put
    in a folder of its own size is never found and the menu shows a generic icon. It goes in the nearest listed size instead."""
    return "%dx%d" % ((s := min(ICON_SIZES, key=lambda x: (abs(x - w), -x))), s)

def find_icon(root, icon):
    """Best icon inside the extracted app as (path, extension, size dir), or None."""
    r = glob.escape(root)
    cands = [f"{root}/.DirIcon"]
    if icon and re.fullmatch(r"[A-Za-z0-9._+-]+", icon):
        for ext in ("svg", "png"):
            cands += glob.glob(f"{r}/usr/share/icons/hicolor/*/apps/{icon}.{ext}") + [f"{root}/{icon}.{ext}"]
    best = None
    for p in cands:
        try:
            if not os.path.isfile(p) or not inside(root, p) or os.path.getsize(p) > 5_000_000: continue
            with open(p, "rb") as fh: head = fh.read(SVG_HEAD)
        except OSError:
            continue
        if b"<svg" in head.lower(): cand = ((1, 0), p, "svg", "scalable")
        elif head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
            w, h = struct.unpack(">II", head[16:24])
            if not 16 <= max(w, h) <= 8192: continue
            cand = ((0, max(w, h)), p, "png", icon_size_dir(max(w, h)))
        else: continue
        if best is None or cand[0] > best[0]: best = cand
    return best[1:] if best else None

def remove_icons(name):
    for p in glob.glob(f"{ICONS}/*/apps/ajail-{name}.*"):
        try: os.unlink(p)
        except OSError: pass

def refresh_desktop_db():
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", APPLICATIONS], capture_output=True, check=False)

def install_launcher(name, root):
    keep, icon = read_desktop(root)
    remove_icons(name)
    found, iname = find_icon(root, icon), None
    if found:
        src, ext, size = found
        os.makedirs(f"{ICONS}/{size}/apps", exist_ok=True)
        shutil.copyfile(src, f"{ICONS}/{size}/apps/ajail-{name}.{ext}")
        iname = f"ajail-{name}"
    lines = ["[Desktop Entry]", "Type=Application", f"Exec={SELF} run {name} %U", "Terminal=false", f"X-AJAIL-App={name}"]
    if not any(k.startswith("Name=") for k in keep): lines.append(f"Name={name}")
    lines += keep
    if iname: lines.append(f"Icon={iname}")
    os.makedirs(APPLICATIONS, exist_ok=True)
    path = f"{APPLICATIONS}/ajail-{name}.desktop"
    open(path + ".tmp", "w").write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)
    refresh_desktop_db()
