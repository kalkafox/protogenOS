"""Mirror selection with reflector and pacman download tuning."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

COUNTRY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .,'()-]{0,63}$")


@dataclass(frozen=True, slots=True)
class MirrorCountry:
    name: str
    code: str
    count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_reflector_countries(output: str) -> tuple[MirrorCountry, ...]:
    """Parse `reflector --list-countries` (a Country/Code/Count table)."""
    countries: list[MirrorCountry] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or not fields[-1].isdigit():
            continue
        code = fields[-2]
        if not re.fullmatch(r"[A-Z]{2}", code):
            continue
        countries.append(MirrorCountry(" ".join(fields[:-2]), code, int(fields[-1])))
    return tuple(countries)


def reflector_command(country: str, mirrorlist: str = "/etc/pacman.d/mirrorlist") -> list[str]:
    return [
        "reflector",
        "--country",
        country,
        "--protocol",
        "https",
        "--latest",
        "20",
        "--sort",
        "rate",
        "--connection-timeout",
        "5",
        "--download-timeout",
        "5",
        "--save",
        mirrorlist,
    ]


def enable_parallel_downloads(config: str, count: int = 5) -> str:
    """Turn on pacman's ParallelDownloads unless the config already sets it."""
    lines = config.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^ParallelDownloads\s*=", stripped):
            return config if config.endswith("\n") else config + "\n"
        if re.match(r"^#\s*ParallelDownloads\s*=", stripped):
            lines[index] = f"ParallelDownloads = {count}"
            return "\n".join(lines) + "\n"
    for index, line in enumerate(lines):
        if line.strip() == "[options]":
            lines.insert(index + 1, f"ParallelDownloads = {count}")
            return "\n".join(lines) + "\n"
    return config
