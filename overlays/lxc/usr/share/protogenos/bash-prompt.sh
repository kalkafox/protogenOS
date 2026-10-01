# protogenOS prompt: visor-red user@host, white directory. Sourced by
# /etc/bash.bashrc; a PS1 set in ~/.bashrc still takes precedence.
if [[ "${PS1}" == '[\u@\h \W]\$ ' ]]; then
    PS1='\[\e[38;2;213;31;61m\]\u@\h\[\e[0m\] \[\e[38;2;245;241;242m\]\W\[\e[0m\] \$ '
fi
