"""protogenOS boot artwork: Plymouth splash, GRUB theme, and ISO boot splash.

Everything is drawn here as an LED matrix (a paw print for now) with only
the standard library, so the ISO build and the installer render identical files without
image tools or binary assets in the repository.

    python -m protogenos_installer.boot_art plymouth DIR
    python -m protogenos_installer.boot_art grub DIR
    python -m protogenos_installer.boot_art syslinux FILE
"""

from __future__ import annotations

import math
from functools import lru_cache
import struct
import sys
import zlib
from pathlib import Path

# 21 x 15 LED matrix: a paw print. A placeholder until the protogen visor
# artwork is final.
LOGO = (
    "......###...###......",
    ".....#####.#####.....",
    ".....#####.#####.....",
    ".....#####.#####.....",
    ".###..###...###..###.",
    "#####...........#####",
    "#####...........#####",
    "#####...#####...#####",
    ".###..#########..###.",
    ".....###########.....",
    "....#############....",
    "....#############....",
    "....#############....",
    ".....###########.....",
    "......#########......",
)

# Palette from config/theme.conf.
VOID = (0x09, 0x09, 0x0B)
CARBON = (0x14, 0x12, 0x16)
RAISED = (0x21, 0x1B, 0x20)
LOGO_RED = (0xFF, 0x40, 0x5C)
DEEP_RED = (0x72, 0x14, 0x26)
SNOW = (0xF5, 0xF1, 0xF2)
ALLOY = (0xBE, 0xB3, 0xB7)

THROBBER_FRAMES = 36
PLYMOUTH_THEME = "protogenos"
PLYMOUTH_THEME_DIR = f"/usr/share/plymouth/themes/{PLYMOUTH_THEME}"
GRUB_THEME_DIR = "/usr/share/grub/themes/protogenos"


def _hex(color: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)


class Canvas:
    """RGBA image with straight-alpha "over" blending."""

    def __init__(self, width: int, height: int, fill: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray(bytes(fill) * (width * height))

    def blend(self, x: int, y: int, color: tuple[int, int, int], alpha: float) -> None:
        if alpha <= 0 or not (0 <= x < self.width and 0 <= y < self.height):
            return
        alpha = min(alpha, 1.0)
        index = (y * self.width + x) * 4
        dst_a = self.pixels[index + 3] / 255
        out_a = alpha + dst_a * (1 - alpha)
        for channel in range(3):
            src = color[channel]
            dst = self.pixels[index + channel]
            value = (src * alpha + dst * dst_a * (1 - alpha)) / out_a if out_a else 0
            self.pixels[index + channel] = int(round(value))
        self.pixels[index + 3] = int(round(out_a * 255))

    def disc(
        self, cx: float, cy: float, radius: float, color: tuple[int, int, int], alpha: float = 1.0,
        *, glow: float = 0.0,
    ) -> None:
        """Anti-aliased filled circle, optionally with a soft halo of width glow."""
        reach = radius + glow + 1
        for y in range(int(cy - reach), int(cy + reach) + 1):
            for x in range(int(cx - reach), int(cx + reach) + 1):
                distance = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                coverage = min(max(radius + 0.5 - distance, 0.0), 1.0)
                if glow and distance > radius:
                    halo = max(0.0, 1 - (distance - radius) / glow) ** 2 * 0.35
                    coverage = max(coverage, halo)
                self.blend(x, y, color, coverage * alpha)

    def rounded_rect(
        self, left: int, top: int, width: int, height: int, radius: float,
        color: tuple[int, int, int], alpha: float = 1.0,
    ) -> None:
        for y in range(top, top + height):
            for x in range(left, left + width):
                dx = max(left + radius - (x + 0.5), 0, (x + 0.5) - (left + width - radius))
                dy = max(top + radius - (y + 0.5), 0, (y + 0.5) - (top + height - radius))
                coverage = min(max(radius + 0.5 - math.hypot(dx, dy), 0.0), 1.0) if dx or dy else 1.0
                self.blend(x, y, color, coverage * alpha)

    def png(self) -> bytes:
        rows = b"".join(
            b"\x00" + bytes(self.pixels[y * self.width * 4 : (y + 1) * self.width * 4])
            for y in range(self.height)
        )

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 6, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b"")
        )


