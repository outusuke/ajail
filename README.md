# ajail

Run AppImages in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox. Each app gets its own private home and its own permissions, set Flatseal-style.

- `ajail`: launcher and CLI
- `ajail-gui`: GTK4 + libadwaita permission editor
- `make-selftest`: builds a test AppImage that reports what the sandbox lets through

## Quick start

```bash
sudo dnf install bubblewrap squashfs-tools xdg-dbus-proxy libnotify python3-gobject gtk4 libadwaita   # Fedora
sudo install -m 755 -o root -g root ajail ajail-gui /usr/local/bin/
ajail check                  # shows what is missing
ajail on                     # optional: route every AppImage through ajail (asks for your password)
```

On Debian/Ubuntu and Arch the same package names work for the core tools; `ajail check` prints the exact command. Elsewhere, install the equivalents. `ajail` must live at `/usr/local/bin/ajail`, owned by root.

Needs Linux, Wayland (X11 is a per-app option), Python 3, `bubblewrap`, `squashfs-tools`. Optional: `xdg-dbus-proxy` (notifications, portals, tray), `passt` (Keep localhost private), `squashfuse` + `fuse3` (compressed installs), `systemd --user` (resource limits), `notify-send`.

## Using it

Double-click an AppImage (with `ajail on`) and choose **Install** or **Run once**. Terminal launches never prompt.

- **Install** unpacks the app once and adds it to your app menu with its name, icon and file types. The AppImage goes to the Trash unless you untick that. Open a newer file and choose **Update** to upgrade; data and settings are kept.
- **Run once** runs it without installing and deletes the unpacked copy afterwards. Tick **Don't ask again** to skip the window for that app.
- **Use less disk space** (experimental) keeps the AppImage compressed and mounts it with `squashfuse` at launch. Smaller, slower, and the image is read outside the sandbox, so only use it for apps you trust.

```bash
ajail FILE.AppImage [args]       # open (prompts when double-clicked)
ajail run-file [--once] [--mount] FILE
ajail install FILE [--trash] [--mount]
ajail run NAME [args]
ajail uninstall NAME [--purge]   # --purge moves its data to the Trash
ajail on | off | status | check
ajail preview [NAME]             # print the bwrap command
ajail-gui                        # permissions, logs
```

Files passed as arguments (or via "Open with") are shared for that run only. Names come from the file name minus version and arch: `Foo-1.2.3-x86_64.AppImage` is `Foo`. Only type 2 AppImages; ajail refuses to run as root.

## What the sandbox does

- Only `/usr` and a few `/etc` files are visible; the environment is rebuilt. Your real `$HOME` is hidden except for folders you share. Each app's home is `~/.appimage-homes/NAME`.
- The AppImage is unpacked inside its own sandbox (no network, no home).
- A seccomp blocklist denies `bpf`, `perf_event_open`, `kexec_load` and similar.
- Notifications and portals go through `xdg-dbus-proxy`, which only lets through the calls listed under Permissions.
- The private home is tied to the app's `Name=`. A different app with the same file name is refused rather than inheriting the first one's data.

## Permissions

Plain `key=value` files: `~/.config/ajail/default.conf` (all apps) and `~/.config/ajail/apps/NAME.conf` (one app). The app file overrides the default; list entries add up, except `xdg`.

