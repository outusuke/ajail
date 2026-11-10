# ajail source layout

`ajail` is still installed as ONE file. The source is split into layers so it can be audited in order of importance.

    ./build.py          # check layering, then write ./ajail from src/*.py
    ./build.py --check  # layering only (run in CI)

| Fragment | What | How to review |
|---|---|---|
| `src/20_core.py` | **Security core**: config parsing and validation, path rules, `bwrap_args`, seccomp filter, D-Bus proxy rules, pasta/net, xauth scoping, identity check, sandboxed extraction | Line by line. Every change here needs a deliberate review. |
| `src/50_run.py` | `launch`: starts the sandbox, handles the app's output streams, cleanup | Second tier. |
| `src/00_config.py`, `10_util.py` | Paths, defaults, syscall tables; logging, notifications | Read once; the tables define policy. |
| `src/30_host.py` | Reads desktop settings for the sandbox, resource limits, stale-file sweep | Skim. |
| `src/40_images.py`, `60_desktop.py`, `70_commands.py` | Mounting/moving images, desktop entries, CLI commands | Normal review. They run outside the sandbox but cannot change what the sandbox allows. |

## Layer rule

A fragment may only use names defined in itself or in a lower-numbered fragment. `build.py` enforces it, so
`20_core` can never silently depend on install, GUI or command code. If you need something from a higher layer in the
core, that is a design question, not a quick import.

## Editing

Edit `src/*.py`, then run `./build.py`, and install the generated `ajail`. Do not edit the generated file.
Section headers (`# ======== NN_name`) in the built file show where each fragment starts.
