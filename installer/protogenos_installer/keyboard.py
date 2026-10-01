"""Keyboard layouts: XKB catalog, console keymap mapping, and config files."""

from __future__ import annotations

import pwd
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

XKB_RULES = Path("/usr/share/X11/xkb/rules/base.lst")
KBD_MODEL_MAP = Path("/usr/share/systemd/kbd-model-map")
KBD_KEYMAPS = Path("/usr/share/kbd/keymaps")
LAYOUT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
VARIANT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{0,32}$")


@dataclass(frozen=True, slots=True)
class KeyboardVariant:
    code: str
    description: str


@dataclass(frozen=True, slots=True)
class KeyboardLayout:
    code: str
    description: str
    variants: tuple[KeyboardVariant, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "description": self.description,
            "variants": [
                {"code": variant.code, "description": variant.description}
                for variant in self.variants
            ],
        }


def list_layouts(rules: Path = XKB_RULES) -> tuple[KeyboardLayout, ...]:
    """Parse xkeyboard-config's base.lst into layouts with their variants."""
    try:
        lines = rules.read_text().splitlines()
    except OSError:
        return (KeyboardLayout("us", "English (US)"),)
    section = ""
    descriptions: dict[str, str] = {}
    variants: dict[str, list[KeyboardVariant]] = {}
    for line in lines:
        if line.startswith("! "):
            section = line[2:].strip()
            continue
        stripped = line.strip()
        if not stripped:
            continue
        code, _, rest = stripped.partition(" ")
        rest = rest.strip()
        if section == "layout":
            descriptions[code] = rest
        elif section == "variant":
            layout, _, description = rest.partition(": ")
            variants.setdefault(layout, []).append(KeyboardVariant(code, description))
    layouts = [
        KeyboardLayout(code, description, tuple(variants.get(code, ())))
        for code, description in descriptions.items()
        if code != "custom"  # placeholder for a user-supplied symbols file
    ]
    return tuple(sorted(layouts, key=lambda layout: layout.description.lower()))


def console_keymap(
    layout: str,
    variant: str = "",
    *,
    model_map: Path = KBD_MODEL_MAP,
    keymaps_root: Path = KBD_KEYMAPS,
) -> str:
    """Pick the Linux console keymap matching an XKB layout/variant.

    Uses systemd's kbd-model-map (what localectl uses for conversion), then a
    console keymap with the same name, and finally US.
    """
    wanted_variant = variant or "-"
    layout_only: str | None = None
    try:
        lines = model_map.read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4:
            continue
        console, xlayout, _model, xvariant = fields[:4]
        if xlayout != layout:
            continue
        if xvariant == wanted_variant:
            return console
        if xvariant == "-" and layout_only is None:
            layout_only = console
    if variant and keymaps_root.is_dir():
        if any(keymaps_root.rglob(f"{layout}-{variant}.map.gz")):
            return f"{layout}-{variant}"
    if layout_only:
        return layout_only
    if keymaps_root.is_dir() and any(keymaps_root.rglob(f"{layout}.map.gz")):
        return layout
    return "us"


def vconsole_conf(keymap: str) -> str:
    return f"KEYMAP={keymap}\n"


def x11_keyboard_conf(layout: str, variant: str = "") -> str:
    lines = [
        'Section "InputClass"',
        '        Identifier "system-keyboard"',
        '        MatchIsKeyboard "on"',
        f'        Option "XkbLayout" "{layout}"',
    ]
    if variant:
        lines.append(f'        Option "XkbVariant" "{variant}"')
    lines.append("EndSection")
    return "\n".join(lines) + "\n"


def plasma_kxkbrc(layout: str, variant: str = "") -> str:
    return f"[Layout]\nLayoutList={layout}\nVariantList={variant}\nUse=true\n"


def apply_plasma_layout(user: str, layout: str, variant: str = "") -> None:
    """Switch a running Plasma session's keyboard layout (from root).

    KWin watches kxkbrc through KConfigWatcher, which only reacts to the
    change notifications that ``kwriteconfig6 --notify`` sends on the
    session bus, so each key is written that way, as the session's user.
    The variant goes first so the new layout never pairs with a stale one.
    """
    account = pwd.getpwnam(user)
    environment = [
        "env", "-i",
        f"HOME={account.pw_dir}",
        "PATH=/usr/bin",
        "LANG=C.UTF-8",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{account.pw_uid}/bus",
    ]
    for key, value in (("VariantList", variant), ("LayoutList", layout), ("Use", "true")):
        subprocess.run(
            [
                "runuser", "-u", user, "--", *environment,
                "kwriteconfig6", "--notify", "--file", "kxkbrc",
                "--group", "Layout", "--key", key, value,
            ],
            capture_output=True,
            check=False,
        )
