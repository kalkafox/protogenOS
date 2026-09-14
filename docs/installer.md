# Installer Architecture

The protogenOS installer is a Python package
(`installer/protogenos_installer/`) with three front ends sharing one backend:

| Front end | Entry point | Used when |
| --- | --- | --- |
| Graphical | `protogenos-install-web` | Default on the live ISO when a DRM display device exists |
| Curses TUI | `protogenos-install` | No display device, the GUI fails to start, or `protogenos.installer=tui` is on the kernel command line |
| Plain prompts | `protogenos-install --lo-fi` | Requested explicitly, or stdin/stdout is not a terminal |

The live session's `zlogin` launches the GUI on `tty1` and falls back to the
TUI if the GUI exits with an error. The ISO boot menu (systemd-boot, GRUB, and
syslinux) has a "with text installer" entry that adds
`protogenos.installer=tui`; `scripts/add-installer-boot-entries` creates it
when the profile is prepared. Closing either returns to a shell, where
both commands can be rerun.

## Components

- `profiles.py` — reads `profiles/*.packages` and `profiles/options.conf` and
  resolves a persona plus selections into an `InstallPlan` (packages, AUR
  packages, multilib requirement).
- `cli.py` / `tui.py` — text front ends and command-line flags.
- `webapi.py` — local HTTP JSON API (`127.0.0.1:8765`) that also serves the
  built React app from `installer-web/dist`.
- `backend.py` — `InstallerBackend`, which validates and runs the install.
- `storage.py` — disk layouts, LUKS, filesystems, and target mounts.
- `hardware.py` — CPU, GPU (including NVIDIA generation), hypervisor, TPM 2.0,
  fingerprint reader, and Secure Boot detection.
- `features.py` — which extras the text front ends offer, and their defaults.
- `locales.py` — locale suggestion from timezone and keyboard layout.
- `logshare.py` — log upload for bug reports.
- `bootloader.py`, `keyboard.py`, `network.py`, `mirrors.py`, `config_io.py` —
  focused helpers used by the backend and front ends.

## Graphical installer

`scripts/protogenos-install-web` loads common VM display drivers, checks for
`/dev/dri/card*`, starts the API server, and opens Firefox in kiosk mode under
the `cage` Wayland compositor. Its log is `/tmp/protogenos-install-web.log`.

Screens, in order: keyboard, network, persona, options, AUR confirmation
(only when AUR packages are selected), disk (with a partition bar showing
what is kept, erased, and used), storage, extras, users and system, review,
progress, and done or error. The error screen shows the install log and can
upload it to `paste.rs` for a bug report after asking for confirmation.

Main API routes:

| Route | Purpose |
| --- | --- |
| `GET /api/system` | Firmware, detected hardware, and optional feature support |
| `GET /api/personas`, `GET /api/options?persona=` | Personas and selectable groups |
| `POST /api/plan/resolve` | Resolve selections into a plan |
| `GET/POST /api/keyboard`, `GET /api/keyboard/layouts` | Keyboard layout |
| `GET /api/network`, `POST /api/network/scan`, `POST /api/network/connect` | Connectivity and Wi-Fi |
| `GET /api/disks`, `GET /api/disks/layout` | Target disks and their partitions |
| `GET /api/timezones`, `GET /api/mirrors/countries` | System choices |
| `GET /api/locales/suggest?timezone=&layout=` | Suggested locale |
| `POST /api/config/validate` | Validate a configuration without installing |
| `POST /api/install/start`, `GET /api/install/status` | Run and follow the install |
| `POST /api/install/log/upload` | Upload the finished install's log, returning its link |
| `POST /api/system/reboot` | Reboot when done |

`/api/install/start` refuses plans containing AUR packages unless
`aur_confirmed` is true, and refuses a second concurrent install.

For development, `scripts/dev-web-installer` runs the API with `--dry-run`
alongside the Vite dev server; `scripts/build-frontend` builds `dist/`.

## Command-line flags

```text
protogenos-install [--persona general|gamer|developer|minimal]
                   [--select GROUP=ID[,ID] ...] [--allow-aur]
                   [--non-interactive] [--output plan.json]
                   [--config saved.json --creds creds.json [--unattended]]
                   [--save-config saved.json] [--dry-run] [--lo-fi]
```

- `--non-interactive` resolves and prints the plan only; it never touches a
  disk. It fails if the plan needs AUR packages and `--allow-aur` is absent.
