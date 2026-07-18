# Project vision

## Identity

protogenOS should feel welcoming, expressive, and unmistakably furry without
making the theme an obstacle to everyday use. Protogens are the central visual
motif, while the project should represent and welcome the broader furry
community.

## Product principles

1. **Fandom-forward** — original furry artwork, thoughtful theming, and an
   inclusive community are core features rather than an afterthought.
2. **Arch-compatible** — use official Arch packages and infrastructure wherever
   practical; maintain custom packages only where they add protogenOS identity
   or functionality.
3. **Approachable** — installation and common desktop tasks should not require
   prior Arch expertise.
4. **Respectful of artists** — ship artwork only with explicit permission,
   attribution, and a documented redistribution license.
5. **Maintainable** — prefer configuration and small packages over forks of
   kernels, desktops, or system components.

## First-release boundary

Version 0.1 needs one desktop, one installer path, one architecture (`x86_64`),
and a small curated application set. Additional desktop editions, extensive
repositories, and custom system components can wait until the base release is
reliable.

## Decisions still to make

- Display manager and exact KDE Plasma package set
- Installer experience
- Default applications
- Typography, logo, mascot, and wallpaper policy
- Community links and code of conduct
- Update/release cadence

## Decisions made

- KDE Plasma is the initial desktop environment.
- The core visual direction is black and red.
- The maintainer's `dotconfig` setup will be available as an opt-in developer
  environment, not enabled for every user.
- Applications are selected through alternatives and optional groups rather
  than being hard-coded into each persona.
