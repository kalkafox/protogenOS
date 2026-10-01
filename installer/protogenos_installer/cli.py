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
    UserAccount,
    format_size,
    detect_firmware,
    list_install_disks,
)
from .branding import INSTALLER_BANNER, INSTALLER_TAGLINE
from .config_io import ConfigFileError, export_config, load_documents, read_json, write_json
from .features import feature_settings, offered_features
from .hardware import detect_features, detect_hardware
from .keyboard import LAYOUT_PATTERN, VARIANT_PATTERN, console_keymap
from .locales import suggest_locale
from .mirrors import COUNTRY_PATTERN
from .models import InstallPlan, OptionGroup
from .network import is_online
from .preflight import describe_problems, run_checks
from .storage import GIB, MIN_ROOT_BYTES, read_disk_layout
from .profiles import PERSONAS, ProfileError, ProfileRepository
from .server import (
    SERVER_PERSONA,
    ServerConfigError,
    fetch_github_keys,
    parse_authorized_keys,
    validate_static_network,
)


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


def _show_preflight() -> None:
    """Warn about machine problems before any questions (they never block)."""
    try:
        problems = describe_problems(run_checks())
    except Exception:  # noqa: BLE001 - checks are advisory
        return
    if problems:
        print(f"\n{problems}")


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
                status = _colorize("has existing partitions", _YELLOW)
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
        confirmation = input(f"Use {disk.path}? You choose how it is partitioned next. [y/N] ").strip().lower()
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


def _choose(prompt: str, options: Sequence[str], default: str) -> str:
    while True:
        response = input(f"{prompt} ({'/'.join(options)}) [{default}]: ").strip().lower() or default
        if response in options:
            return response
        print(f"Enter one of: {', '.join(options)}.")