- `--config` installs from a configuration saved with `--save-config` (or
  `/var/log/protogenos-install.json`). Passwords come from `--creds`, a JSON
  file with `user_password`, `root_password`, and `encryption_passphrase`.
- `--unattended` skips the typed confirmation. It is destructive.
- `--dry-run` logs every command instead of running it and writes to a
  throwaway directory. It needs neither root nor a disk.

## Personas and options

Personas are General Use, Gamer, Developer, and Minimal (console only, no
Plasma). Desktop personas add `profiles/desktop.packages`: Plasma
(`plasma-meta`), Plasma Login Manager, PipeWire, and the KDE portal.

`profiles/options.conf` defines selectable groups (kernel, browser, terminal,
file manager, editor, launchers, themes, fonts), one per line:

```text
group|selection|id|label|package|source|default|profiles
```

`selection` is `one-of`, `any-of`, or `optional`; `source` is `official`,
`aur`, or `future`. AUR options default to off, so a default install needs no
AUR packages.

## Install steps

The backend reports numbered steps to every front end:

1. **Checking the installation environment** — root, required tools, target
   is an unmounted block device, `/mnt` free, the live medium excluded.
2. **Preparing disks** — partition, optionally encrypt, format, and mount.
3. **Installing packages** — rank mirrors with reflector for the chosen
   country, refresh the Arch keyring, enable ParallelDownloads (and multilib
   when needed), write `vconsole.conf`, run `pacstrap` with download progress,
   and generate `fstab`.
4. **Configuring the system** — locale, timezone, hostname, branding
   (`os-release`, `issue`, `motd`), users and sudo, keyboard for X11 and
   Plasma, desktop theming, zram, extras (snapper, Flathub, GameMode),
   enabled services, and Wi-Fi networks copied from the live session into
   NetworkManager.
5. **Installing the bootloader** — initramfs hooks (LUKS unlock prompt, no
   `kms` with the NVIDIA driver), the chosen bootloader, then Secure Boot
   signing and TPM2 enrollment when selected.
6. **Building AUR packages** — only when selected; see below.
7. **Finishing up** — take the initial snapshot, copy the install log and
   saved configuration into the target, then unmount.

Mounts are cleaned up recursively after success or failure.

## Storage

| Layout | What happens | Requirements |
| --- | --- | --- |
| Erase | Wipe the disk and create a new GPT table | — |
| Free space | Create an ESP and root in the largest unallocated region; existing partitions are untouched | UEFI, GPT, at least 17 GiB free |
| Existing partitions | Format a chosen root partition and reuse an existing ESP | UEFI, GPT, root at least 16 GiB and unmounted |

Erase layout partitions:

| Boot mode | Partitions |
| --- | --- |
| UEFI | 1 GiB FAT32 ESP (mounted at `/boot`), root |
| BIOS | 2 MiB BIOS boot, root |
| BIOS with LUKS or F2FS | 2 MiB BIOS boot, 1 GiB ext4 `/boot`, root |

Filesystems: Btrfs (default), ext4, XFS, or F2FS. Btrfs is mounted with
`compress=zstd:1,noatime` and by default gets `@`, `@home`, `@log`, `@pkg`,
and `@snapshots` subvolumes, with `rootflags=subvol=@` on the kernel command
line. Turning subvolumes off (`btrfs_subvolumes: false`) installs to the
top-level volume instead; snapshot rollback needs the subvolume layout.
Encryption is optional LUKS2 on root, opened as `cryptroot`.

## Extras

The GUI's Extras screen, a TUI checklist, and text prompts offer optional
features. Features the machine or earlier choices can't support are hidden
(TUI and text) or shown disabled with the reason (GUI). Saved configurations
use the field names below; all default to off.

