# Optional dotconfig environment

protogenOS may offer the maintainer's
[`dotconfig`](https://github.com/kalkafox/dotconfig) repository as an optional
developer environment. It must never be required for the normal desktop or
enabled without the user's choice.

## Installation experience

The installer should eventually expose an option similar to:

> Install the protogenOS developer dotfiles (terminal, shell, Git, tmux, and
> Neovim configuration)

The option defaults to **off** and describes that it changes the new user's
shell and application configuration.

## Initial safe scope

The integration should select only reviewed GNU Stow packages:

- `nvim`
- `zsh`
- `tmux`
- `git`
- `bin`
- `alacritty`
- `kitty`

Starship themes can be installed as shared data, with the furry preset offered
as the protogenOS default for users who select this environment.

The `claude`, `codex`, and `rtk` packages are intentionally excluded from the
distro option. They contain personal agent preferences and are not necessary
for a general development environment. The repository's full `install.sh` also
downloads and registers third-party tools, so the distro installer should not
execute it unattended.

## Packaging plan

For a release, protogenOS should:

1. Pin a reviewed `dotconfig` commit rather than following `main` at install
   time.
2. Package the selected configuration as `protogenos-dotconfig` after the
   repository has an explicit redistribution license.
3. Express runtime dependencies through pacman instead of downloading them in
   a post-install script.
4. Apply the configuration as the newly created user, never as root.
5. Back up conflicting user files and provide a documented removal path.

This makes the feature reproducible and auditable while leaving the original
repository free to remain a more expansive personal bootstrap environment.
