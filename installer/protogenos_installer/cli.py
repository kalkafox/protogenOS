from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from .backend import (
    HOSTNAME_PATTERN,
    LOCALE_PATTERN,
    USERNAME_PATTERN,
    CommandRunner,
    DiskInfo,
    InstallConfig,
    InstallError,
    InstallerBackend,
    format_size,
    list_install_disks,
)
from .branding import INSTALLER_BANNER, INSTALLER_TAGLINE
from .models import OptionGroup
from .profiles import PERSONAS, ProfileError, ProfileRepository


_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _color_enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _colorize(text: str, *codes: str) -> str:
    if not _color_enabled():
        return text
    return f"{''.join(codes)}{text}{_RESET}"


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


def _show_title() -> None:
    """Render the installer identity before any menus or plan output."""
    print(f"\n{INSTALLER_BANNER}")
    print(INSTALLER_TAGLINE)


def _choose_persona() -> str | None:
    print("\nChoose how you plan to use protogenOS:")
    for index, persona in enumerate(PERSONAS, 1):
        print(f"  {index}. {persona.title()}")
    print("  0. Exit to shell")
    while True:
        response = input("Persona [1]: ").strip() or "1"
        if response == "0":
            return None
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
        selected_count = len(set(indexes))
        if group.selection == "one-of" and selected_count != 1:
            print("Choose exactly one item from this group.")
            continue
        if group.selection == "optional" and selected_count > 1:
            print("Choose at most one item from this group.")
            continue
        return tuple(group.choices[index - 1].identifier for index in dict.fromkeys(indexes))


def _prompt_matching(
    prompt: str, default: str, pattern: re.Pattern[str], error: str
) -> str:
    while True:
        value = input(f"{prompt} [{default}]: ").strip() or default
        if pattern.fullmatch(value):
            return value
        print(error)


def _describe_disk(disk: DiskInfo) -> str:
    removable = ", removable" if disk.removable else ""
    return f"{disk.path} — {disk.model}, {format_size(disk.size)}{removable}"


def _choose_disk(runner: CommandRunner) -> DiskInfo | None:
    while True:
        disks = list_install_disks(runner)
        if not disks:
            raise InstallError("no unused writable disks were found")

        print("\nSelect the target disk:")
        for index, disk in enumerate(disks, 1):
            if disk.partitioned:
                status = _colorize("has existing partitions — will be erased", _RED)
            else:
                status = _colorize("empty, no partitions — safe to use", _GREEN)
            print(f"  {index}. {_describe_disk(disk)} [{status}]")
        print("  c. Open cfdisk to manage partitions manually, then return here")
        print("  0. Cancel and return to shell")

        response = input("Disk [number/c/0]: ").strip().lower()
        if response == "0":
            return None
        if response == "c":
            if shutil.which("cfdisk") is None:
                print("cfdisk is not available on this system.")
                continue
            sub = input("Which disk number should cfdisk open? ").strip()
            if not (sub.isdigit() and 1 <= int(sub) <= len(disks)):
                print("Enter one of the displayed disk numbers.")
                continue
            runner.run(["cfdisk", disks[int(sub) - 1].path], check=False)
            continue
        if not (response.isdigit() and 1 <= int(response) <= len(disks)):
            print("Enter one of the displayed disk numbers, 'c', or '0'.")
            continue

        disk = disks[int(response) - 1]
        if disk.partitioned:
            print(
                _colorize(
                    f"\n!!! WARNING: {disk.path} contains existing partitions and data. "
                    "Continuing WILL PERMANENTLY ERASE everything on it. !!!",
                    _BOLD,
                    _RED,
                )
            )
        else:
            print(_colorize(f"\n{disk.path} appears empty — no partitions detected.", _GREEN))
        confirmation = input(
            f"Use {disk.path}? It will be formatted and ALL data on it will be lost. [y/N] "
        ).strip().lower()
        if confirmation in {"y", "yes"}:
            return disk
        print("Returning to disk selection.")


def _prompt_password(label: str) -> str:
    while True:
        password = getpass.getpass(f"Password for {label}: ")
        confirmation = getpass.getpass("Confirm password: ")
        if not password:
            print("Password cannot be empty.")
        elif password != confirmation:
            print("Passwords did not match.")
        else:
            return password


def _choose_install_config() -> InstallConfig | None:
    if os.geteuid() != 0:
        raise InstallError("disk installation must run as root")
    runner = CommandRunner()
    disk = _choose_disk(runner)
    if disk is None:
        return None

    firmware = "uefi" if Path("/sys/firmware/efi").is_dir() else "bios"
    print(f"Detected boot mode: {firmware.upper()}")
    hostname = _prompt_matching(
        "Hostname",
        "protogenos",
        HOSTNAME_PATTERN,
        "Use only letters, numbers, and internal hyphens.",
    )
    username = _prompt_matching(
        "User name",
        "proto",
        USERNAME_PATTERN,
        "Start with a lowercase letter or underscore; use lowercase letters, numbers, _ or -.",
    )
    locale = _prompt_matching(
        "Locale",
        "en_US.UTF-8",
        LOCALE_PATTERN,
        "Use a UTF-8 locale such as en_US.UTF-8.",
    )
    timezone = input("Timezone [UTC]: ").strip() or "UTC"
    password = _prompt_password(username)

    grant_sudo = input(f"Grant {username} sudo (administrator) access? [Y/n] ").strip().lower() not in {
        "n",
        "no",
    }
    root_password = None
    if not grant_sudo:
        print("Root will stay unlocked with its own password since this user won't have sudo.")
        root_password = _prompt_password("root")

    config = InstallConfig(
        disk=disk.path,
        firmware=firmware,
        hostname=hostname,
        username=username,
        user_password=password,
        timezone=timezone,
        locale=locale,
        grant_sudo=grant_sudo,
        root_password=root_password,
    )
    config.validate()
    return config


