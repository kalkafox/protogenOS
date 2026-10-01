# Development Script Reference

Run project scripts from the repository root. They use paths relative to the
checkout and stop on errors. Generated profiles, work trees, virtual disks, and
ISO files are ignored by Git.

## ISO Build Scripts

### `scripts/prepare-profile`

Copies Archiso's installed `releng` profile into `profile/`, applies the
protogenOS filesystem overlay, brands BIOS and UEFI boot entries, and embeds the
installer. It also adds its live dependencies and a tty1 login hook that opens
the installer automatically while preserving shell access.

```bash
./scripts/prepare-profile
PROTOGENOS_PROFILE_DIR=/tmp/protogenos-profile ./scripts/prepare-profile
```

Requires Arch Linux with `archiso` installed. It refuses to modify an existing
target directory; move or remove a generated profile before preparing it again.

### `scripts/build-iso`

Builds a prepared profile with `mkarchiso`:

```bash
sudo ./scripts/build-iso
```

The script must run as root. These environment variables override its paths:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROTOGENOS_PROFILE_DIR` | `profile/` | Prepared Archiso profile |
| `PROTOGENOS_WORK_DIR` | `work/` | Temporary Archiso work tree |
| `PROTOGENOS_OUTPUT_DIR` | `out/` | Completed ISO destination |

### `scripts/docker-build`

Provides the recommended host-independent build path:

```bash
./scripts/docker-build
PROTOGENOS_ARCH_IMAGE=archlinux:base ./scripts/docker-build
```

It builds `docker/Dockerfile`, then runs the image with `--privileged` because
Archiso creates mounts. `PROTOGENOS_BUILDER_IMAGE` changes the local image name,
and `PROTOGENOS_ARCH_IMAGE` changes its Arch base image. Only run the privileged
container from trusted source. Successful builds replace prior ISO files and
`SHA256SUMS` in `out/`.

Downloaded packages persist in `~/.cache/protogenos/pacman-pkg`, which is
bind-mounted over the container's pacman cache, so later builds only fetch
packages that changed. `PROTOGENOS_PACMAN_CACHE` moves that directory; delete
it to reclaim space.

For quicker local iteration, `PROTOGENOS_FAST_BUILD=1` compresses the live root
filesystem with zstd instead of xz. The build is much faster and the ISO is
somewhat larger. Keep the default xz compression for release images.

```bash
PROTOGENOS_FAST_BUILD=1 ./scripts/docker-build
```

### `scripts/container-build`

Internal Docker/CI entry point used by `docker-build` and the ISO workflow. It
creates an isolated directory under `/tmp`, invokes `prepare-profile` and
`build-iso`, copies artifacts into `out/`, and generates `SHA256SUMS`.
`HOST_UID` and `HOST_GID` restore local artifact ownership when supplied.

## LXC Template Scripts

### `scripts/build-lxc`

Builds the Proxmox LXC server template in the Docker builder image:

```bash
./scripts/build-lxc
```

It uses the same image, `PROTOGENOS_BUILDER_IMAGE`, `PROTOGENOS_ARCH_IMAGE`
and package cache as `docker-build`, and it also runs `--privileged` because
`pacstrap` creates mounts. The template and its `.sha256` file replace older
templates in `out/`. See [`docs/lxc.md`](lxc.md).

### `scripts/container-build-lxc`

Internal Docker entry point used by `build-lxc`. It installs
[`config/lxc.packages`](../config/lxc.packages) into a new root with
`pacstrap`, applies `overlays/lxc/` and the branding, enables services, removes
per-machine state, checks the result and packs it with zstd.
`PROTOGENOS_OUTPUT_DIR` changes the destination.

### `scripts/test-lxc`

Creates a disposable unprivileged container from the template on a Proxmox
VE node, runs smoke checks, then destroys it:

```bash
./scripts/test-lxc
./scripts/test-lxc --host root@pve --keep
./scripts/test-lxc --dry-run
```

Supported options are `--host USER@HOST`, `--template PATH`, `--vmid ID`,
`--storage NAME`, `--template-storage NAME`, `--bridge NAME`, `--keep`, and
`--dry-run`.

## Installer and Validation

### `scripts/protogenos-install`

Starts the Python installation wizard. With no options it is interactive:

```bash
./scripts/protogenos-install
./scripts/protogenos-install --persona developer \
  --select kernel=linux-zen \
  --select browser=firefox,librewolf \
  --allow-aur --non-interactive --output plan.json
