# Visual direction

protogenOS uses a dark, high-contrast base with red energy accents. The theme
should suggest illuminated protogen visors and synthetic materials without
turning every surface bright red.

## Foundation palette

| Role | Color | Intended use |
|---|---|---|
| Void | `#09090B` | Desktop and deepest backgrounds |
| Carbon | `#141216` | Windows, panels, and cards |
| Raised | `#211B20` | Hovered and elevated surfaces |
| Signal red | `#D51F3D` | Primary controls and selections |
| Visor red | `#FF405C` | Focus, glow, and small highlights |
| Deep red | `#721426` | Borders and subdued red surfaces |
| Snow | `#F5F1F2` | Primary text |
| Alloy | `#BEB3B7` | Secondary text and inactive icons |

Red is an accent, not the default text color. Destructive actions must remain
visually distinguishable from ordinary red-accented controls. Every final KDE
color role should be checked for readable contrast in normal, hover, disabled,
and selected states.

## Artwork direction

- Favor dark negative space with red visor light and restrained glow.
- Represent different species and fursonas, even when protogens lead the brand.
- Avoid using community art without explicit redistribution permission.
- Record title, artist, source, license, and any modification for every asset.
- Include a calmer wallpaper option for users sensitive to bright imagery.

The machine-readable starting colors live in `config/theme.conf`.

## Terminal logo

fastfetch and neofetch show a pair of protogen ears in braille characters,
in Snow, instead of Arch's logo; color `1` (Visor red) is reserved for future
visor details. The logo and configs live in
`installer/protogenos_installer/assets/fetch/`: `fastfetch-logo.txt` uses
fastfetch's `$1`/`$2` color markers and `neofetch-logo.txt` uses neofetch's
`${c1}`/`${c2}`, so edit both together. Installs and the live ISO get the
system-wide `/etc/xdg/fastfetch/config.jsonc`. neofetch only reads a per-user
config, so new accounts get `~/.config/neofetch/config.conf` from `/etc/skel`.
fastfetch is installed on every persona except Minimal; neofetch is AUR-only and
unmaintained, so it is not installed, but its config is ready if a user adds it.
