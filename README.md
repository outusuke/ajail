# ajail

Run AppImages in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox. Each app gets a private home and its own permissions, set Flatseal-style.

- `ajail`: launcher and CLI
- `ajail-gui`: GTK4 + libadwaita permission editor and installer
- `make-selftest`: builds a test AppImage that reports what the sandbox exposes

## Install

```bash
sudo dnf install bubblewrap squashfs-tools xdg-dbus-proxy libnotify python3-gobject gtk4 libadwaita   # Fedora
sudo install -m 755 -o root -g root ajail ajail-gui /usr/local/bin/
ajail check      # lists what is missing, with the install command for your distro
ajail on         # optional: send every AppImage through ajail (asks for your password)
```

`ajail` must be at `/usr/local/bin/ajail`, owned by root. Debian/Ubuntu and Arch use the same package names for the core tools.

**Required:** Linux, Wayland (X11 is a per-app option), Python 3, `bubblewrap`, `squashfs-tools`.
**Optional:** `xdg-dbus-proxy` (notifications, portals, tray), `passt` (`net_private`), `squashfuse` + `fuse3` (compressed installs), `systemd --user` (resource limits), `notify-send` (failure popups).

## Using it

With `ajail on`, double-click an AppImage and choose **Install** or **Run once**. Terminal launches never prompt. Only type 2 AppImages work, and ajail refuses to run as root.

| Choice | What happens |
| --- | --- |
| **Install** | Unpacks the app once and adds it to your app menu (name, icon, file types). Afterwards ajail asks whether to move the AppImage to the Trash; it can be restored from there. Opening a newer file offers **Update**; data and settings are kept. |
| **Run once** | Unpacks inside the sandbox, runs, and deletes the unpacked copy on exit. Tick **Don't ask again** to skip this window for that app. Never uses compressed mode. |

Settings for installing are under **Installing** on the main page of `ajail-gui`:

- **After installing, the AppImage file:** `Ask me` (default), `Move to Trash`, `Keep it`, or `Move into app folder` (only with compressed installs).
- **Use less disk space:** compressed installs, see below.

Files passed as arguments or through "Open with" are shared for that run only. The app name is the file name minus version and architecture: `Foo-1.2.3-x86_64.AppImage` becomes `Foo`.

### Compressed installs (experimental)

Off by default. Needs `squashfuse`, `fuse3` and access to `/dev/fuse`.

- Keeps the AppImage as-is in `~/.local/share/ajail/apps/NAME/image` and mounts it at each launch. Smaller on disk, slower to start.
- **Trust:** the image is read by `squashfuse` outside the sandbox. Use it only for apps you trust. Installing still unpacks the app briefly to read its launcher and icon, then deletes that copy.
- Set it for all apps under **Installing**. Override per app on the page of an app that is not installed yet, or with `mount.NAME=0|1` in `ui.conf`. A per-app value beats the global one.
- **Move into app folder** moves the file into ajail's folder, so there is no duplicate. Files on removable or network drives, symlinks and files in folders you can't write to are copied instead.
- **Get the file back:** press **Restore…** on the app's page and pick a folder. A copy is placed there and the app keeps working. Uninstalling the app also puts the file back at its original path first.

### Command line

```bash
ajail FILE.AppImage [args]       # open; prompts when double-clicked
ajail run-file [--once] [--mount] FILE
ajail install FILE [--trash] [--mount [--move]]
ajail run NAME [args]            # run an installed app
ajail list | stop NAME | name FILE
ajail uninstall NAME [--purge] [--discard-image]
ajail restore NAME [--keep] [--to FOLDER]
ajail on | off | status | check
ajail preview [NAME]             # print the bwrap command
```

- `install --trash` moves the AppImage to the Trash; `--mount` makes it a compressed install; `--move` (with `--mount`) moves the file into ajail's folder.
- `run-file --once` never mounts, even with `--mount`.
- `uninstall --purge` moves the app's data to the Trash. It refuses to delete an AppImage that was moved into ajail unless you pass `--discard-image`.
- `restore` puts a moved AppImage back and uninstalls the app. `--keep` copies it back and leaves the app installed. `--to` picks the folder.

## What the sandbox does

- Only `/usr` and a few `/etc` files are visible, and the environment is rebuilt. Your real `$HOME` is hidden except for folders you share. Each app's home is `~/.appimage-homes/NAME`.
- Unpacking happens in a separate sandbox with no network and no home.
- A seccomp blocklist denies `bpf`, `perf_event_open`, `kexec_load` and similar calls.
- Notifications and portals go through `xdg-dbus-proxy`, which allows only the calls listed under Permissions.
- The private home is tied to the app's `Name=`. A different app with the same file name is refused instead of inheriting the first one's data.

## Permissions

