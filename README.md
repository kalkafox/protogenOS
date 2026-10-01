<h1 align="center">protogenOS</h1>

<p align="center"><strong>A furry-themed, Arch-based Linux distribution</strong></p>

<p align="center">
  <a href="LICENSE.md"><img alt="License: GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-D51F3D?style=flat-square&labelColor=141216"></a>
  <img alt="Based on Arch Linux" src="https://img.shields.io/badge/based%20on-Arch%20Linux-D51F3D?style=flat-square&labelColor=141216">
  <img alt="Desktop: KDE Plasma" src="https://img.shields.io/badge/desktop-KDE%20Plasma-D51F3D?style=flat-square&labelColor=141216">
  <img alt="Status: pre-release" src="https://img.shields.io/badge/status-pre--release-721426?style=flat-square&labelColor=141216">
</p>

<p align="center">
  <a href="#try-it">Try it</a> ·
  <a href="#the-installer">Installer</a> ·
  <a href="#building-the-iso">Build</a> ·
  <a href="#development">Develop</a> ·
  <a href="#documentation">Docs</a>
</p>

---

protogenOS is an Arch-based Linux distribution with a black-and-red look
inspired by protogen visors, made by and for the furry community. It keeps
Arch's official repositories and rolling updates, adds a KDE Plasma desktop,
and has its own installer, so you don't need to know Arch to set it up.

> [!WARNING]
> protogenOS is **pre-release software**. The installer partitions and formats
> disks. Try it in a virtual machine first, and back up anything important
> before installing on real hardware.

## Highlights

- **Live desktop.** The ISO boots into a Plasma session with the installer
  open in a window, so you can browse and test hardware first. Machines that
  can't run it get the installer full screen, or a curses text installer
  with the same options.
- **Fast, careful installs.** Packages download in the background, from the
  fastest mirrors, while you're still choosing. Pre-flight checks flag a
  missing disk, battery power, legacy BIOS mode and more before you start.
- **Personas.** Pick General Use, Gamer, Developer, Server or Minimal, then swap
  individual apps: browser, terminal, file manager, editor, kernel and more.
- **Storage options.** Erase a disk, install into free space or shrink
  Windows (NTFS) or an ext4 partition to make room, or reuse existing
  partitions. Btrfs, ext4, XFS or F2FS, with optional LUKS2 encryption.
- **Hardware-aware extras.** Btrfs snapshots, Flatpak, gaming tweaks, the
  NVIDIA open driver, fingerprint login, TPM2 disk unlock and Secure Boot.
  Each is offered only when your hardware and earlier choices support it.
- **Plain Arch underneath.** Official Arch packages and kernels (`linux`,
  `linux-zen` or `linux-lts`). protogenOS builds no custom kernels or forks, so updates arrive
  as soon as Arch ships them.
- **Reproducible builds.** Every ISO is built from this repository with
  Archiso, locally or in GitHub Actions.

## Try it