def _yes(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    response = input(f"{prompt} [{hint}] ").strip().lower()
    return default if not response else response in {"y", "yes"}


def _choose_install_config(plan: InstallPlan, *, dry_run: bool = False) -> InstallConfig | None:
    if not dry_run and os.geteuid() != 0:
        raise InstallError("disk installation must run as root")
    runner = CommandRunner()
    disk = _choose_disk(runner)
    if disk is None:
        return None

    firmware = detect_firmware()
    print(f"Detected boot mode: {firmware.upper()}")

    layouts = ["erase"]
    root_partition = boot_partition = None
    format_boot = False
    if firmware == "uefi":
        layout = read_disk_layout(runner, disk.path)
        largest = layout.largest_free.size if layout.largest_free else 0
        if layout.table == "gpt" and largest >= MIN_ROOT_BYTES + GIB:
            layouts.append("free-space")
            print(f"  free-space: install alongside existing partitions ({format_size(largest)} unallocated)")
        if len(layout.partitions) >= 2:
            layouts.append("partitions")
            print("  partitions: format an existing root partition and use an EFI partition for /boot")
            for part in layout.partitions:
                print(f"    {part.path}  {format_size(part.size)}  {part.fstype or '-'}  {part.label}")
    print("  erase: " + _colorize("delete everything on the disk", _RED))
    disk_layout = _choose("Installation type", layouts, "erase")
    if disk_layout == "partitions":
        root_partition = input("Root partition to FORMAT (e.g. /dev/sda3): ").strip()
        boot_partition = input("EFI system partition for /boot (e.g. /dev/sda1): ").strip()
        format_boot = _yes(f"Format {boot_partition}? (removes other bootloaders on it)")

    filesystem = _choose("Root filesystem", ["btrfs", "ext4", "xfs", "f2fs"], "btrfs")
    btrfs_subvolumes = filesystem != "btrfs" or _yes(
        "Create Btrfs subvolumes (@, @home, @log, @pkg, @snapshots)?", True
    )
    encrypt = _yes("Encrypt the system with LUKS2?")
    passphrase = _prompt_password("disk encryption") if encrypt else None
    swap = "zram" if _yes("Enable compressed swap in RAM (zram)?", True) else "none"
    bootloader = (
        _choose("Bootloader", ["grub", "systemd-boot", "limine"], "grub") if firmware == "uefi" else "grub"
    )
    offers = offered_features(
        plan.persona,
        filesystem=filesystem,
        btrfs_subvolumes=btrfs_subvolumes,
        encrypt=encrypt,
        firmware=firmware,
        bootloader=bootloader,
        hardware=detect_hardware(desktop=plan.desktop),
        support=detect_features(),
    )
    features = feature_settings(
        offer.key for offer in offers if _yes(f"{offer.label} ({offer.detail})?", offer.default)
    )
    keyboard_layout = _prompt_matching("Keyboard layout (XKB, e.g. us, de, fr)", "us", LAYOUT_PATTERN, "Use a layout code such as us or de.")
    keyboard_variant = _prompt_matching("Keyboard variant (empty for default)", "", VARIANT_PATTERN, "Use a variant code such as nodeadkeys.")
    if not dry_run:
        runner.run(["loadkeys", console_keymap(keyboard_layout, keyboard_variant)], capture_output=True, check=False)

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
    timezone = input("Timezone [UTC]: ").strip() or "UTC"
    locale = _prompt_matching(
        "Locale",
        suggest_locale(timezone, keyboard_layout),
        LOCALE_PATTERN,
        "Use a UTF-8 locale such as en_US.UTF-8.",
    )
    mirror_country = _prompt_matching(
        "Mirror country (empty for fastest worldwide)", "", re.compile(rf"^$|{COUNTRY_PATTERN.pattern}"), "Use a country name such as Germany."
    )
    password = _prompt_password(username)

    grant_sudo = _yes(f"Grant {username} sudo (administrator) access?", True)
    root_password = None
    if not grant_sudo:
        print("Root will stay unlocked with its own password since this user won't have sudo.")
        root_password = _prompt_password("root")

    additional_users: list[UserAccount] = []
    while _yes("Create another user account?"):
        name = _prompt_matching("User name", "", USERNAME_PATTERN, "Use lowercase letters, numbers, _ or -.")
        additional_users.append(UserAccount(name, _prompt_password(name), _yes(f"Grant {name} sudo access?")))
    kernel_headers = _yes("Install kernel headers (for DKMS modules)?")
    server = _collect_server_settings(username) if plan.persona == SERVER_PERSONA else {}

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
        filesystem=filesystem,
        btrfs_subvolumes=btrfs_subvolumes,
        disk_partitioned=disk.partitioned,
        disk_layout=disk_layout,
        root_partition=root_partition,
        boot_partition=boot_partition,
        format_boot=format_boot,
        encrypt=encrypt,
        encryption_passphrase=passphrase,
        bootloader=bootloader,
        swap=swap,
        keyboard_layout=keyboard_layout,
        keyboard_variant=keyboard_variant,
        mirror_country=mirror_country,
        kernel_headers=kernel_headers,
        additional_users=tuple(additional_users),
        **features,
        **server,
    )
    config.validate()
    config.validate_for_persona(plan.persona)
    return config


def _collect_ssh_keys(username: str) -> tuple[str, ...]:
    print(f"\nPassword logins over SSH are disabled; add a public key for {username}.")
    keys: list[str] = []
    while True:
        source = _choose("Add a key by pasting it or from a GitHub username", ["paste", "github"], "paste")
        try:
            if source == "github":
                name = input("GitHub username: ").strip()
                found = fetch_github_keys(name)
                print(f"Found {len(found)} key(s) for {name}.")
            else:
                found = parse_authorized_keys(input("Public key (ssh-ed25519 AAAA... comment): "))
                if not found:
                    print("Paste a key starting with ssh-ed25519, ssh-rsa, or ecdsa-sha2-nistp256.")
        except ServerConfigError as error:
            print(error)
            continue
        keys.extend(key for key in found if key not in keys)
        if keys and not _yes("Add another key?"):
            return tuple(keys)


def _collect_static_network() -> dict[str, object]:
    if not _yes("Use a static IP address instead of DHCP?"):
        return {}
    while True:
        address = input("Address with prefix (e.g. 192.168.1.10/24; empty keeps DHCP): ").strip()
        if not address:
            return {}
        gateway = input("Gateway (empty for none): ").strip()
        dns = tuple(input("DNS servers, separated by spaces (empty for none): ").split())
        interface = input("Network interface (empty for any wired interface): ").strip()
        try:
            validate_static_network(address, gateway, dns, interface)
        except ServerConfigError as error:
            print(error)
            continue
        return {
            "static_address": address,
            "static_gateway": gateway,
            "static_dns": dns,
            "static_interface": interface,
        }


