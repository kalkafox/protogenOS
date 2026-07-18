# Repository Guidelines

## Project Structure & Module Organization

protogenOS is a furry-themed, Arch-based distribution built with Archiso.
Project-owned filesystem content lives in `overlays/airootfs/`; do not edit a
generated Archiso tree. Package personas and selectable alternatives live in
`profiles/`, while future PKGBUILDs belong in `packages/`. Shared release inputs
are kept in `config/`, design decisions in `docs/`, build helpers in `scripts/`,
and the container definition in `docker/`. GitHub Actions workflows are under
`.github/workflows/`. Python installer code is in
`installer/protogenos_installer/`, with tests under `tests/`.

`profile/`, `work/`, and `out/` are generated and ignored. Never commit ISO
images, package archives, or build logs.

## Build, Test, and Development Commands

- `./scripts/docker-build` builds the Archiso builder and produces an ISO plus
  `SHA256SUMS` in `out/`. Docker runs privileged because Archiso creates mounts.
- `./scripts/prepare-profile` copies Archiso's current `releng` profile and
  applies protogenOS overlays. Run it on Arch after installing `archiso`.
- `sudo ./scripts/build-iso` builds from the prepared local profile.
- `bash -n scripts/*` performs shell syntax checks.
- `git diff --check` detects whitespace errors before submission.
- `PYTHONPATH=installer python -m unittest discover -s tests -v` runs installer
  unit tests.
- `./scripts/dev-check` runs the complete fast validation set.
- `./scripts/run-iso --dry-run --bios` previews the QEMU ISO command;
  `./scripts/run-kernel --dry-run` validates direct kernel/initramfs extraction.

Only run the privileged container from trusted source. Test finished images in
a disposable QEMU virtual machine before using physical disks.

## Coding Style & Naming Conventions

Shell scripts use Bash, four-space indentation, `set -euo pipefail`, quoted
expansions, and descriptive `snake_case` variables. Keep scripts executable and
compatible with ShellCheck. Python uses four spaces, type hints, dataclasses,
and standard-library dependencies where practical. Package manifests contain
one Arch package per line.
In `profiles/options.conf`, preserve the documented pipe-delimited field order.
Use lowercase kebab-case for package names such as `protogenos-branding`.

## Testing Guidelines

At minimum, run Python unit tests, shell syntax checks, `git diff --check`, and
a Docker ISO build for build-system changes. Installer tests cover profile
resolution, invalid choices, and generated plans before disk operations exist.

## Commit & Pull Request Guidelines

The repository has no existing commit history. Use concise, imperative subjects;
Conventional Commit prefixes are encouraged, for example
`feat(installer): add browser selection`. Keep unrelated changes separate.
Pull requests should explain user-visible behavior, list validation performed,
link relevant issues, and include screenshots for installer or theme changes.
Call out new repositories, AUR packages, privileged operations, or destructive
installation behavior explicitly.
