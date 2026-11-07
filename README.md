# ajail

Run AppImages inside a [bubblewrap](https://github.com/containers/bubblewrap) sandbox, each with its own persistent private home. Permissions are set per app, Flatseal-style, with an optional GTK4 front-end.

- **`ajail`**: the launcher and CLI
- **`ajail-gui`**: GTK4 + libadwaita permission editor
- **`make-selftest`**: builds a tiny AppImage that reports what the sandbox lets through

## How it works

- The AppImage is extracted once (squashfs, inside its own sandbox) and cached in `~/.local/share/ajail/extracted`. It is re-extracted when the file changes.
- Each app gets a private home at `~/.appimage-homes/NAME`. The real `$HOME` is hidden except for folders you share.
- Only the system paths the app needs are bound in (`/usr` and a short list of `/etc` files). The environment is cleared and rebuilt.
- A seccomp blocklist (similar to Flatpak's) denies syscalls such as `bpf`, `perf_event_open` and `kexec_load`.
- Notifications and portals go through `xdg-dbus-proxy`, which filters what the app may reach on the session bus.
- The private home is tied to the app's `Name=` from its `.desktop` file. A different app that happens to have the same file name is refused instead of inheriting the first app's data and permissions.
- With `ajail on`, a `binfmt_misc` rule makes the kernel route every type 2 AppImage through `ajail`, however it is started (double-click, `./app.AppImage`, "Open with").
- Double-clicking an AppImage that isn't installed opens a window offering **Install** or **Run once**. Launches from a terminal never prompt.

## Requirements

- Linux with a Wayland session (X11 can be enabled per app)
- Python 3
- `bubblewrap` and `squashfs-tools`
- Optional: `xdg-dbus-proxy` (notifications, portals, tray), `notify-send` (failure popups), `squashfuse` and `fuse3` (compressed installs, experimental), `passt` (provides `pasta`, needed for **Keep localhost private**)
- Optional: systemd with a user session (`systemd-run --user`), for the memory, CPU and process limits
- For `ajail-gui`: PyGObject, GTK 4 and libadwaita (1.5 or newer, for `Adw.AlertDialog`)

On Fedora (the distro ajail was written on):

```bash
sudo dnf install bubblewrap squashfs-tools xdg-dbus-proxy libnotify python3-gobject gtk4 libadwaita
sudo dnf install squashfuse fuse3    # optional, for compressed installs
sudo dnf install passt               # optional, for "Keep localhost private"
```

On other distros, install whatever your distro calls the same things: `bubblewrap`, `squashfs-tools` (for `unsquashfs`), `xdg-dbus-proxy`, `notify-send` (usually in a libnotify package), and for `ajail-gui` PyGObject with the GTK 4 and libadwaita bindings. Compressed installs also need `squashfuse` and `fuse3`. Package names differ between distros, so no commands are given for them here.

For the tools ajail itself needs, its error messages and `ajail check` suggest the matching command on Fedora, Debian/Ubuntu and Arch (read from `/etc/os-release`, derivatives included), where those packages have the same names. Everywhere else they just name the packages.

## Install

`ajail` expects to live at `/usr/local/bin/ajail` and be owned by root (`ajail on` checks this).

```bash
sudo install -m 755 -o root -g root ajail ajail-gui /usr/local/bin/
ajail check
```

To put **AppImage Jail** in your application menu, open `ajail-gui` (from a terminal the first time) and turn on **Show AppImage Jail in the app menu**. It writes a launcher and icon under `~/.local/share/` and needs no root; turn the switch off to remove them.

`ajail check` reports what is available and what is missing.

## Installing apps

Installing works like dragging an app into Applications:

1. Double-click an AppImage (with `ajail on`), or press **+** in `ajail-gui`, or run `ajail install FILE.AppImage`.
2. Choose **Install**. The app is unpacked once, added to your application menu with its own name and icon, and can handle its file types.
3. The AppImage is moved to the Trash afterwards by default (untick the box to keep it). The installed copy doesn't need it.
4. To update, open the newer AppImage and choose **Update**. Settings and data are kept.
5. To remove it, press **Uninstall…** on the app's page, or run `ajail uninstall NAME`. You choose whether to keep its data or move it to the Trash.

**Run once** runs the AppImage without installing it and deletes its unpacked copy when the app closes, so nothing is left behind except the app's private data. If you'd rather never see the window, tick **Don't ask again for NAME** in it, or turn off **Ask before opening this AppImage** on the app's page in `ajail-gui`; double-clicking that AppImage then runs it immediately and installing is done from the GUI. The choice is per app. **Ask before opening a new AppImage** on the main page is only the default for apps without their own setting. To get the window back for an app, turn its switch on again.

The install window also has **Use less disk space** (experimental), see below. It applies to both Install and Run once: with it on, Run once mounts the AppImage instead of unpacking it, so nothing is written to disk. The window remembers the Trash choice globally and **Use less disk space** per app. The same two settings are on the app's page for apps that aren't installed yet, and **Launch** there follows them. With the prompt off, double-clicking an AppImage mounts it if that app has **Use less disk space** on, and launches from a terminal never do. These per-app choices are stored as `open_mode.NAME=` and `mount.NAME=` lines in `~/.config/ajail/ui.conf`, and are removed with the app's data on `ajail uninstall --purge`.

The launcher is generated by ajail. Only the app's name, categories, file types and icon are copied from the AppImage's own `.desktop` file, never its `Exec` line or actions.

## Compressed installs (experimental)

By default an installed app is unpacked, which takes roughly 2 to 4 times the size of the AppImage. With **Use less disk space** (or `ajail install --mount FILE`), ajail keeps a copy of the AppImage instead and mounts its filesystem with `squashfuse` each time the app starts. The mount is removed when the app exits, and mounts left behind by a crash are cleaned up on the next launch.

In a test with a 15 MB AppImage holding about 54 MB of files, the compressed install used 15 MB instead of 54 MB, and reading every file through the mount took 0.36 s instead of 0.03 s. Real apps will differ.

Trade-offs:
- **Weaker isolation for one step.** `squashfuse` reads the image as your user, outside the sandbox, every time the app starts. A bug in its parser could be exploited by a hostile AppImage before the sandbox exists. Unpacking (the default) does that work inside a sandbox with no network and no access to your home. Only use compressed installs for AppImages you trust.
- **Slower starts and file access**, because files are decompressed as they are read.
- **Needs `squashfuse`, `fuse3` and access to `/dev/fuse`.** `ajail check` tells you whether they are available.
- **To switch an app between the two modes**, install its AppImage again with the other choice.

## Usage

```bash
ajail FILE.AppImage [args]   # open an AppImage (offers Install or Run once when double-clicked, runs it from a terminal)
ajail run-file [--once] [--mount] FILE # run an AppImage, never offering to install it (--once deletes the unpacked copy on exit, --mount mounts it instead of unpacking)
ajail install FILE           # unpack it and add it to the app menu (--trash: move the file to the Trash, --mount: keep it compressed)
ajail run NAME [args]        # run an installed app
ajail uninstall NAME         # remove it from the app menu (--purge also moves its data to the Trash)
ajail on                     # force every AppImage through ajail (asks for your password)
ajail off                    # undo that
ajail status                 # is forcing on or off
ajail check                  # check the setup
ajail preview [NAME]         # print the bwrap command that would run
ajail name FILE.AppImage     # show the name ajail uses for a file
ajail-gui                    # edit permissions, view logs
```

Files or folders passed as arguments (or opened with "Open with") are shared with the app for that run only. Installed apps get "Open with" support through the launcher's `%U` entry.

App names are derived from the file name with the extension, version, and architecture stripped. `Foo-1.2.3-x86_64.AppImage` becomes `Foo`.

Only type 2 AppImages are supported. ajail refuses to run as root.

## Permissions

Settings live in plain `key=value` files:

- `~/.config/ajail/default.conf` for all apps
- `~/.config/ajail/apps/NAME.conf` for one app

Precedence is built-in defaults, then `default.conf`, then the app's file. List entries add up, except `xdg`, which is resolved per folder.

| Key | Default | Grants |
| --- | --- | --- |
| `net` | 1 | Network access (including localhost and your LAN) |
| `net_private` | 0 | With `net`: own network via `pasta`, so the app reaches the internet but not services on this computer (localhost). Your local network is still reachable. Fails to start if `pasta` is missing |
| `sound` | 1 | PipeWire/PulseAudio (includes microphone) |
| `gpu` | 1 | `/dev/dri` and NVIDIA devices |
| `x11` | 0 | X11 socket; weaker isolation |
| `portals` | 1 | Notifications and a fixed list of desktop portals (settings, open links, file chooser, print, background and a few more) via the D-Bus proxy. Other portals, such as RemoteDesktop, Location and Secret, are not reachable |
| `portal_screenshot` | 1 | With `portals`: the Screenshot portal. The desktop shows its own prompt |
| `portal_screencast` | 1 | With `portals`: the ScreenCast portal (screen sharing in Discord, Teams, Slack). The desktop shows its own picker |
| `tray` | 0 | Tray icon and media keys |
| `camera` | 0 | `/dev/video*`, and the Camera portal when `portals` is on |
| `input` | 0 | `/dev/input` (controllers, raw input) |
| `ssh_agent` | 0 | Your SSH agent socket; the app can sign as you |
| `printing` | 0 | CUPS socket |
| `theme` | 1 | Read-only access to your fonts, icons and themes |
| `userns` | 1 | Lets the app create its own sandbox (Electron/Chromium need this); `0` needs bubblewrap 0.8+ |
| `tmp_home` | 0 | The private home is a temporary filesystem, erased when the app closes. Shared folders are not affected |
| `privacy` | 0 | Minimal `/etc/passwd`, `/etc/group` and `/etc/hosts` (no real name) and the hostname `ajail` |
| `harden` | 0 | Also blocks `io_uring_*`, `userfaultfd`, `process_vm_readv/writev` and `kcmp`. Can slow down or break some apps |

Limits use the same files; `0` means no limit, and they need `systemd-run --user` (without it the app runs unlimited, with a warning):

```ini
mem_max=2048     # memory in MB
cpu_max=150      # percent of one CPU core
tasks_max=300    # processes and threads
```

List keys:

```ini
allow=/home/me/Projects          # read & write
allow_ro=/media/me/usb/photos    # read-only
xdg=download                     # standard folder, read & write
xdg=pictures:ro                  # standard folder, read-only
xdg=documents:none               # cancel a folder granted in default.conf
env=MY_VAR=value
talk=org.gnome.SessionManager    # extra D-Bus name the app may talk to
```

`xdg` kinds: `download`, `documents`, `pictures`, `music`, `videos`, `desktop`.

Shared paths must be non-hidden items inside your home, or live under `/mnt/`, `/media/` or `/run/media/$USER/`. Anything else is skipped with a warning.

To start over, press **Reset all settings…** on the main page of `ajail-gui`. It clears `default.conf` and `ui.conf` (the default permissions, the ask-before-opening setting and every app's remembered choices). Each app's own permissions and data are kept; those have their own **Reset** buttons on the app's page.

## Self-test

```bash
./make-selftest              # writes ajail-selftest.AppImage (needs mksquashfs)
ajail ./ajail-selftest.AppImage
```

The test app isn't a real AppImage runtime, so it only runs through `ajail`. It reports what the sandbox exposes (network, sockets, devices, home folders, D-Bus, desktop portals, seccomp, nested sandboxes) and saves the report to `selftest-report.txt` in the app's private home.

## Files and logs

| Path | Contents |
| --- | --- |
| `~/.config/ajail/` | Settings and remembered app identities |
| `~/.appimage-homes/NAME` | Private home per app |
| `~/.local/share/ajail/extracted/` | Cache for AppImages you ran directly from a terminal or with the prompt turned off (safe to delete; rebuilt on next launch). Run once leaves nothing here |
| `~/.local/share/ajail/apps/` | Installed apps. Deleting a folder here removes the app. Compressed installs hold `image` instead of `root` |
| `$XDG_RUNTIME_DIR/ajail-mnt/` | Temporary mountpoints for running compressed apps |
| `~/.local/share/applications/ajail-NAME.desktop` | Launcher of an installed app (icons go in `~/.local/share/icons/hicolor/`) |
| `~/.config/ajail/ui.conf` | The "ask before opening" setting and the last choices in the install window |
| `~/.local/state/ajail/ajail.log` | Launch log and app stderr |

If an app fails within 10 seconds of starting, ajail sends a notification that opens `ajail-gui` on that app's log.

## Limitations

This is a convenience layer over bubblewrap, not a security guarantee. **Keep localhost private** hides services on this computer but does not block your local network or the internet, and it relies on the `pasta` command line options listed in the source; test it with the self-test (`Host services visible` should show an isolated network) and report problems. The resource limits protect your session, not your privacy, and are skipped if systemd is unavailable. Compressed installs (experimental) read the AppImage outside the sandbox. Enabling `x11`, `ssh_agent`, `input` or broad folder shares weakens isolation, and `net` gives the app your network. Check the result with the self-test or `ajail preview NAME`.