def draw_logo(
    canvas: Canvas, left: float, top: float, pitch: float, *,
    brightness=lambda column, row: 1.0, unlit_alpha: float = 0.18, alpha: float = 1.0,
) -> None:
    """Draw the LED matrix; brightness(column, row) scales each lit LED."""
    radius = pitch * 0.34
    for row, line in enumerate(LOGO):
        for column, cell in enumerate(line):
            cx = left + (column + 0.5) * pitch
            cy = top + (row + 0.5) * pitch
            if cell == "#":
                level = brightness(column, row)
                canvas.disc(cx, cy, radius, LOGO_RED, alpha * level, glow=pitch * 0.45 * level)
            else:
                canvas.disc(cx, cy, radius * 0.8, DEEP_RED, alpha * unlit_alpha)


def logo_size(pitch: float) -> tuple[int, int]:
    return int(len(LOGO[0]) * pitch), int(len(LOGO) * pitch)


def throbber_frame(index: int, frames: int = THROBBER_FRAMES, pitch: int = 14) -> Canvas:
    """One frame of a scan sweeping across the logo; the frames loop seamlessly."""
    margin = pitch
    width, height = logo_size(pitch)
    canvas = Canvas(width + 2 * margin, height + 2 * margin)
    columns = len(LOGO[0])
    phase = index / frames

    def brightness(column: int, _row: int) -> float:
        # A bright band travels left to right; distance wraps around.
        offset = (column / columns - phase) % 1.0
        distance = min(offset, 1 - offset)
        return 0.65 + 0.35 * math.exp(-((distance / 0.12) ** 2))

    draw_logo(canvas, margin, margin, pitch, brightness=brightness)
    return canvas


def bullet() -> Canvas:
    canvas = Canvas(14, 14)
    canvas.disc(7, 7, 4.5, LOGO_RED, glow=2)
    return canvas


def entry() -> Canvas:
    canvas = Canvas(300, 36)
    canvas.rounded_rect(0, 0, 300, 36, 8, LOGO_RED)
    canvas.rounded_rect(2, 2, 296, 32, 6, CARBON)
    return canvas


def lock() -> Canvas:
    canvas = Canvas(24, 30)
    # Shackle: a ring whose lower half the body covers.
    for angle_step in range(0, 181, 3):
        angle = math.radians(180 + angle_step)
        canvas.disc(12 + 7 * math.cos(angle), 13 + 8 * math.sin(angle), 1.6, ALLOY)
    for y in range(13, 17):
        canvas.disc(5, y, 1.6, ALLOY)
        canvas.disc(19, y, 1.6, ALLOY)
    canvas.rounded_rect(2, 14, 20, 15, 3, LOGO_RED)
    canvas.disc(12, 21, 2.2, VOID)
    return canvas


def plymouth_config(font: str = "Noto Sans 12") -> str:
    background = "0x{:02x}{:02x}{:02x}".format(*VOID)
    return f"""[Plymouth Theme]
Name=protogenOS
Description=An LED paw print, scanning while protogenOS starts.
ModuleName=two-step

[two-step]
Font={font}
TitleFont={font}
ImageDir={PLYMOUTH_THEME_DIR}
DialogHorizontalAlignment=.5
DialogVerticalAlignment=.75
TitleHorizontalAlignment=.5
TitleVerticalAlignment=.30
HorizontalAlignment=.5
VerticalAlignment=.45
Transition=none
TransitionDuration=0.0
BackgroundStartColor={background}
BackgroundEndColor={background}
ProgressBarBackgroundColor=0x211b20
ProgressBarForegroundColor=0xff405c
MessageBelowAnimation=true

[boot-up]
UseEndAnimation=false

[shutdown]
UseEndAnimation=false

[reboot]
UseEndAnimation=false

[updates]
SuppressMessages=true
ProgressBarShowPercentComplete=true
UseProgressBar=true
Title=Installing updates...
SubTitle=Do not turn off your computer

[system-upgrade]
SuppressMessages=true
ProgressBarShowPercentComplete=true
UseProgressBar=true
Title=Upgrading the system...
SubTitle=Do not turn off your computer

[firmware-upgrade]
SuppressMessages=true
ProgressBarShowPercentComplete=true
UseProgressBar=true
Title=Upgrading firmware...
SubTitle=Do not turn off your computer
"""


