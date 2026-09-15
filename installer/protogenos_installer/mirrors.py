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


PARALLEL_DOWNLOADS = 15


def reflector_command(country: str = "", mirrorlist: str = "/etc/pacman.d/mirrorlist") -> list[str]:
    """Rank recently synced HTTPS mirrors by measured speed, worldwide or in one country."""
    command = ["reflector"]
    if country:
        command += ["--country", country]
    return command + [
        "--protocol",
        "https",
        "--age",
        "12",
        "--latest",
        "30",
        "--fastest",
        "10",
        "--sort",
        "rate",
        "--threads",
        "8",
        "--connection-timeout",
        "5",
        "--download-timeout",
        "5",
        "--save",
        mirrorlist,
    ]


def enable_parallel_downloads(config: str, count: int = PARALLEL_DOWNLOADS) -> str:
    """Set pacman's ParallelDownloads to count, replacing any existing value."""
    lines = config.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^#?\s*ParallelDownloads\s*=", line.strip()):
            lines[index] = f"ParallelDownloads = {count}"
            return "\n".join(lines) + "\n"
    for index, line in enumerate(lines):
        if line.strip() == "[options]":
            lines.insert(index + 1, f"ParallelDownloads = {count}")
            return "\n".join(lines) + "\n"
    return config
