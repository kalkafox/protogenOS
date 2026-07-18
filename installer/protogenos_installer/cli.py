from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .models import OptionGroup
from .profiles import PERSONAS, ProfileError, ProfileRepository


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_selection(value: str) -> tuple[str, tuple[str, ...]]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("selection must use GROUP=CHOICE[,CHOICE]")
    group, raw_choices = value.split("=", 1)
    if not group:
        raise argparse.ArgumentTypeError("selection group cannot be empty")
    choices = tuple(choice.strip() for choice in raw_choices.split(",") if choice.strip())
    return group, choices


def _choose_persona() -> str:
    print("\nChoose how you plan to use protogenOS:")
    for index, persona in enumerate(PERSONAS, 1):
        print(f"  {index}. {persona.title()}")
    while True:
        response = input("Persona [1]: ").strip() or "1"
        if response.isdigit() and 1 <= int(response) <= len(PERSONAS):
            return PERSONAS[int(response) - 1]
        print("Enter one of the displayed numbers.")


def _choose_group(group: OptionGroup) -> tuple[str, ...]:
    print(f"\n{group.name.replace('-', ' ').title()} ({group.selection}):")
    defaults: list[int] = []
    for index, choice in enumerate(group.choices, 1):
        source = "" if choice.source == "official" else f", {choice.source.upper()}"
        marker = " [default]" if choice.default else ""
        print(f"  {index}. {choice.label} ({choice.package}{source}){marker}")
        if choice.default:
            defaults.append(index)

    default_text = ",".join(str(index) for index in defaults) or "none"
    while True:
        response = input(f"Selection [{default_text}]: ").strip()
        if not response:
            indexes = defaults
        elif response.lower() in {"none", "skip"}:
            indexes = []
        else:
            try:
                indexes = [int(item.strip()) for item in response.split(",")]
            except ValueError:
                print("Enter comma-separated numbers or 'none'.")
                continue
        if any(index < 1 or index > len(group.choices) for index in indexes):
            print("One or more choices are outside the displayed range.")
            continue
        if group.selection in {"one-of", "optional"} and len(set(indexes)) > 1:
            print("Choose at most one item from this group.")
            continue
        return tuple(group.choices[index - 1].identifier for index in dict.fromkeys(indexes))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a protogenOS installation plan")
    parser.add_argument("--profiles-dir", type=Path, default=_project_root() / "profiles")
    parser.add_argument("--persona", choices=PERSONAS)
    parser.add_argument(
        "--select",
        action="append",
        default=[],
        metavar="GROUP=CHOICE[,CHOICE]",
        type=_parse_selection,
    )
    parser.add_argument("--allow-aur", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--output", type=Path, help="write the plan as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        repository = ProfileRepository(args.profiles_dir)
        persona = args.persona
        if persona is None:
            if args.non_interactive:
                parser.error("--persona is required with --non-interactive")
            persona = _choose_persona()

        selections: dict[str, tuple[str, ...]] = {}
        for group, choices in args.select:
            if group in selections:
                parser.error(f"--select was provided more than once for {group!r}")
            selections[group] = choices
        if not args.non_interactive:
            for group in repository.groups_for(persona):
                if group.name not in selections:
                    selections[group.name] = _choose_group(group)

        plan = repository.resolve(persona, selections)
        if plan.aur_packages and not args.allow_aur:
            packages = ", ".join(plan.aur_packages)
            if args.non_interactive:
                parser.error(f"AUR packages require --allow-aur: {packages}")
            response = input(f"\nBuild these AUR packages as the target user: {packages}? [y/N] ")
            if response.strip().lower() not in {"y", "yes"}:
                print("Installation plan cancelled; no disks were modified.")
                return 1

        print(f"\nprotogenOS {plan.persona.title()} installation plan")
        print(f"Packages: {len(plan.packages)}")
        print(f"Multilib required: {'yes' if plan.multilib_required else 'no'}")
        if plan.aur_packages:
            print(f"AUR packages: {', '.join(plan.aur_packages)}")
        print("  " + "\n  ".join(plan.packages))

        if args.output:
            args.output.write_text(json.dumps(plan.to_dict(), indent=2) + "\n")
            print(f"\nPlan written to {args.output}")
        print("\nPlanning complete. No disks were modified; the install backend is not enabled yet.")
        return 0
    except (OSError, ProfileError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