```

Options include `--persona general|gamer|developer|minimal`, repeatable
`--select GROUP=CHOICE[,CHOICE]`, `--allow-aur`, `--non-interactive`,
`--profiles-dir PATH`, and `--output PATH`. Non-interactive mode only creates a
plan and never modifies disks. Interactive mode can continue into a guarded,
whole-disk installation after showing the resolved package list and requiring
an exact erase confirmation. On the live ISO it starts automatically on tty1.
Choose `0. Exit to shell` to close it, and run `protogenos-install` to reopen it
later. See [`docs/installer.md`](installer.md) for the disk layout and limits.

### `scripts/dev-check`

Runs the fast pre-commit checks:

```bash
./scripts/dev-check
```

This executes Python unit tests, parses every Bash script with `bash -n`, checks
the live-login fragment with `zsh -n`, and checks the Git diff for whitespace
errors.

## QEMU Development Scripts

Install QEMU, OVMF, and libarchive on Arch before using the VM helpers:

```bash
sudo pacman -S qemu-desktop edk2-ovmf libarchive
```

### `scripts/create-vm-disk`

Creates a qcow2 installation target without overwriting an existing file:

```bash
./scripts/create-vm-disk
./scripts/create-vm-disk out/test.qcow2 64G
```

The defaults are `out/protogenos-dev.qcow2` and `40G`.

### `scripts/run-iso`

Boots the newest ISO in `out/`; UEFI is the default:

```bash
./scripts/run-iso
./scripts/run-iso --disk out/protogenos-dev.qcow2
./scripts/run-iso --bios --dry-run
```

Supported options are `--iso PATH`, `--disk PATH`, `--bios`, `--uefi`,
`--memory MiB`, `--cpus COUNT`, `--headless`, and `--dry-run`. Set
`PROTOGENOS_OVMF_CODE` and `PROTOGENOS_OVMF_VARS` for nonstandard firmware
locations, or `QEMU_SYSTEM_X86_64` to override the QEMU executable.

### `scripts/run-disk`

Boots an already-installed protogenOS qcow2 disk directly, with no ISO
attached. UEFI is the default:

```bash
./scripts/run-disk --disk out/protogenos-dev.qcow2
./scripts/run-disk --disk out/protogenos-dev.qcow2 --bios --dry-run
```

`--disk PATH` is required (there is no default, unlike the ISO runners).
Supported options are `--disk PATH`, `--bios`, `--uefi`, `--memory MiB`,
`--cpus COUNT`, `--headless`, and `--dry-run`.

### `scripts/run-kernel`

Extracts the kernel, initramfs, and command line from the ISO's systemd-boot
entry and boots them directly while leaving the ISO attached for its live root:

```bash
./scripts/run-kernel --dry-run
./scripts/run-kernel --headless --append "systemd.log_level=debug"
./scripts/run-kernel --uefi
```

It accepts `--iso`, `--disk`, `--bios`, `--uefi`, `--memory`, `--cpus`,
`--append`, `--headless`, and `--dry-run`. `--bios` (the default) boots without
firmware drives; `--uefi` adds OVMF pflash drives so the direct-booted kernel
runs under the same firmware as a UEFI install. It requires `bsdtar` from
`libarchive`.

### `scripts/qemu-lib`

Internal shared library sourced by both QEMU runners. It locates the newest ISO
and OVMF firmware, selects KVM when available, builds common QEMU arguments, and
implements dry-run command printing. Do not execute it directly.
