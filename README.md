# protogenOS

protogenOS is a furry-themed, Arch-based Linux distribution with a visual
identity inspired by protogens and the wider furry community.

The project is currently in its bootstrap phase. The first milestone is a
reproducible, branded live ISO built with Archiso. It will remain compatible
with Arch's repositories while protogenOS-specific branding and defaults are
delivered as a small set of separate packages.

## Initial goals

- Boot on UEFI systems in a virtual machine.
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
packages/   Future PKGBUILDs for protogenOS packages
kernels/    Pinned linux-tkg flavors and kernel build outputs
scripts/    Profile preparation and build helpers
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
belong in `overlays/`, packages, or preparation scripts.

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

`.github/workflows/build-iso.yml` builds through the same container on the
project's self-hosted runner when run manually or when a `v*` tag is pushed. It
uploads the ISO and `SHA256SUMS` as a GitHub Actions artifact retained for 14
days. Normal branch pushes do not start the comparatively expensive ISO build.
Both ISO and kernel workflows require the `protogenos-build` runner label; see
`docs/ci.md` before registering the build machine.

## Installer prototype

The Python wizard resolves personas and optional package groups into a reviewed
installation plan. It does not partition disks yet:

```bash
./scripts/protogenos-install
./scripts/protogenos-install --persona gamer \
  --select browser=firefox,brave \
  --select gaming-launcher=steam,lutris \
  --allow-aur --non-interactive --output plan.json
```

Run its tests with:

```bash
PYTHONPATH=installer python -m unittest discover -s tests -v
```

## Custom kernels

protogenOS defines `generic`, `performance`, `developer`, `zen`, and
experimental `minimal-vm` linux-tkg flavors. The stock Arch `linux` package is
the installer default and live-ISO rescue kernel until the custom repository is
available. Build one flavor in Docker with:

```bash
./scripts/build-kernel performance
./scripts/build-kernel --list
./scripts/build-kernel --check
./scripts/build-kernel --preflight performance
```

Packages and checksums are written to `kernels/out/<flavor>/`. Builds use the
linux-tkg revision and kernel version pinned in `kernels/linux-tkg.lock`; update
both deliberately and boot-test every flavor before publishing. A full build
needs substantial disk, memory, and time. See `kernels/README.md` for the
support policy and persona recommendations.

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
ISO's systemd-boot entry:

```bash
./scripts/run-kernel
./scripts/run-kernel --headless --append "systemd.log_level=debug"
```

Both runners accept `--iso PATH`, `--memory MiB`, `--cpus COUNT`, and
`--dry-run`. QEMU, `qemu-img`, OVMF (`edk2-ovmf`), and `bsdtar` (`libarchive`)
must be installed on the host. KVM is used automatically when available;
otherwise the scripts fall back to software emulation.

## Project status

The desktop environment, installer, visual language, and initial package set
are being defined. The current direction is KDE Plasma with a black-and-red
visual system. See `docs/theme.md` and `docs/dotfiles.md` for the initial design
and optional developer-environment policy.

Arch Linux is a trademark of its respective owner. protogenOS is an independent
furry-themed distribution built using Arch Linux technology and is not endorsed
by or affiliated with the Arch Linux project.
