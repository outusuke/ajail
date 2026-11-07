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

## Requirements

- Linux with a Wayland session (X11 can be enabled per app)
- Python 3
- `bubblewrap` and `squashfs-tools`
- Optional: `xdg-dbus-proxy` (notifications, portals, tray), `notify-send` (failure popups)
- For `ajail-gui`: PyGObject, GTK 4 and libadwaita (1.5 or newer, for `Adw.AlertDialog`)

The error messages assume Fedora (`dnf`):

```bash
sudo dnf install bubblewrap squashfs-tools xdg-dbus-proxy libnotify python3-gobject gtk4 libadwaita
```

Other distros need the equivalent packages.

## Install

`ajail` expects to live at `/usr/local/bin/ajail` and be owned by root (`ajail on` checks this).

```bash
sudo install -m 755 -o root -g root ajail ajail-gui /usr/local/bin/
ajail check
```

`ajail check` reports what is available and what is missing.

## Usage

```bash
ajail FILE.AppImage [args]   # run one AppImage sandboxed
ajail on                     # force every AppImage through ajail (asks for your password)
ajail off                    # undo that
ajail status                 # is forcing on or off
ajail check                  # check the setup
ajail preview [NAME]         # print the bwrap command that would run
ajail name FILE.AppImage     # show the name ajail uses for a file
ajail-gui                    # edit permissions, view logs
```

Files or folders passed as arguments (or opened with "Open with") are shared with the app for that run only.

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
| `sound` | 1 | PipeWire/PulseAudio (includes microphone) |
| `gpu` | 1 | `/dev/dri` and NVIDIA devices |
| `x11` | 0 | X11 socket; weaker isolation |
| `portals` | 1 | Notifications and desktop portals via the D-Bus proxy |
| `tray` | 0 | Tray icon and media keys |
| `camera` | 0 | `/dev/video*` |
| `input` | 0 | `/dev/input` (controllers, raw input) |
| `ssh_agent` | 0 | Your SSH agent socket; the app can sign as you |
| `printing` | 0 | CUPS socket |
| `theme` | 1 | Read-only access to your fonts, icons and themes |
| `userns` | 1 | Lets the app create its own sandbox (Electron/Chromium need this); `0` needs bubblewrap 0.8+ |

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

## Self-test

```bash
./make-selftest              # writes ajail-selftest.AppImage (needs mksquashfs)
ajail ./ajail-selftest.AppImage
```

The test app isn't a real AppImage runtime, so it only runs through `ajail`. It reports what the sandbox exposes (network, sockets, devices, home folders, D-Bus, seccomp, nested sandboxes) and saves the report to `selftest-report.txt` in the app's private home.

## Files and logs

| Path | Contents |
| --- | --- |
| `~/.config/ajail/` | Settings and remembered app identities |
| `~/.appimage-homes/NAME` | Private home per app |
| `~/.local/share/ajail/extracted/` | Extracted AppImages (safe to delete; rebuilt on next launch) |
| `~/.local/state/ajail/ajail.log` | Launch log and app stderr |

If an app fails within 10 seconds of starting, ajail sends a notification that opens `ajail-gui` on that app's log.

## Limitations

This is a convenience layer over bubblewrap, not a security guarantee. Enabling `x11`, `ssh_agent`, `input` or broad folder shares weakens isolation, and `net` gives the app your network. Check the result with the self-test or `ajail preview NAME`.
