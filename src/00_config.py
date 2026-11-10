#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# ajail - run AppImages in a bubblewrap sandbox with a persistent private home
# ======== 00_config: Configuration: paths, permission defaults, syscall tables.

import codecs, errno, fcntl, glob, grp, json, os, platform, pwd, re, select, shlex, shutil, signal, struct, subprocess
import sys, tempfile, threading, time, urllib.parse

_pw     = pwd.getpwuid(os.getuid())
HOME    = os.path.realpath(_pw.pw_dir)
USER    = _pw.pw_name
CONF    = f"{HOME}/.config/ajail"
STATE   = f"{HOME}/.local/state/ajail"
RUNNING = f"{STATE}/running"
SOURCES = f"{STATE}/sources"
LOG     = f"{STATE}/ajail.log"
HOMES   = f"{HOME}/.appimage-homes"
LAYERS  = f"{HOME}/.local/share/ajail/app-layers"     # writable layer over /app for apps with app_writable
EXTRACT = f"{HOME}/.local/share/ajail/extracted"
SELF    = "/usr/local/bin/ajail"
BM      = "/proc/sys/fs/binfmt_misc"
RULE    = r":appimage-jail:M:8:AI\x02::/usr/local/bin/ajail:"
UNSQ    = "/usr/bin/unsquashfs"
INSTALLED    = f"{HOME}/.local/share/ajail/apps"
APPLICATIONS = f"{HOME}/.local/share/applications"
ICONS        = f"{HOME}/.local/share/icons/hicolor"
MOUNTS       = f"{os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'}/ajail-mnt"
ETC = ["ld.so.cache", "ld.so.conf", "ld.so.conf.d", "passwd", "group", "nsswitch.conf", "hosts",
       "resolv.conf", "localtime", "os-release", "fonts", "ssl", "pki", "crypto-policies",
       "alternatives", "vulkan", "drirc", "mime.types", "protocols", "services"]

# Permission defaults. Keep in sync with DEFAULTS in ajail-gui.
DEFAULTS = {"net": 1, "sound": 1, "gpu": 1, "x11": 0, "portals": 1, "tray": 0, "camera": 0,
            "input": 0, "ssh_agent": 0, "printing": 0, "theme": 1, "userns": 1,
            "net_private": 0, "tmp_home": 0, "privacy": 0, "harden": 0,
            "portal_screenshot": 1, "portal_screencast": 1, "app_writable": 0}
# Numeric limits (0 = no limit): memory in MB, CPU in % of one core, number of processes.
NUM = {"mem_max": 0, "cpu_max": 0, "tasks_max": 0}
LISTS = ("allow", "allow_ro", "xdg", "env", "talk", "portal")
# Opt-in desktop portals, enabled one at a time with portal=NAME. Anything not listed here is rejected.
# Keep in sync with EXTRA_PORTALS in ajail-gui.
EXTRA_PORTALS = ("Location", "GlobalShortcuts", "RemoteDesktop", "InputCapture", "Wallpaper", "DynamicLauncher", "Secret")
XDG_DEFAULT = {"download": "Downloads", "documents": "Documents", "pictures": "Pictures",
               "music": "Music", "videos": "Videos", "desktop": "Desktop"}
THEME = [".local/share/fonts", ".fonts", ".local/share/icons", ".icons", ".local/share/themes", ".themes"]
OUTSIDE = ("/mnt/", "/media/", f"/run/media/{USER}/")
# Blocked syscalls: kexec_load kexec_file_load perf_event_open bpf syslog uselib acct quotactl add_key
# keyctl request_key move_pages mbind get_mempolicy set_mempolicy migrate_pages open_by_handle_at name_to_handle_at
SYSCALLS_X86_64 = [246, 320, 298, 321, 103, 134, 163, 179, 248, 250, 249, 279, 237, 239, 238, 256, 304, 303]
SYSCALLS_AARCH64 = [104, 294, 241, 280, 116, 89, 60, 217, 219, 218, 239, 235, 236, 237, 238, 265, 264]
# Extra blocked syscalls with "Block extra system calls" (harden): io_uring_setup io_uring_enter io_uring_register
# userfaultfd process_vm_readv process_vm_writev kcmp
SYSCALLS_EXTRA = {"x86_64": [425, 426, 427, 323, 310, 311, 312], "aarch64": [425, 426, 427, 282, 270, 271, 272]}
LEGACY_FLAGS ={"--net=none": ("net", 0), "--nosound": ("sound", 0), "--no3d": ("gpu", 0), "--x11": ("x11", 1)}

# Install hints in error messages. Only families where the packages ajail asks for (bubblewrap, squashfs-tools,
# xdg-dbus-proxy, squashfuse, fuse3) have the same names are listed; anywhere else gets a generic hint.
OS_RELEASE = "/etc/os-release"
INSTALL_CMD = {"fedora": "sudo dnf install", "debian": "sudo apt install", "arch": "sudo pacman -S"}
IDENT_MAX = 200
