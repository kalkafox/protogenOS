# Proxmox LXC Template

protogenOS ships a headless server template for Proxmox VE containers. It is
the Server persona adapted to a container: the same console tools and
branding, with Docker preinstalled, and without the pieces a container cannot
use.

## What Is in the Image

| Area | Behavior |
| --- | --- |
| Packages | [`config/lxc.packages`](../config/lxc.packages): `base`, OpenSSH, `nano`, `vim` (also linked as `vi`), `sudo`, `which`, `wget`, `unzip`, `lsof`, `bind` (`dig`, `host`), `traceroute`, the Server persona tools, `fastfetch`, `docker`, `docker-compose` |
| Left out | Kernel, `linux-firmware`, bootloader, `smartmontools`, NetworkManager, `firewalld` |
| Networking | `systemd-networkd`; Proxmox writes `eth0.network`, `/etc/hostname` and `/etc/resolv.conf`. Boot waits at most 30 seconds for a link |
| Login | Root, with the password or SSH keys given to Proxmox at creation. `sshd` is installed and enabled and accepts both |
| pacman | `DisableSandboxFilesystem` and `DisableSandboxSyscalls` are set in `/etc/pacman.conf`; pacman's download sandbox fails inside Proxmox containers |
| Firewall | None inside the container; use the Proxmox firewall. Root can log in over SSH with a password, so do not expose port 22 to the internet without a firewall rule or keys-only login |
| systemd | `systemd-firstboot`, `sys-kernel-config.mount`, `sys-kernel-debug.mount` and `systemd-journald-audit.socket` are masked. The timezone defaults to UTC; Proxmox replaces it when one is chosen at creation |
| Branding | `os-release` (`ID=protogenos`, `VARIANT_ID=lxc`), `motd`, `issue`, fastfetch logo, red bash prompt |
| Updates | Official Arch repositories. The branding files are not packaged, so `pacman` does not update them |

The template contains no per-machine state. Proxmox writes SSH host keys when
it creates the container. On first boot, systemd creates a machine ID,
`sshdgenkeys.service` creates any SSH host keys that are still missing, and
`protogenos-pacman-init.service` creates and populates the pacman keyring.

Overlay files live in [`overlays/lxc/`](../overlays/lxc).

## Building

```bash
./scripts/build-lxc
```

The template is built in the same privileged Docker builder as the ISO and
shares its package cache. The output is
`out/protogenos-server_YYYYMMDD-1_amd64.tar.zst`, with a `.sha256` file next
to it. A new build replaces older templates in `out/`.

## Using It on Proxmox

Copy the template to a template storage, such as
`/var/lib/vz/template/cache/` for `local`, and create an unprivileged
container with the `nesting` and `keyctl` features:

```bash
pct create 200 local:vztmpl/protogenos-server_YYYYMMDD-1_amd64.tar.zst \
    --hostname proto \
    --ostype archlinux \
    --unprivileged 1 \
    --features nesting=1,keyctl=1 \
    --memory 1024 \
    --rootfs local-lvm:8 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --ssh-public-keys ~/.ssh/authorized_keys
pct start 200
```

In the web interface, the template shows up under **Create CT**. Leave
**Unprivileged container** checked, then enable **Nesting** and **keyctl**
under **Options → Features** before starting it.

`nesting` is required, not only for Docker. Without it, Proxmox's AppArmor
profile blocks the mounts systemd uses to pass credentials to services, so
journald, networking, the console and SSH all fail (Arch's own template
behaves the same). An interactive shell from `pct enter` then prints a
warning. Fix it on the host with:

```bash
pct set 200 --features nesting=1,keyctl=1 && pct reboot 200
```

## Optional Server Extras

The installer's optional Server extras are not preinstalled. Add them inside
the container:

```bash
pacman -S cockpit && systemctl enable --now cockpit.socket
pacman -S netdata && systemctl enable --now netdata
pacman -S fail2ban && systemctl enable --now fail2ban
```

Without `firewalld`, fail2ban needs a different ban action, such as
`nftables-multiport`, set in `/etc/fail2ban/jail.local`.

## Testing

`scripts/test-lxc` creates a disposable container from the newest template,
checks it, and destroys it:

```bash
./scripts/test-lxc                         # run on the Proxmox node itself
./scripts/test-lxc --host root@pve         # drive a node over SSH
./scripts/test-lxc --host root@pve --keep  # keep the container to inspect it
./scripts/test-lxc --dry-run               # print the commands only
```

It checks that systemd boots with no failed units, `os-release` is correct,
`eth0` gets an address and DNS works, SSH host keys and the machine ID were
created, the pacman keyring verifies a package install, Docker runs
`hello-world`, and fastfetch reports protogenOS.

Options: `--template PATH`, `--vmid ID` (default: the next free ID),
`--storage NAME` (default `local-lvm`), `--template-storage NAME` (default
`local`), `--bridge NAME` (default `vmbr0`), `--keep` and `--dry-run`. The
script refuses to use a container ID that already exists, and it only removes
the container and template it created.
