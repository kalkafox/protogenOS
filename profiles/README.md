# Installation profiles

Profiles are installer inputs, not separate ISO editions. The installer starts
with `base`, applies one persona, and then resolves the user's application
choices.

```text
base + general + selected applications
base + general + gamer + selected applications
base + general + developer + selected applications
```

## Selection rules

- A `one-of` group presents alternatives and requires exactly one choice.
- An `any-of` group allows multiple choices.
- An `optional` group can be skipped.
- Every choice has a package source. `official` packages come from Arch's
  signed repositories; `aur` packages require a separate, explicit opt-in.
- Choices marked `aur` are not silently installed. The first installer can
  offer them only after displaying the source and build implications.
- The installer shows the resolved package list before it writes to disk.

The Gamer profile requires Arch's `multilib` repository for Steam and 32-bit
graphics libraries. The installer should enable it only when the user selects
a package that needs it.

Browsers are an `any-of` group: Firefox is selected by default, but users may
install several browsers or none at all.

Terminal emulators and file managers are also `any-of` groups so users can
install several. Konsole and Dolphin are the defaults since they integrate
natively with the Plasma desktop; Kitty, Alacritty, Foot, WezTerm, Thunar,
Nautilus, and PCManFM are opt-in alternatives.

Kernels are a `one-of` group. The standard Arch `linux` package is the default,
and the official `linux-zen` package is available for users who prefer its
desktop-oriented tuning. protogenOS does not compile or distribute a kernel.

The package names here are inputs to the future installer. They should be
validated against the current repositories during each build rather than being
assumed to remain available forever.