def _collect_server_settings(username: str) -> dict[str, object]:
    return {"ssh_authorized_keys": _collect_ssh_keys(username), **_collect_static_network()}


def _wait_for_network() -> bool:
    print("\nChecking internet connection...")
    while not is_online():
        print("No internet connection; packages are downloaded during installation.")
        print("Plug in Ethernet, or join Wi-Fi from another console (Alt+F2) with:")
        print("  iwctl station wlan0 connect <network name>")
        response = input("Press Enter to check again, or type q to quit: ").strip().lower()
        if response in {"q", "quit"}:
            return False
    return True


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


def confirmation_phrase(config: InstallConfig) -> str:
    if config.disk_layout == "partitions":
        return f"FORMAT {config.root_partition}"
    if config.disk_layout == "free-space":
        return f"INSTALL {config.disk}"
    return f"ERASE {config.disk}"


def _describe_extras(config: InstallConfig) -> str:
    extras = [
        label
        for enabled, label in (
            (config.snapshots, "snapshots"),
            (config.flatpak, "Flatpak"),
            (config.gaming_tweaks, "gaming tweaks"),
            (config.nvidia_driver == "nvidia-open", "NVIDIA open driver"),
            (config.fingerprint, "fingerprint login"),
            (config.tpm2_unlock, "TPM disk unlock"),
            (config.secure_boot, "Secure Boot"),
            (config.cockpit, "Cockpit"),
            (config.netdata, "Netdata"),
            (config.fail2ban, "fail2ban"),
            (config.update_downloads, "update downloads"),
            (config.serial_console, "serial console"),
        )
        if enabled
    ]
    return ", ".join(extras) or "none"


def describe_network(config: InstallConfig) -> str:
    if not config.static_address:
        return "DHCP"
    details = [config.static_address]
    if config.static_gateway:
        details.append(f"via {config.static_gateway}")
    if config.static_dns:
        details.append(f"DNS {', '.join(config.static_dns)}")
    if config.static_interface:
        details.append(f"on {config.static_interface}")
    return "static " + " ".join(details)


def _describe_filesystem(config: InstallConfig) -> str:
    if config.filesystem != "btrfs":
        return config.filesystem
    return "btrfs (subvolumes)" if config.btrfs_subvolumes else "btrfs (flat, no subvolumes)"


def _describe_target(config: InstallConfig) -> str:
    if config.disk_layout == "partitions":
        boot = "formatted" if config.format_boot else "kept"
        return f"{config.root_partition} (FORMATTED) as root, {config.boot_partition} ({boot}) as /boot"
    if config.disk_layout == "free-space":
        return f"new partitions in the free space on {config.disk}"
    return f"{config.disk} (ENTIRE DISK WILL BE ERASED)"


def _offer_chroot_shell(target_root: Path) -> None:
    if not sys.stdin.isatty():
        return
    if not _yes("\nOpen a shell inside the new system before unmounting it?"):
        return
    print("Type 'exit' to finish.")
    CommandRunner().run(["arch-chroot", str(target_root)], check=False)


