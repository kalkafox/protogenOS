# protogenOS

protogenOS is a furry-themed, Arch-based Linux distribution with a visual
identity inspired by protogens and the wider furry community.

The project is currently in its bootstrap phase. The first milestone is a
reproducible, branded live ISO built with Archiso. It will remain compatible
with Arch's repositories while protogenOS-specific branding and defaults are
delivered as a small set of separate packages.

## Initial goals

- Boot on BIOS and UEFI systems in a virtual machine.
- Provide a polished furry-themed live desktop.
- Install a usable Arch-based system with sensible defaults.
- Offer the maintainer's `dotconfig` developer environment during installation.
- Keep protogenOS customization separate from the upstream Archiso profile.
- Make every release reproducible from this repository.

## Repository layout

```text
docs/       Project direction and design decisions
config/     Release inputs shared by build and installer tooling
profiles/   Persona package sets and selectable application groups
overlays/   Files copied into the Archiso live filesystem
installer/  Python installation wizard and guarded disk backend
tests/      Installer and profile resolution tests
packages/   Future PKGBUILDs for protogenOS packages
scripts/    Profile preparation and build helpers
.github/    Hosted ISO build workflow
profile/    Generated Archiso profile (not committed)
out/        Generated ISO images (not committed)
```

## Preparing a build profile

On an Arch Linux build system, install `archiso`, then run:

```bash
sudo pacman -S archiso
./scripts/prepare-profile
```

This copies Archiso's current `releng` profile into `profile/` and applies the
protogenOS overlay. The generated profile is disposable; project-owned changes
belong in `overlays/`, packages, or preparation scripts. Preparation also
brands the firmware boot menus and installs the `protogenos-install` wizard in
the live environment.

Build the ISO with:

```bash
sudo ./scripts/build-iso
```

The finished image will be written to `out/`.

### Docker build

Docker can provide the Arch build environment without installing Archiso on
the host:

```bash
./scripts/docker-build
```

The container requires `--privileged` because Archiso creates mounts while
building its filesystem image. Only run the project-owned builder from trusted
source. Set `PROTOGENOS_ARCH_IMAGE` to a dated official Arch image tag when a
release needs a stable builder input; the default `archlinux:base` follows the
rolling Arch image.

### GitHub Actions

`.github/workflows/build-iso.yml` builds through the same container on GitHub's
standard Ubuntu runner when run manually or when a `v*` tag is pushed. It
uploads the ISO and `SHA256SUMS` as a GitHub Actions artifact retained for 14
days. Normal branch pushes do not start the comparatively expensive ISO build.

## Installer

The live environment starts the graphical installer (`protogenos-install-web`,
a kiosk browser under cage) automatically on the primary console, and falls
back to the text wizard (`protogenos-install`) on machines without a display
device. Boot with `protogenos.installer=tui` to force the text wizard. Both
open with a branded protogenOS title, ask whether the system is for General
Use, Gaming, or Development, and resolve package alternatives into a
reviewable plan. The graphical installer logs to
`/tmp/protogenos-install-web.log`.

Both front ends start with the keyboard layout (applied immediately, and
used for the installed console, desktop, and disk-unlock prompt), then make
sure the live session is online, offering a Wi-Fi picker (iwd) when it is not;
Wi-Fi networks joined live are copied into the installed system's
NetworkManager.

Installation options:

- **Disk layout:** erase the whole disk, install alongside other systems in
  the largest unallocated space (a new EFI partition is created; existing
  partitions are untouched), or format an existing root partition and reuse
  an EFI system partition. The last two require UEFI and GPT.
- **Filesystems:** Btrfs (default; zstd compression and `@`, `@home`, `@log`,
  `@pkg`, and `@snapshots` subvolumes, or a flat volume without subvolumes),
  ext4, XFS, or F2FS.
- **Encryption:** optional LUKS2 root with an initramfs unlock prompt. On BIOS
  systems an unencrypted ext4 `/boot` partition is added automatically.
- **Bootloader:** GRUB (UEFI or BIOS, with os-prober when installing alongside
  other systems), systemd-boot, or Limine (UEFI only). UEFI installs mount the
  EFI system partition at `/boot`.
- **Swap:** zram (default) or none.
- **System:** hostname, locale, timezone, mirror country (ranked with
  reflector), optional kernel headers, the administrator account, and any
  number of additional users. pacman's ParallelDownloads is enabled.
