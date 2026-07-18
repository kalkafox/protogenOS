from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PackageChoice:
    group: str
    selection: str
    identifier: str
    label: str
    package: str
    source: str
    default: bool
    profiles: frozenset[str]


@dataclass(frozen=True, slots=True)
class OptionGroup:
    name: str
    selection: str
    choices: tuple[PackageChoice, ...]


@dataclass(frozen=True, slots=True)
class InstallPlan:
    persona: str
    packages: tuple[str, ...]
    selections: dict[str, tuple[str, ...]]
    aur_packages: tuple[str, ...]
    multilib_required: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
