#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# ajail-gui - GTK4 + libadwaita front-end for ajail (a Flatseal-style permission editor)
# ======== 00_config: Configuration: paths, permission defaults and labels, UI text tables. Data only.

import json, os, pwd, re, signal, shutil, subprocess, sys, threading
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

SELF    = "/usr/local/bin/ajail"
HOME    = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)      # from the account like ajail, not from $HOME
USER    = pwd.getpwuid(os.getuid()).pw_name      # from the account, not from the environment
CONF    = HOME + "/.config/ajail"
APPS    = CONF + "/apps"
HOMES   = HOME + "/.appimage-homes"
LAYERS  = HOME + "/.local/share/ajail/app-layers"
EXTRACT = HOME + "/.local/share/ajail/extracted"
INSTALLED = HOME + "/.local/share/ajail/apps"
UICONF = CONF + "/ui.conf"
STATE   = HOME + "/.local/state/ajail"
RUNNING = STATE + "/running"
SOURCES = STATE + "/sources"
LOG     = STATE + "/ajail.log"
RULE    = "/proc/sys/fs/binfmt_misc/appimage-jail"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
OUTSIDE = ("/mnt/", "/media/", f"/run/media/{USER}/")
os.makedirs(APPS, exist_ok=True)

# Keep in sync with DEFAULTS in ajail.
DEFAULTS = {"net": 1, "sound": 1, "gpu": 1, "x11": 0, "portals": 1, "tray": 0, "camera": 0,
            "input": 0, "ssh_agent": 0, "printing": 0, "theme": 1, "userns": 1,
            "net_private": 0, "tmp_home": 0, "privacy": 0, "harden": 0,
            "portal_screenshot": 1, "portal_screencast": 1, "app_writable": 0}
NUM = {"mem_max": 0, "cpu_max": 0, "tasks_max": 0}      # 0 = no limit
LISTS = ("allow", "allow_ro", "xdg", "env", "talk", "portal")
# Opt-in desktop portals. Keep in sync with EXTRA_PORTALS in ajail.
EXTRA_PORTALS = ("Location", "GlobalShortcuts", "RemoteDesktop", "InputCapture", "Wallpaper", "DynamicLauncher", "Secret")
PORTAL_INFO = {      # name -> (switch title, description); must have exactly the EXTRA_PORTALS keys
    "Location": ("Location", "Let the app ask the desktop where you are."),
    "GlobalShortcuts": ("Global shortcuts", "Let the app register keyboard shortcuts that work in any window."),
    "RemoteDesktop": ("Remote desktop and input control",
                      "Let the app ask to control your mouse and keyboard. Far-reaching access."),
    "InputCapture": ("Input capture", "Let the app ask to capture your keyboard and mouse input. Far-reaching access."),
    "Wallpaper": ("Wallpaper", "Let the app ask to change your desktop wallpaper."),
    "DynamicLauncher": ("Create launchers", "Let the app ask to add its own entries to your app menu."),
    "Secret": ("Secrets", "Let the app ask the desktop for its stored secrets (keyring). Far-reaching access."),
}
XDG =[("download", "Downloads"), ("documents", "Documents"), ("pictures", "Pictures"),
       ("music", "Music"), ("videos", "Videos"), ("desktop", "Desktop")]
MODES = ["No access", "Read-only", "Read & write"]
LABEL = {"net": "Internet", "sound": "Audio", "gpu": "Graphics", "camera": "Camera",
         "input": "Input devices", "x11": "X11", "ssh_agent": "SSH agent", "printing": "Printing"}
SECTIONS = [
    ("What can this app use?",
     "Changes apply the next time the app starts. Everything else on your computer stays hidden from it.", [
        ("net", "Internet", "Network access. Also reaches localhost and your local network."),
        ("net_private", "Keep localhost private",
         "Internet only: the app can't reach services running on this computer (databases, dev servers). "
         "Other devices on your local network are still reachable. Needs passt (pasta)."),
        ("sound", "Audio", "Play sound. Also allows microphone access."),
        ("x11", "X11 compatibility", "Only for apps that won't start otherwise. Weaker isolation."),
        ("printing", "Printing", "Talk to the print service (CUPS)"),
        ("ssh_agent", "SSH agent", "Use your SSH keys without seeing them. The app can sign as you.")]),
    ("Devices", None, [
        ("gpu", "Graphics acceleration", "Needed by most apps; turn off only if the app crashes"),
        ("camera", "Camera", "Webcams (/dev/video*)"),
        ("input", "Game controllers and input devices", "Raw access to /dev/input")]),
    ("Desktop integration", None, [
        ("portals", "Notifications and portals", "Notifications, opening links, dark mode (needs xdg-dbus-proxy)"),
        ("portal_screenshot", "Screenshots",
         "Let the app ask the desktop for a screenshot. Needs Notifications and portals. The desktop shows its own prompt."),
        ("portal_screencast", "Screen sharing",
         "Let the app ask to share your screen or a window (Discord, Teams, Slack). Needs Notifications and portals. "
         "The desktop shows its own picker."),
        ("tray", "Tray icon and media keys", "Show a tray icon and answer media keys (needs xdg-dbus-proxy)"),
        ("theme", "Your fonts and themes", "Read-only access to your installed fonts, icons and themes")]),
    ("Hardening", None, [
        ("tmp_home", "Temporary home",
         "Everything the app saves in its private home is erased when it closes. Shared folders are not affected."),
        ("privacy", "Hide personal details",
         "Hides your real name and the computer name from the app (minimal user list, hosts file and hostname)."),
        ("harden", "Block extra system calls",
         "Also blocks io_uring and a few other rarely needed calls. Can slow down or break some apps."),
        ("app_writable", "Let the app save into its own folder",
         "For apps that fail with 'Read-only file system' under /app. What it writes is kept in a separate layer; "
         "the app's own files are not changed. Needs bubblewrap 0.11+."),
        ("userns","Allow the app's own sandbox",
         "Electron and Chromium apps need this. Turn off for stricter isolation (needs bubblewrap 0.8+).")]),
]
