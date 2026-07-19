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

The live environment exposes the wizard as `protogenos-install`. It opens with
a branded protogenOS title, asks whether the system is for General Use, Gaming,
or Development, and resolves package alternatives into a reviewable plan. The
wizard starts automatically on the primary live console; choose `0. Exit to
shell` to close it without installing.

Interactive mode can install that plan to an unused disk. The current backend
uses a GPT layout, ext4 root filesystem, GRUB, NetworkManager, and SDDM. It
supports both UEFI and legacy BIOS boot. Installation erases the entire selected
disk and requires an exact `ERASE /dev/...` confirmation; preserving existing
partitions, disk encryption, and custom filesystem layouts are not implemented.

From a repository checkout or the booted ISO, run:

```bash
./scripts/protogenos-install
protogenos-install                       # inside the live ISO
./scripts/protogenos-install --persona gamer \
  --select kernel=linux-zen \
  --select browser=firefox,brave \
  --select gaming-launcher=steam,lutris \
  --allow-aur --non-interactive --output plan.json
```

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