@lru_cache(maxsize=1)
def plymouth_images() -> dict[str, bytes]:
    """Rendered once per process; drawing in pure Python takes seconds."""
    images = {
        f"throbber-{index + 1:04d}.png": throbber_frame(index).png()
        for index in range(THROBBER_FRAMES)
    }
    images.update(
        {"bullet.png": bullet().png(), "entry.png": entry().png(), "lock.png": lock().png()}
    )
    return images


def write_plymouth_theme(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{PLYMOUTH_THEME}.plymouth").write_text(plymouth_config())
    for name, data in plymouth_images().items():
        (directory / name).write_bytes(data)


def background(width: int, height: int, *, logo_top: float, logo_pitch: float) -> Canvas:
    """Dark gradient with a dimmed logo; menus are drawn over the lower part."""
    canvas = Canvas(width, height)
    for y in range(height):
        t = y / max(height - 1, 1)
        color = tuple(int(VOID[i] + (CARBON[i] - VOID[i]) * t) for i in range(3))
        row = bytes((*color, 255)) * width
        canvas.pixels[y * width * 4 : (y + 1) * width * 4] = row
    logo_width, _ = logo_size(logo_pitch)
    draw_logo(canvas, (width - logo_width) / 2, logo_top, logo_pitch, unlit_alpha=0.12, alpha=0.85)
    return canvas


def grub_theme() -> str:
    return f"""# protogenOS GRUB theme
title-text: ""
desktop-image: "background.png"
desktop-color: "{_hex(VOID)}"
terminal-font: "Unifont Regular 16"
# "Loading Linux..." appears here while booting; keep it under the logo.
terminal-left: "25%"
terminal-top: "46%"
terminal-width: "50%"
terminal-height: "40%"
terminal-border: "0"

+ boot_menu {{
    left = 25%
    top = 46%
    width = 50%
    height = 40%
    item_font = "Unifont Regular 16"
    item_color = "{_hex(ALLOY)}"
    selected_item_font = "Unifont Regular 16"
    selected_item_color = "{_hex(LOGO_RED)}"
    item_height = 30
    item_padding = 8
    item_spacing = 6
    icon_width = 0
    icon_height = 0
    scrollbar = false
}}

+ label {{
    left = 0
    top = 92%
    width = 100%
    align = "center"
    id = "__timeout__"
    text = "Starting protogenOS in %d seconds"
    color = "{_hex(ALLOY)}"
    font = "Unifont Regular 16"
}}
"""


def write_grub_theme(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "theme.txt").write_text(grub_theme())
    (directory / "background.png").write_bytes(grub_background())


@lru_cache(maxsize=1)
def grub_background() -> bytes:
    return background(1920, 1080, logo_top=150, logo_pitch=22).png()


def write_syslinux_splash(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(background(640, 480, logo_top=30, logo_pitch=7).png())


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"plymouth", "grub", "syslinux"}:
        print("usage: python -m protogenos_installer.boot_art {plymouth,grub,syslinux} PATH", file=sys.stderr)
        return 2
    kind, target = argv[0], Path(argv[1])
    {"plymouth": write_plymouth_theme, "grub": write_grub_theme, "syslinux": write_syslinux_splash}[kind](target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