def _finalize_install(
    plan, config: InstallConfig, *, dry_run: bool = False, unattended: bool = False
) -> int:
    print("\nInstallation summary")
    if dry_run:
        print("  (DRY RUN: no disks or system files will be touched)")
    print(f"  Target: {_describe_target(config)}")
    print(f"  Boot: {config.bootloader} ({config.firmware.upper()})")
    print(f"  Filesystem: {_describe_filesystem(config)}{', LUKS2 encrypted' if config.encrypt else ''}")
    print(f"  Swap: {config.swap}")
    print(f"  Extras: {_describe_extras(config)}")
    print(f"  Keyboard: {config.keyboard_layout}{' ' + config.keyboard_variant if config.keyboard_variant else ''}")
    print(f"  Hostname: {config.hostname}")
    users = [config.username, *(user.username for user in config.additional_users)]
    print(f"  Users: {', '.join(users)}")
    print(
        f"  Sudo access: {'yes (root login locked)' if config.grant_sudo else 'no (root has its own password)'}"
    )
    print(f"  Locale/timezone: {config.locale} / {config.timezone}")
    print(f"  Mirrors: {config.mirror_country or 'fastest worldwide'}")
    if plan.persona == SERVER_PERSONA:
        print(f"  SSH: key-only, {len(config.ssh_authorized_keys)} authorized key(s)")
        print(f"  Network: {describe_network(config)}")
    if not unattended:
        phrase = confirmation_phrase(config)
        confirmation = input(f"\nType {phrase} to begin: ").strip()
        if confirmation != phrase:
            print("Confirmation did not match. No disks were modified.")
            return 1

    backend = InstallerBackend(dry_run=dry_run)
    if dry_run:
        print(f"  Dry-run target root: {backend.target_root}")
    before_unmount = None if (dry_run or unattended) else _offer_chroot_shell
    backend.install(plan, config, before_unmount=before_unmount)
    print("\nprotogenOS installation completed successfully.")
    for warning in backend.warnings:
        print(_colorize(f"warning: {warning}", _YELLOW, _BOLD))
    print("You may reboot after removing the installation media.")
    return 0


def _save_config(path: Path | None, plan, config: InstallConfig) -> None:
    if path is None:
        return
    write_json(path, export_config(plan, config))
    print(f"Configuration saved to {path} (passwords are not included)")


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
    parser.add_argument(
        "--config",
        type=Path,
        help="install from a saved configuration (as written by --save-config or "
        "found at /var/log/protogenos-install.json on an installed system)",
    )
    parser.add_argument(
        "--creds",
        type=Path,
        help="JSON file with user_password, root_password, encryption_passphrase, "
        "and additional_users {name: password} for --config",
    )
    parser.add_argument("--save-config", type=Path, help="write the chosen configuration (without passwords) to this file")
    parser.add_argument(
        "--unattended",
        action="store_true",
        help="with --config: skip the typed confirmation and install immediately (DESTRUCTIVE)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log installation commands instead of running them, writing to a "
        "throwaway directory instead of the target disk (no root/disk required)",
    )
    return parser


def _install_from_config(args, repository: ProfileRepository) -> int:
    credentials = read_json(args.creds) if args.creds else None
    persona, selections, allow_aur, config = load_documents(read_json(args.config), credentials)
    plan = repository.resolve(persona, selections)
    if plan.aur_packages and not (allow_aur or args.allow_aur):
        raise InstallError(f"AUR packages require allow_aur or --allow-aur: {', '.join(plan.aur_packages)}")
    config.validate()
    config.validate_for_persona(plan.persona)
    _print_plan(plan)
    if args.output:
        _write_plan(args.output, plan)
    if not args.dry_run and not args.unattended and not _wait_for_network():
        print("Installer closed. No disks were modified.")
        return 0
    return _finalize_install(plan, config, dry_run=args.dry_run, unattended=args.unattended)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _show_title()
        _show_preflight()
        repository = ProfileRepository(args.profiles_dir)

        preset_selections: dict[str, tuple[str, ...]] = {}
        for group, choices in args.select:
            if group in preset_selections:
                parser.error(f"--select was provided more than once for {group!r}")
            preset_selections[group] = choices

        if args.unattended and args.config is None:
            parser.error("--unattended requires --config")
        if args.config is not None:
            return _install_from_config(args, repository)

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
            _save_config(args.save_config, plan, result.config)
            return _finalize_install(plan, result.config, dry_run=args.dry_run)

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
        if not args.dry_run and not _wait_for_network():
            print("Installer closed. No disks were modified.")
            return 0

        config = _choose_install_config(plan, dry_run=args.dry_run)
        if config is None:
            print("Installation cancelled. No disks were modified.")
            return 0
        _save_config(args.save_config, plan, config)
        return _finalize_install(plan, config, dry_run=args.dry_run)
    except (OSError, ProfileError, InstallError, ConfigFileError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInstallation interrupted; mounted target filesystems were cleaned up.")
        return 130
