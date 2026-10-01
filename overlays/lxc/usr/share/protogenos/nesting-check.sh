# Warn when the container was started without the Proxmox nesting feature.
# systemd then cannot set up per-service credentials, so journald and most
# other services fail with exit status 243 (CREDENTIALS) and nothing works.
if [[ $- == *i* ]] \
    && [[ "$(systemctl show --property=ExecMainStatus --value systemd-journald.service 2>/dev/null)" == 243 ]]; then
    printf '\e[1;31mprotogenOS: this container needs the Proxmox "nesting" feature.\e[0m\n' >&2
    printf 'systemd services cannot start without it. On the Proxmox host, run\n' >&2
    printf '  pct set <vmid> --features nesting=1,keyctl=1 && pct reboot <vmid>\n' >&2
    printf 'or enable Options > Features > Nesting, then restart the container.\n' >&2
fi