def _tui_available() -> bool:
    try:
        import curses  # noqa: F401
    except ImportError:
        return False
    return sys.stdout.isatty() and sys.stdin.isatty()


def _print_plan(plan) -> None:
    print(f"\nprotogenOS {plan.persona.title()} installation plan")
    print(f"Packages: {len(plan.packages)}")
    print(f"Multilib required: {'yes' if plan.multilib_required else 'no'}")
    if plan.aur_packages:
        print(f"AUR packages: {', '.join(plan.aur_packages)}")
    print("  " + "\n  ".join(plan.packages))


def _write_plan(output: Path, plan) -> None:
    output.write_text(json.dumps(plan.to_dict(), indent=2) + "\n")
    print(f"\nPlan written to {output}")


def _finalize_install(plan, config: InstallConfig) -> int:
    print("\nInstallation summary")
    print(f"  Target: {config.disk} (ENTIRE DISK WILL BE ERASED)")
    print(f"  Boot mode: {config.firmware.upper()}")
    print(f"  Hostname: {config.hostname}")
    print(f"  User: {config.username}")
    print(
        f"  Sudo access: {'yes (root login locked)' if config.grant_sudo else 'no (root has its own password)'}"
    )
    print(f"  Locale/timezone: {config.locale} / {config.timezone}")
    confirmation = input(f"\nType ERASE {config.disk} to begin: ").strip()
    if confirmation != f"ERASE {config.disk}":
        print("Confirmation did not match. No disks were modified.")
        return 1

    InstallerBackend().install(plan, config)
    print("\nprotogenOS installation completed successfully.")
    print("You may reboot after removing the installation media.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or install a protogenOS system")
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
    parser.add_argument(
        "--lo-fi",
        action="store_true",
        help="use the plain numbered prompts instead of the curses TUI",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _show_title()
        repository = ProfileRepository(args.profiles_dir)

        preset_selections: dict[str, tuple[str, ...]] = {}
        for group, choices in args.select:
            if group in preset_selections:
                parser.error(f"--select was provided more than once for {group!r}")
            preset_selections[group] = choices

        if args.persona is None and args.non_interactive:
            parser.error("--persona is required with --non-interactive")

        if args.non_interactive:
            plan = repository.resolve(args.persona, preset_selections)
            if plan.aur_packages and not args.allow_aur:
                packages = ", ".join(plan.aur_packages)
                parser.error(f"AUR packages require --allow-aur: {packages}")
            _print_plan(plan)
            if args.output:
                _write_plan(args.output, plan)
            print("\nPlan complete. No disks were modified.")
            return 0

        use_tui = not args.lo_fi and _tui_available()

        if use_tui:
            from . import tui

            result = tui.run_wizard(repository, args, preset_selections)
            if result.cancelled:
                print("\nInstaller closed. Run protogenos-install to return.")
                return 0
            plan = result.plan
            _print_plan(plan)
            if args.output:
                _write_plan(args.output, plan)
            if result.aur_declined:
                print("Installation plan cancelled; no disks were modified.")
                return 1
            if result.config is None:
                print("Installer closed. No disks were modified.")
                return 0
            return _finalize_install(plan, result.config)

        persona = args.persona
        if persona is None:
            persona = _choose_persona()
            if persona is None:
                print("\nInstaller closed. Run protogenos-install to return.")
                return 0

        selections = dict(preset_selections)
        for group in repository.groups_for(persona):
            if group.name not in selections:
                selections[group.name] = _choose_group(group)

        plan = repository.resolve(persona, selections)
        if plan.aur_packages and not args.allow_aur:
            packages = ", ".join(plan.aur_packages)
            response = input(f"\nBuild these AUR packages as the target user: {packages}? [y/N] ")
            if response.strip().lower() not in {"y", "yes"}:
                print("Installation plan cancelled; no disks were modified.")
                return 1

        _print_plan(plan)
        if args.output:
            _write_plan(args.output, plan)

        response = input("\nInstall this plan to a disk now? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            print("Installer closed. No disks were modified.")
            return 0

        config = _choose_install_config()
        if config is None:
            print("Installation cancelled. No disks were modified.")
            return 0
        return _finalize_install(plan, config)
    except (OSError, ProfileError, InstallError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInstallation interrupted; mounted target filesystems were cleaned up.")
        return 130
