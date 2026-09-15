from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Personas installed without the Plasma desktop layer.
HEADLESS_PERSONAS = frozenset({"minimal", "server"})


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
    # Personas this choice is preselected for; groups_for() resolves default.
    default_profiles: frozenset[str] = frozenset()


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

    @property
    def desktop(self) -> bool:
        return self.persona not in HEADLESS_PERSONAS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
