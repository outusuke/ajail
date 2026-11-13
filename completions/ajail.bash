# bash completion for ajail. SPDX-License-Identifier: GPL-3.0-or-later
# Install: sudo install -m 644 completions/ajail.bash /usr/share/bash-completion/completions/ajail
#          (or per user: ~/.local/share/bash-completion/completions/ajail)
_ajail_apps() { ajail list 2>/dev/null | awk '{print $1}'; }

_ajail() {
    local cur=${COMP_WORDS[COMP_CWORD]} cmd=${COMP_WORDS[1]} i
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=( $(compgen -W "on off status check preview list stop name install uninstall restore run run-file desktop help" -- "$cur") )
        compopt -o filenames 2>/dev/null; COMPREPLY+=( $(compgen -f -X '!*.[aA]pp[iI]mage' -- "$cur") )
        return
    fi
    case $cmd in
        run)
            if [[ $cur == -* ]] && (( COMP_CWORD == 2 )); then COMPREPLY=( $(compgen -W "--compat-env" -- "$cur") )
            elif (( COMP_CWORD == 2 )) || { (( COMP_CWORD == 3 )) && [[ ${COMP_WORDS[2]} == --compat-env ]]; }; then
                COMPREPLY=( $(compgen -W "$(_ajail_apps)" -- "$cur") )
            fi ;;
        run-file)
            if [[ $cur == -* ]]; then COMPREPLY=( $(compgen -W "--once --mount --compat-env" -- "$cur") )
            else compopt -o filenames 2>/dev/null; COMPREPLY=( $(compgen -f -- "$cur") ); fi ;;
        install)
            if [[ $cur == -* ]]; then COMPREPLY=( $(compgen -W "--trash --mount --move" -- "$cur") )
            else compopt -o filenames 2>/dev/null; COMPREPLY=( $(compgen -f -- "$cur") ); fi ;;
        uninstall)
            if [[ $cur == -* ]]; then COMPREPLY=( $(compgen -W "--purge --discard-image" -- "$cur") )
            else COMPREPLY=( $(compgen -W "$(_ajail_apps)" -- "$cur") ); fi ;;
        restore)
            if [[ $cur == -* ]]; then COMPREPLY=( $(compgen -W "--keep --to" -- "$cur") )
            elif [[ ${COMP_WORDS[COMP_CWORD-1]} == --to ]]; then compopt -o dirnames 2>/dev/null; COMPREPLY=( $(compgen -d -- "$cur") )
            else COMPREPLY=( $(compgen -W "$(_ajail_apps)" -- "$cur") ); fi ;;
        preview|stop) (( COMP_CWORD == 2 )) && COMPREPLY=( $(compgen -W "$(_ajail_apps)" -- "$cur") ) ;;
        desktop) COMPREPLY=( $(compgen -W "--install-menu --remove-menu" -- "$cur") ) ;;
        name) compopt -o filenames 2>/dev/null; COMPREPLY=( $(compgen -f -- "$cur") ) ;;
    esac
}
complete -F _ajail ajail