There are no published releases yet, so build the ISO first (see
[Building the ISO](#building-the-iso)). Then boot it in QEMU:

```bash
./scripts/create-vm-disk                          # 40G qcow2 disk in out/
./scripts/run-iso --disk out/protogenos-dev.qcow2 # UEFI; add --bios for legacy
./scripts/run-disk --disk out/protogenos-dev.qcow2  # boot the installed system
```

[`scripts/try-iso`](scripts/try-iso) does the same job as a single file you
can copy anywhere, with no repository checkout needed:

```bash
./try-iso protogenos-*.iso        # install into a fresh disk image
./try-iso --boot-disk             # boot the result
```

These need `qemu-desktop`, `edk2-ovmf` and `libarchive` on the host. KVM is
used when available.

## The installer

The live ISO starts a Plasma desktop with the graphical installer open. With
too little memory or no working graphics it shows the installer alone, and
without a display device the text installer. The boot menu has "with
installer only" and "with text installer" entries to choose directly.

| Persona | What you get |
| --- | --- |
| **General Use** | Plasma desktop, PipeWire, Discover, Firefox and everyday KDE apps |
| **Gamer** | General Use plus Steam, Lutris, Heroic or Faugus, GameMode, MangoHud, Gamescope, Wine, NTSync and game-performance; optional Proton-GE and Proton-CachyOS |
| **Developer** | General Use plus `base-devel`, Git, Podman, debugging tools and your choice of editor |
| **Server** | Headless system with key-only SSH, firewalld, the LTS kernel and Podman or Docker; optional Cockpit, Netdata and fail2ban |
| **Minimal** | Console-only system with no desktop and the fewest packages |

The installer walks through keyboard, pre-flight checks, network (with a Wi-Fi picker), persona
and apps, disk and storage, extras, users and system settings, then a review
screen. Before touching a disk, it names exactly what will be lost: the GUI
lists every affected partition and needs a ticked acknowledgement, and the
text installer needs a typed confirmation such as `ERASE /dev/sda`.

AUR packages are always opt-in and are built after the bootloader is
installed, so a failed AUR build can't leave the system unbootable. Logs and
the chosen configuration (without passwords) are saved to `/var/log/` on the
installed system. Saved configurations can be replayed for unattended
installs:

```bash
protogenos-install --save-config my-install.json
protogenos-install --config my-install.json --creds creds.json --unattended
```

See [`docs/installer.md`](docs/installer.md) for every option, the storage
layouts and the safety rules.

## Building the ISO

### With Docker (recommended)

Works on any Linux host with Docker, including WSL2:

```bash
./scripts/docker-build
```

The ISO and `SHA256SUMS` are written to `out/`. Downloaded packages are cached
in `~/.cache/protogenos/pacman-pkg`, so later builds are faster. For quick
local testing, skip the slow xz compression:

```bash
PROTOGENOS_FAST_BUILD=1 ./scripts/docker-build
```

> [!NOTE]
> The build container runs with `--privileged` because Archiso creates mounts.
> Only build from source you trust.

### Proxmox LXC template

```bash
./scripts/build-lxc     # out/protogenos-server_YYYYMMDD-1_amd64.tar.zst
./scripts/test-lxc --host root@pve
```

A headless server container with Docker ready to run. See
[`docs/lxc.md`](docs/lxc.md).

### On Arch Linux

```bash
sudo pacman -S archiso
./scripts/prepare-profile     # generates profile/ from Archiso's releng
sudo ./scripts/build-iso
```

### In GitHub Actions

The [Build protogenOS ISO](.github/workflows/build-iso.yml) workflow runs when
started manually or when a `v*` tag is pushed. It uploads the ISO and checksums
as a workflow artifact kept for 14 days.

## Development

```bash
./scripts/dev-check                                     # tests, shell syntax, whitespace
PYTHONPATH=installer python -m unittest discover -s tests -v
./scripts/dev-web-installer                             # GUI installer in dry-run mode
./scripts/run-kernel --headless                         # boot the ISO's kernel directly
```

`dev-web-installer` runs the installer API with `--dry-run` next to the Vite
dev server. It needs no root, no ISO and no real disk, and it logs commands
instead of running them. The frontend uses [Bun](https://bun.sh).

### Repository layout

```text
installer/      Python installer: backend, text front ends, web API
installer-web/  React + TypeScript graphical installer
profiles/       Persona package sets and selectable app options
overlays/       Files copied into the live ISO and LXC template filesystems
config/         Shared release inputs, such as the theme palette
scripts/        Build, QEMU and installer helper scripts
docker/         Archiso builder image
tests/          Installer unit tests
packages/       PKGBUILDs for protogenOS packages (planned)
docs/           Design decisions and reference documentation
```

`profile/`, `work/` and `out/` are generated and never committed.

## Documentation

| Document | Covers |
| --- | --- |
| [Installer architecture](docs/installer.md) | Front ends, API, install steps, storage, extras, safety |
| [Installer roadmap](docs/installer-roadmap.md) | What is done, what is open, known gaps |
| [Proxmox LXC template](docs/lxc.md) | Building, using and testing the container template |
| [Script reference](docs/scripts.md) | Every build, QEMU and installer script |
| [Vision](docs/vision.md) | Identity, product principles, open decisions |
| [Visual direction](docs/theme.md) | Color palette and artwork policy |
| [Developer dotfiles](docs/dotfiles.md) | The optional `dotconfig` environment |

## Contributing

Run `./scripts/dev-check` before opening a pull request, and do a Docker ISO
build for build-system changes. Use short, imperative commit subjects with
Conventional Commit prefixes, for example `feat(installer): add browser
selection`. Pull requests should describe user-visible behavior and the
testing done, include screenshots for installer or theme changes, and call out
new repositories, AUR packages, privileged operations or destructive
installation behavior. [`AGENTS.md`](AGENTS.md) has the full guidelines.

Artwork is shipped only with the artist's explicit permission and a documented
redistribution license.

## License

protogenOS is licensed under the [GNU General Public License v3.0](LICENSE.md).

Arch Linux is a trademark of its respective owner. protogenOS is an
independent distribution built using Arch Linux technology and is not endorsed
by or affiliated with the Arch Linux project.
