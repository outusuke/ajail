# fish completion for ajail. SPDX-License-Identifier: GPL-3.0-or-later
# Install: cp completions/ajail.fish ~/.config/fish/completions/
function __ajail_apps
    ajail list 2>/dev/null | string replace -r '\s+.*' ''
end
set -l c "not __fish_seen_subcommand_from on off status check preview list stop name install uninstall restore run run-file export import desktop help"
complete -c ajail -f -n $c -a "on off status check preview list stop name install uninstall restore run run-file export import desktop help"
complete -c ajail -n $c -F
complete -c ajail -n "__fish_seen_subcommand_from run" -l compat-env -d "Pass graphics and toolkit variables from this session"
complete -c ajail -n "__fish_seen_subcommand_from run" -l allow-32bit -d "Let a 32-bit app run (weakens the socket filter)"
complete -c ajail -f -n "__fish_seen_subcommand_from run" -a "(__ajail_apps)"
complete -c ajail -n "__fish_seen_subcommand_from run-file" -l once -d "Delete the unpacked copy on exit"
complete -c ajail -n "__fish_seen_subcommand_from run-file" -l mount -d "Mount the file instead of unpacking it"
complete -c ajail -n "__fish_seen_subcommand_from run-file" -l compat-env -d "Pass graphics and toolkit variables from this session"
complete -c ajail -n "__fish_seen_subcommand_from run-file" -l allow-32bit -d "Let a 32-bit app run (weakens the socket filter)"
complete -c ajail -n "__fish_seen_subcommand_from install" -l trash -d "Move the AppImage to the Trash"
complete -c ajail -n "__fish_seen_subcommand_from install" -l mount -d "Keep it compressed, mount at launch"
complete -c ajail -n "__fish_seen_subcommand_from install" -l move -d "With --mount: move the file into ajail"
complete -c ajail -f -n "__fish_seen_subcommand_from uninstall restore preview stop" -a "(__ajail_apps)"
complete -c ajail -n "__fish_seen_subcommand_from uninstall" -l purge -d "Also move the data to the Trash"
complete -c ajail -n "__fish_seen_subcommand_from uninstall" -l discard-image -d "Delete a moved AppImage"
complete -c ajail -f -n "__fish_seen_subcommand_from desktop" -l install-menu -d "Add ajail-gui to the app menu"
complete -c ajail -f -n "__fish_seen_subcommand_from desktop" -l remove-menu -d "Remove it again"
complete -c ajail -n "__fish_seen_subcommand_from restore" -l keep -d "Leave the app installed"
complete -c ajail -n "__fish_seen_subcommand_from restore" -l to -r -F -d "Folder for the AppImage"
complete -c ajail -f -n "__fish_seen_subcommand_from export" -a "(__ajail_apps)"
complete -c ajail -f -n "__fish_seen_subcommand_from export" -l default -d "Export the default settings only"
complete -c ajail -f -n "__fish_seen_subcommand_from export" -l all -d "Export every settings file"
complete -c ajail -n "__fish_seen_subcommand_from export" -s o -r -F -d "Write to a file instead of the terminal"
complete -c ajail -f -n "__fish_seen_subcommand_from export import" -l force -d "Replace an existing file or settings that differ"
complete -c ajail -n "__fish_seen_subcommand_from import" -F
