from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from .models import InstallPlan, OptionGroup, PackageChoice

PERSONAS = ("general", "gamer", "developer")
SELECTION_TYPES = {"one-of", "any-of", "optional"}
PACKAGE_SOURCES = {"official", "aur", "future"}
PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9@._+:-]+$")


class ProfileError(ValueError):
    """Raised when profile data or a requested selection is invalid."""


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def load_package_manifest(path: Path) -> tuple[str, ...]:
    packages: list[str] = []
    for line_number, raw_line in enumerate(path.read_text().splitlines(), 1):
        package = raw_line.strip()
        if not package or package.startswith("#"):
            continue
        if not PACKAGE_PATTERN.fullmatch(package):
            raise ProfileError(f"{path}:{line_number}: invalid package {package!r}")
        packages.append(package)
    return _unique(packages)


def load_options(path: Path) -> tuple[PackageChoice, ...]:
    choices: list[PackageChoice] = []
    seen: set[tuple[str, str]] = set()
    for line_number, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 8:
            raise ProfileError(f"{path}:{line_number}: expected 8 fields")
        group, selection, identifier, label, package, source, default, profiles = fields
        if selection not in SELECTION_TYPES:
            raise ProfileError(f"{path}:{line_number}: invalid selection type {selection!r}")
        if source not in PACKAGE_SOURCES:
            raise ProfileError(f"{path}:{line_number}: invalid source {source!r}")
        if default not in {"yes", "no"}:
            raise ProfileError(f"{path}:{line_number}: default must be yes or no")
        profile_set = frozenset(item.strip() for item in profiles.split(",") if item.strip())
        if not profile_set or not profile_set.issubset(PERSONAS):
            raise ProfileError(f"{path}:{line_number}: invalid profile list {profiles!r}")
        if not PACKAGE_PATTERN.fullmatch(package):
            raise ProfileError(f"{path}:{line_number}: invalid package {package!r}")
        key = (group, identifier)
        if key in seen:
            raise ProfileError(f"{path}:{line_number}: duplicate choice {group}.{identifier}")
        seen.add(key)
        choices.append(
            PackageChoice(
                group=group,
                selection=selection,
                identifier=identifier,
                label=label,
                package=package,
                source=source,
                default=default == "yes",
                profiles=profile_set,
            )
        )

    group_types: dict[str, str] = {}
    for choice in choices:
        previous = group_types.setdefault(choice.group, choice.selection)
        if previous != choice.selection:
            raise ProfileError(f"option group {choice.group!r} mixes selection types")
    return tuple(choices)


class ProfileRepository:
    def __init__(self, root: Path):
        self.root = root
        self._choices = load_options(root / "options.conf")

    def groups_for(self, persona: str) -> tuple[OptionGroup, ...]:
        self._validate_persona(persona)
        grouped: dict[str, list[PackageChoice]] = {}
        for choice in self._choices:
            if persona in choice.profiles:
                grouped.setdefault(choice.group, []).append(choice)
        return tuple(
            OptionGroup(name=name, selection=choices[0].selection, choices=tuple(choices))
            for name, choices in grouped.items()
        )

    def resolve(
        self,
        persona: str,
        selections: Mapping[str, Iterable[str]] | None = None,
    ) -> InstallPlan:
        self._validate_persona(persona)
        requested = {name: tuple(values) for name, values in (selections or {}).items()}
        groups = {group.name: group for group in self.groups_for(persona)}
        unknown_groups = set(requested) - set(groups)
        if unknown_groups:
            raise ProfileError(f"unknown option groups: {', '.join(sorted(unknown_groups))}")

        packages: list[str] = list(load_package_manifest(self.root / "base.packages"))
        packages.extend(load_package_manifest(self.root / "general.packages"))
        if persona != "general":
            packages.extend(load_package_manifest(self.root / f"{persona}.packages"))

        resolved: dict[str, tuple[str, ...]] = {}
        aur_packages: list[str] = []
        for name, group in groups.items():
            identifiers = requested.get(
                name,
                tuple(choice.identifier for choice in group.choices if choice.default),
            )
            identifiers = _unique(identifiers)
            if group.selection in {"one-of", "optional"} and len(identifiers) > 1:
                raise ProfileError(f"option group {name!r} accepts at most one choice")
            choices_by_id = {choice.identifier: choice for choice in group.choices}
            invalid = set(identifiers) - set(choices_by_id)
            if invalid:
                raise ProfileError(f"invalid choices for {name}: {', '.join(sorted(invalid))}")

            resolved[name] = identifiers
            for identifier in identifiers:
                choice = choices_by_id[identifier]
                if choice.source == "future":
                    raise ProfileError(f"{choice.label} is planned but not installable yet")
                packages.append(choice.package)
                if choice.source == "aur":
                    aur_packages.append(choice.package)

        package_list = _unique(packages)
        multilib_required = any(
            package == "steam" or package.startswith("lib32-") for package in package_list
        )
        return InstallPlan(
            persona=persona,
            packages=package_list,
            selections=resolved,
            aur_packages=_unique(aur_packages),
            multilib_required=multilib_required,
        )

    @staticmethod
    def _validate_persona(persona: str) -> None:
        if persona not in PERSONAS:
            raise ProfileError(f"unknown persona {persona!r}; choose from {', '.join(PERSONAS)}")
