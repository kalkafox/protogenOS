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

### `scripts/container-build`

Internal Docker/CI entry point used by `docker-build` and the ISO workflow. It
creates an isolated directory under `/tmp`, invokes `prepare-profile` and
`build-iso`, copies artifacts into `out/`, and generates `SHA256SUMS`.
`HOST_UID` and `HOST_GID` restore local artifact ownership when supplied.

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

Options include `--persona general|gamer|developer`, repeatable
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