Plain `key=value` files: `~/.config/ajail/default.conf` (all apps) and `~/.config/ajail/apps/NAME.conf` (one app). The app file overrides the default. List entries add up, except `xdg`. Spaces around `=` are fine and `#` starts a comment (so a path containing ` #` can't be stored). A line ajail can't use (unknown key, a value other than `0`/`1`, no `=`) is skipped with a warning in the terminal and on the Log page, so a typo is never silent.

| Key | Default | Grants |
| --- | --- | --- |
| `net` | 1 | Network, including localhost and your LAN |
| `net_private` | 0 | With `net`: internet but not services on this computer, via `pasta`. Your LAN stays reachable. The app won't start if `pasta` is missing |
| `sound` | 1 | PipeWire/PulseAudio (includes the microphone) |
| `gpu` | 1 | `/dev/dri`, NVIDIA devices |
| `x11` | 0 | X11 socket (weaker isolation) |
| `portals` | 1 | Notifications and a fixed set of portals: settings, open links, file chooser, print, background and a few more |
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

### Defaults that favor compatibility

Two defaults trade isolation for apps that work out of the box. Both are one switch away from stricter.

| Key | Default | What you give up | Stricter setting |
| --- | --- | --- | --- |
| `userns` | 1 | The app can create its own user namespaces. Electron and Chromium apps (Discord, Slack, VS Code, most browsers) need this for their internal sandbox, and break without it. Nested namespaces also give the app a larger kernel attack surface to try. | `userns=0` (needs bubblewrap 0.8+). Try it per app; if an app won't start or shows a sandbox error, turn it back on. |
| `harden` | 0 | `io_uring_*`, `userfaultfd`, `process_vm_readv/writev` and `kcmp` stay available. They are rarely needed, but they have been a source of kernel bugs. Blocking them can slow down or break some apps (databases, some game engines and runtimes use `io_uring`). | `harden=1`. Run the app, and check the Log page if it fails. |

Other defaults that open things up on purpose: `net=1`, `sound=1` (includes the microphone), `gpu=1`, `portals=1` with `portal_screenshot=1` and `portal_screencast=1` (the desktop still asks you each time), `theme=1`. Run the [self-test](#self-test) after changing any of them.

For an app you don't fully trust, a stricter starting point is:

```ini
userns=0
harden=1
net=0
sound=0
```

Resource limits (`0` = none, needs `systemd-run --user`): `mem_max=2048` (MB), `cpu_max=150` (% of one core), `tasks_max=300`.

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
portal=-Location                 # in an app file: turn off a portal enabled in default.conf
```

Shared paths must be non-hidden items in your home or under `/mnt/`, `/media/`, `/run/media/$USER/`. Others are skipped with a warning.

### Opt-in portals

`Location`, `GlobalShortcuts`, `RemoteDesktop`, `InputCapture`, `Wallpaper`, `DynamicLauncher` and `Secret` are blocked unless enabled with `portal=NAME`. They need `portals=1`, and unknown names are ignored with a warning. The desktop may still show its own prompt.

- `RemoteDesktop` and `InputCapture` can control or capture your keyboard and mouse.
- `Secret` can read the desktop's stored secrets. The Secret *service* (`org.freedesktop.secrets`) stays blocked.

In `ajail-gui` each portal is a switch on the app's page. A portal enabled in the default settings shows as on for every app, and turning it off on an app's page writes `portal=-NAME` for that app only.

### Resetting

**Reset all settings…** on the main page of `ajail-gui` clears `default.conf`, every app's permission file and `ui.conf` (open mode, install and disk-space choices). It does not touch app data or installed apps. Each app's page has its own **Reset** buttons for permissions and data.

## Self-test

```bash
./make-selftest && ajail ./ajail-selftest.AppImage
```

Prints what the sandbox exposes and saves it to `selftest-report.txt` in the app's private home. Opt-in portals should read `blocked`; `EXPOSED` means one is reachable without being enabled. Portal rows only appear when the app has a session bus (`portals=1`).

## Tests

```bash
python3 test_portals.py     # unit tests, no sandbox needed
python3 test_parse.py       # settings parser: spaces, comments, warnings
python3 test_hostile.py     # hostile AppImages (needs bubblewrap and squashfs-tools; run as a normal user)
python3 test_build.py       # ajail is up to date with src/, and the layer rule holds
```

`test_hostile.py` builds AppImages with links pointing outside the app, link loops, huge icons and `.desktop` files, injected `Exec=` lines, odd file names and impostors of an installed app. It runs a copy of `ajail` in a temporary home and checks that nothing outside the AppImage was copied, shown or run, and that the canary files it planted stayed untouched.

## Development

`ajail` is generated from `src/*.py` so the security-critical part can be read on its own (`src/20_core.py`). Edit the
files in `src/`, then run `./build.py` and commit the regenerated `ajail`. Do not edit `ajail` directly.
`./build.py --check` verifies that the core only depends on lower layers. See [src/README.md](src/README.md) for the
review order.

## Files

| Path | Contents |
| --- | --- |
| `~/.config/ajail/` | Settings, app identities, `ui.conf` (choices made in `ajail-gui`) |
| `~/.appimage-homes/NAME` | Private home |
| `~/.local/share/ajail/apps/NAME/` | Installed app: `root/` (unpacked) or `image` (compressed) |
| `~/.local/share/ajail/extracted/` | Cache for apps run without installing; safe to delete |
| `~/.local/share/applications/ajail-NAME.desktop` | Generated launcher (name, categories, file types, icon; never the app's `Exec`) |
| `~/.local/state/ajail/ajail.log` | Launch log and app stderr |

If an app exits with an error within 10 seconds, ajail sends a notification that opens its log in `ajail-gui`.

## Limitations

ajail is a convenience layer over bubblewrap, not a security guarantee.

- `net` gives the app your network. `net_private` hides this computer's services, not your LAN or the internet.
- `x11`, `ssh_agent`, `input`, broad folder shares and the `RemoteDesktop`, `InputCapture` and `Secret` portals weaken isolation.
- Compressed installs read the image outside the sandbox.
- Resource limits protect your session, not your privacy.

Check the result with the self-test or `ajail preview NAME`.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