- **Hardware:** CPU microcode, Mesa/Vulkan drivers, and hypervisor guest tools
  are added for the detected hardware; time sync, TRIM, and Bluetooth are
  enabled when present.

The Arch keyring is refreshed before pacstrap. AUR packages are built with
`yay` after the bootloader is installed, so a failed AUR build is reported as
a warning instead of leaving an unbootable system. The command log is kept at
`/var/log/protogenos-install.log`, and the chosen configuration (without
passwords) at `/var/log/protogenos-install.json`, in both the live session
and the installed system.

Text-mode installs require typing a confirmation that names what will be
destroyed: `ERASE /dev/...`, `INSTALL /dev/...` (free space), or
`FORMAT /dev/...` (existing partitions). The graphical installer instead names
the disk model, size, and every partition that will be lost, and requires
ticking an acknowledgement before its install button unlocks. After a
text-mode install you can
open a shell inside the new system before it is unmounted.

From a repository checkout or the booted ISO, run:

```bash
./scripts/protogenos-install
protogenos-install                       # inside the live ISO
./scripts/protogenos-install --persona gamer \
  --select kernel=linux-zen \
  --select browser=firefox,brave \
  --select gaming-launcher=steam,lutris \
  --allow-aur --non-interactive --output plan.json

# Save choices for reuse, then install from them later (passwords live in a
# separate credentials file; --unattended skips the typed confirmation).
protogenos-install --save-config my-install.json
protogenos-install --config my-install.json --creds creds.json
protogenos-install --config my-install.json --creds creds.json --unattended
```

A credentials file looks like
`{"version": 1, "user_password": "...", "root_password": null,
"encryption_passphrase": "...", "additional_users": {"kit": "..."}}`.

The text banner lives in
`installer/protogenos_installer/branding.py`. `INSTALLER_BANNER` is the intended
extension point for the future multiline ASCII-art wordmark; menu code should
not duplicate branding strings elsewhere.

Run its tests with:

```bash
PYTHONPATH=installer python -m unittest discover -s tests -v
```

## Kernel choices

The installer offers Arch's official `linux` and `linux-zen` packages. `linux`
is the default for broad compatibility; `linux-zen` is an optional
desktop-oriented alternative. protogenOS does not compile or distribute custom
kernel binaries, keeping releases fast to build and aligned with Arch updates.

## Development automation

Install the native Arch development dependencies:

```bash
sudo pacman -S qemu-desktop edk2-ovmf libarchive
```

Run the fast development checks with:

```bash
./scripts/dev-check
```

After building an ISO, boot the newest image with QEMU and UEFI:

```bash
./scripts/run-iso
./scripts/create-vm-disk                 # creates a 40G qcow2 disk in out/
./scripts/run-iso --disk out/protogenos-dev.qcow2
```

Use `--bios` to exercise legacy boot or `--headless` for a serial-only VM.
Direct kernel/initramfs boot reads the kernel paths and Archiso options from the
ISO's systemd-boot entry (also takes `--bios`/`--uefi`):

```bash
./scripts/run-kernel
./scripts/run-kernel --uefi
./scripts/run-kernel --headless --append "systemd.log_level=debug"
```

After installing to a qcow2 disk, boot it directly with no ISO attached:

```bash
./scripts/run-disk --disk out/protogenos-dev.qcow2
```

Both ISO/kernel runners accept `--iso PATH`, `--memory MiB`, `--cpus COUNT`,
and `--dry-run`; `run-disk` takes the same plus a required `--disk PATH`. QEMU,
`qemu-img`, OVMF (`edk2-ovmf`), and `bsdtar` (`libarchive`) must be installed
on the host. KVM is used automatically when available; otherwise the scripts
fall back to software emulation. See [`docs/scripts.md`](docs/scripts.md) for
the complete build, installer, and QEMU script reference.

## Project status

KDE Plasma is the first desktop target, with a black-and-red visual system
inspired by protogen visors and synthetic materials. The ISO, boot menus,
live-session identity, and installer carry protogenOS branding. The installer
now performs guarded whole-disk installations using standard Arch tools. See
`docs/installer.md` for its behavior and current limitations, and
`docs/theme.md` and `docs/dotfiles.md` for the design language and optional
developer-environment policy.

Arch Linux is a trademark of its respective owner. protogenOS is an independent
furry-themed distribution built using Arch Linux technology and is not endorsed
by or affiliated with the Arch Linux project.