| Setting | Offered when | Default | What it does |
| --- | --- | --- | --- |
| `snapshots` | Btrfs with subvolumes | on | snapper root config on `@snapshots` with hourly/daily timeline cleanup, snap-pac pre/post pacman snapshots, and an initial "protogenOS installation" snapshot. With GRUB, grub-btrfs adds a snapshot submenu; snapshots boot with `systemd.volatile=overlay` because they are read-only. |
| `flatpak` | always | on except Minimal | Flatpak with the system-wide Flathub remote. |
| `gaming_tweaks` | always | on for Gamer | GameMode (plus lib32 with multilib) with every user in the `gamemode` group, and `kernel.split_lock_mitigate = 0`. Arch already raises `vm.max_map_count`. |
| `nvidia_driver` | NVIDIA GPU, Turing (GTX 16xx/RTX 20xx) or newer | `nvidia-open` | `nvidia-open` for `linux`, `nvidia-open-dkms` plus headers for other kernels, `nvidia-utils`, `nvidia-prime` on hybrid graphics; replaces nouveau's Vulkan driver and drops the `kms` initramfs hook. Older cards keep `nouveau`. |
| `fingerprint` | libfprint-supported USB reader | on | Installs fprintd. Fingers are enrolled after installing, in System Settings → Users. |
| `tpm2_unlock` | LUKS encryption and a TPM 2.0 | off | `systemd-cryptenroll` binds a TPM2 key slot to PCR 7 and adds `rd.luks.options=<uuid>=tpm2-device=auto`. The passphrase stays as a fallback. Requires the systemd initramfs. |
| `secure_boot` | UEFI with systemd-boot or Limine | off | sbctl creates keys and signs the bootloader and kernel (re-signed on updates). In Setup Mode the keys are enrolled with Microsoft's certificates kept; otherwise the finish screen explains how to enroll later. |

Detection reads `/sys`: PCI device IDs for the NVIDIA generation,
`/sys/class/tpm` for TPM 2.0, USB IDs against systemd's
`60-autosuspend-fingerprint-reader.hwdb`, and the `SetupMode`/`SecureBoot`
EFI variables.

Limitations:

- TPM2 unlock is bound to the Secure Boot state. Turning Secure Boot on after
  enrolling makes the disk ask for its passphrase until the TPM slot is
  enrolled again; the finish screen shows the command.
- Secure Boot signs the kernel and bootloader but not the initramfs, and
  Limine's config is not enrolled, so Secure Boot does not protect them.
- GRUB is not offered with Secure Boot, and snapshot boot entries exist only
  with GRUB.

## Locale suggestion

The locale defaults to a suggestion. `zone.tab` maps the timezone to its
country, then the first supported `*.UTF-8` locale is used from: the keyboard
layout's language, the country's main language, English. Without a country
(for example `UTC`), the layout's language is used, else `en_US.UTF-8`. The
GUI keeps following the timezone until the locale is edited; the text front
ends ask for the timezone first.

## Bootloader

GRUB (UEFI or BIOS; os-prober enabled when installing alongside other
systems), systemd-boot (UEFI), or Limine (UEFI). systemd-boot and Limine do
not detect Windows on a separate ESP, so the UI recommends GRUB for dual boot.

## Users and services

The administrator account is always created. Granting sudo adds it to
`wheel`, installs `%wheel ALL=(ALL:ALL) ALL`, and locks root. Declining sudo
requires a root password instead. Any number of additional users can be
added.

Always enabled: NetworkManager, systemd-timesyncd, and `fstrim.timer`.
Desktop personas also enable `plasmalogin.service`. Bluetooth, CUPS,
power-profiles-daemon, and hypervisor guest services are enabled only when the
installed packages ship them.

Hardware detection adds CPU microcode, Mesa/Vulkan drivers, and VMware,
VirtualBox, or Hyper-V guest tools where applicable.

## AUR packages

AUR packages build after the bootloader is installed, so a failed build
leaves a bootable system and is reported as a warning. The installer builds
`yay` as the new user (falling back to building each package directly), using
a temporary passwordless sudo rule that is removed afterward.

## Safety

- Text installs require a typed confirmation naming the change:
  `ERASE /dev/…`, `INSTALL /dev/…` (free space), or `FORMAT /dev/…`
  (existing partitions).
- The GUI names the disk model, size, and every partition that will be lost,
  and unlocks its install button only after an acknowledgement is ticked.
- The live medium and mounted disks never appear as targets.
- After a text install, the installer offers a shell inside the new system
  before unmounting.

## Logs

On a failed GUI install, "Share log for a bug report" uploads the session log
(its last 512 KiB) to `paste.rs` and shows the link. Anyone with the link can
read it. Commands are logged, but passwords are passed on standard input and
never appear.

- `/var/log/protogenos-install.log` — every command run, in the live session
  and the installed system.
- `/var/log/protogenos-install.json` — the chosen configuration without
  passwords; reusable with `--config`.

## Testing

Unit tests mock every destructive command:

```bash
PYTHONPATH=installer python -m unittest discover -s tests -v
```

For end-to-end checks, install into a disposable VM (`scripts/try-iso`,
`scripts/run-iso`, `scripts/create-vm-disk`). Never develop against a physical
disk. Open work is tracked in [installer-roadmap.md](installer-roadmap.md).