| Key | Default | Grants |
| --- | --- | --- |
| `net` | 1 | Network, including localhost and your LAN |
| `net_private` | 0 | With `net`: internet but not services on this computer, via `pasta`. Your LAN stays reachable. App won't start if `pasta` is missing |
| `sound` | 1 | PipeWire/PulseAudio (includes microphone) |
| `gpu` | 1 | `/dev/dri`, NVIDIA devices |
| `x11` | 0 | X11 socket (weaker isolation) |
| `portals` | 1 | Notifications and a fixed set of portals: settings, open links, file chooser, print, background, and a few more |
| `portal_screenshot` | 1 | With `portals`: Screenshot portal |
| `portal_screencast` | 1 | With `portals`: ScreenCast portal (screen sharing) |
| `tray` | 0 | Tray icon, media keys |
| `camera` | 0 | `/dev/video*` and the Camera portal |
| `input` | 0 | `/dev/input` |
| `ssh_agent` | 0 | Your SSH agent; the app can sign as you |
| `printing` | 0 | CUPS socket |
| `theme` | 1 | Read-only fonts, icons, themes |
| `userns` | 1 | App may create its own sandbox (Electron/Chromium need it); `0` needs bubblewrap 0.8+ |
| `tmp_home` | 0 | Private home is erased on exit |
| `privacy` | 0 | Minimal `/etc/passwd`, `/etc/group`, `/etc/hosts`; hostname `ajail` |
| `harden` | 0 | Also blocks `io_uring_*`, `userfaultfd`, `process_vm_*`, `kcmp`. May break apps |

Limits (`0` = none, needs `systemd-run --user`): `mem_max=2048` (MB), `cpu_max=150` (% of one core), `tasks_max=300`.

List keys:

```ini
allow=/home/me/Projects          # read & write
allow_ro=/media/me/usb/photos    # read-only
xdg=download                     # download, documents, pictures, music, videos, desktop
xdg=pictures:ro                  # read-only
xdg=documents:none               # cancel a folder granted in default.conf
env=MY_VAR=value
talk=org.gnome.SessionManager    # extra D-Bus name
portal=Location                  # opt-in portal, one line each
```

Shared paths must be non-hidden items in your home or under `/mnt/`, `/media/`, `/run/media/$USER/`; others are skipped with a warning.

**Opt-in portals.** `Location`, `GlobalShortcuts`, `RemoteDesktop`, `InputCapture`, `Wallpaper`, `DynamicLauncher` and `Secret` are blocked unless you add `portal=NAME`. They need `portals=1`. Unknown names are ignored with a warning. `RemoteDesktop` and `InputCapture` can control or capture your input, and `Secret` can read the desktop's stored secrets, so enable them only for apps that need them. The desktop may still show its own prompt. The Secret *service* (`org.freedesktop.secrets`) stays blocked. In `ajail-gui` each portal is a switch on the app's page; one enabled in the default settings shows as on and locked on every app.

**Reset.** *Reset all settings…* on the main page of `ajail-gui` clears `default.conf` and `ui.conf`. Each app's own permissions and data have their own Reset buttons.

## Self-test

```bash
./make-selftest && ajail ./ajail-selftest.AppImage
```

Prints what the sandbox exposes and saves it to `selftest-report.txt` in the app's private home. Opt-in portals should read `blocked`; `EXPOSED` means one is reachable without being enabled. The portal rows only appear when the app has a session bus (`portals=1`).

## Files

| Path | Contents |
| --- | --- |
| `~/.config/ajail/` | Settings, app identities, `ui.conf` (install-window choices) |
| `~/.appimage-homes/NAME` | Private home |
| `~/.local/share/ajail/apps/` | Installed apps (delete a folder to remove one) |
| `~/.local/share/ajail/extracted/` | Cache for apps run without installing; safe to delete |
| `~/.local/share/applications/ajail-NAME.desktop` | Generated launcher (name, categories, file types and icon only; never the app's `Exec`) |
| `~/.local/state/ajail/ajail.log` | Launch log and app stderr |

If an app dies within 10 seconds, ajail sends a notification that opens its log in `ajail-gui`.

## Limitations

A convenience layer over bubblewrap, not a security guarantee. `net` gives the app your network; `net_private` hides this computer's services but not your LAN or the internet. `x11`, `ssh_agent`, `input` and broad folder shares weaken isolation. Compressed installs read the image outside the sandbox. Resource limits protect your session, not your privacy. Check the result with the self-test or `ajail preview NAME`.
