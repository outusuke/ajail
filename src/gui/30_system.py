# ======== 30_system: System helpers: distro install hint, app-menu entry, sizes.

def mount_install_cmd():
    """Command that installs squashfuse and fuse3 on this distro, or None if the distro isn't recognised."""
    ids = []
    try:
        for ln in open("/etc/os-release"):
            k, _, v = ln.strip().partition("=")
            if k in ("ID", "ID_LIKE"):
                ids += v.strip('"').lower().split()
    except OSError:
        return None
    for family, cmd in (("fedora", "sudo dnf install squashfuse fuse3"),
                        ("rhel", "sudo dnf install squashfuse fuse3"),
                        ("debian", "sudo apt install squashfuse fuse3"),
                        ("ubuntu", "sudo apt install squashfuse fuse3"),
                        ("arch", "sudo pacman -S squashfuse fuse3")):
        if family in ids:
            return cmd
    return None

MENU_DESKTOP = HOME + "/.local/share/applications/org.ajail.Gui.desktop"
MENU_ICON = HOME + "/.local/share/icons/hicolor/scalable/apps/org.ajail.Gui.svg"
MENU_ENTRY = f"""[Desktop Entry]
Type=Application
Name=AppImage Jail
GenericName=AppImage Sandbox Manager
Comment=Install sandboxed AppImages and edit their permissions
Exec={os.path.realpath(sys.argv[0]) if os.path.basename(sys.argv[0]) == "ajail-gui" else "/usr/local/bin/ajail-gui"}
Icon=org.ajail.Gui
Terminal=false
StartupNotify=true
Categories=System;Settings;Security;GTK;
Keywords=appimage;sandbox;bubblewrap;permissions;flatseal;
"""
MENU_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect x="8" y="8" width="112" height="112" rx="26" fill="#3584e4"/>
  <path d="M64 24 L94 36 V62 C94 80 81 94 64 102 C47 94 34 80 34 62 V36 Z" fill="#fff" opacity=".95"/>
  <rect x="50" y="58" width="28" height="22" rx="4" fill="#3584e4"/>
  <path d="M55 58 V51 a9 9 0 0 1 18 0 V58" fill="none" stroke="#3584e4" stroke-width="5"/>
</svg>
"""

def menu_entry_exists():
    return os.path.isfile(MENU_DESKTOP)

def set_menu_entry(on):
    """Add or remove AppImage Jail's own launcher in the user's app menu (no root needed)."""
    if on:
        os.makedirs(os.path.dirname(MENU_DESKTOP), exist_ok=True)
        os.makedirs(os.path.dirname(MENU_ICON), exist_ok=True)
        for path, text in ((MENU_ICON, MENU_SVG), (MENU_DESKTOP, MENU_ENTRY)):
            with open(path + ".tmp", "w") as f:
                f.write(text)
            os.replace(path + ".tmp", path)
    else:
        for path in (MENU_DESKTOP, MENU_ICON):
            try:
                os.unlink(path)
            except OSError:
                pass
    if shutil.which("update-desktop-database"):
        run(["update-desktop-database", os.path.dirname(MENU_DESKTOP)], timeout=10)

def dir_size(path):
    if not os.path.isdir(path):
        return "none yet"
    r = run(["du", "-sh", path], timeout=5)
    return r.stdout.split()[0] if r.stdout else "unknown"
