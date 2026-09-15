"""Curses-based fancy installer wizard.

This is the default interactive frontend. It drives persona/option/disk/user
selection through arrow-key and space-checkbox menus. The destructive disk
operations and final install run outside curses as plain scrolling text
(see cli.py) so subprocess output during partitioning/pacstrap is never
fighting curses for the terminal.
"""

from __future__ import annotations

import curses
import os
import shutil
import time
from dataclasses import dataclass

from .backend import (
    HOSTNAME_PATTERN,
    LOCALE_PATTERN,
    USERNAME_PATTERN,
    CommandRunner,
    DiskInfo,
    InstallConfig,
    InstallError,
    UserAccount,
    format_size,
    detect_firmware,
    list_install_disks,
    list_timezones,
    search_timezones,
)
from .branding import INSTALLER_BANNER, INSTALLER_TAGLINE
from .features import feature_settings, offered_features
from .hardware import detect_features, detect_hardware
from .keyboard import console_keymap, list_layouts
from .locales import suggest_locale
from .mirrors import COUNTRY_PATTERN
from .storage import GIB, MIN_ROOT_BYTES, DiskLayout, read_disk_layout
from .models import InstallPlan, OptionGroup, PackageChoice
from .network import IwdClient, NetworkError, WifiNetwork, is_online
from .profiles import PERSONAS, ProfileRepository
from .server import (
    SERVER_PERSONA,
    ServerConfigError,
    fetch_github_keys,
    parse_authorized_keys,
    validate_static_network,
)

_PAIR_HEADER = 1
_PAIR_CURSOR = 2
_PAIR_GOOD = 3
_PAIR_DANGER = 4
_PAIR_HINT = 5

_PERSONA_BLURBS = {
    "general": "Everyday desktop use with a browser and essentials.",
    "gamer": "Gaming-focused, includes Steam/Lutris and multilib support.",
    "developer": "Development tools, editors, and dotfiles.",
    "server": "Headless server managed over SSH, with a firewall and optional services.",
    "minimal": "Console-only system with the fewest packages; no desktop.",
}


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    detail: str = ""
    detail_pair: int = 0


@dataclass(slots=True)
class WizardResult:
    cancelled: bool = False
    plan: InstallPlan | None = None
    aur_declined: bool = False
    config: InstallConfig | None = None


def run_wizard(
    repository: ProfileRepository,
    args,
    preset_selections: dict[str, tuple[str, ...]],
) -> WizardResult:
    return curses.wrapper(_run_wizard, repository, args, preset_selections)


def _run_wizard(
    stdscr,
    repository: ProfileRepository,
    args,
    preset_selections: dict[str, tuple[str, ...]],
) -> WizardResult:
    curses.curs_set(0)
    stdscr.keypad(True)
    _init_colors()

    dry_run = getattr(args, "dry_run", False)
    keyboard = _select_keyboard(stdscr, apply=not dry_run)
    if keyboard is None:
        return WizardResult(cancelled=True)

    if not dry_run and not _ensure_network(stdscr):
        return WizardResult(cancelled=True)

    persona = args.persona
    if persona is None:
        persona = _select_persona(stdscr)
        if persona is None:
            return WizardResult(cancelled=True)

    selections = dict(preset_selections)
    for group in repository.groups_for(persona):
        if group.name in selections:
            continue
        choices = _select_group(stdscr, group)
        if choices is None:
            return WizardResult(cancelled=True)
        selections[group.name] = choices

    plan = repository.resolve(persona, selections)

    if plan.aur_packages and not args.allow_aur:
        if not _confirm_aur(stdscr, plan.aur_packages):
            return WizardResult(plan=plan, aur_declined=True)

    if not _confirm(
        stdscr,
        "Ready to configure the target disk",
        "Continue to disk selection and installation setup?",
        default=False,
    ):
        return WizardResult(plan=plan)

    runner = CommandRunner(quiet=True)
    disk = _select_disk(stdscr, runner, dry_run=getattr(args, "dry_run", False))
    if disk is None:
        return WizardResult(plan=plan)

    config = _collect_install_config(stdscr, disk, runner, plan, keyboard, dry_run=dry_run)
    if config is None:
        return WizardResult(plan=plan)

    return WizardResult(plan=plan, config=config)


def _init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_PAIR_HEADER, curses.COLOR_MAGENTA, -1)
    curses.init_pair(_PAIR_CURSOR, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(_PAIR_GOOD, curses.COLOR_GREEN, -1)
    curses.init_pair(_PAIR_DANGER, curses.COLOR_RED, -1)
    curses.init_pair(_PAIR_HINT, curses.COLOR_YELLOW, -1)


def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = win.getmaxyx()
    if y < 0 or y >= height or x < 0 or x >= width or not text:
        return
    try:
        win.addstr(y, x, text[: max(0, width - x)], attr)
    except curses.error:
        pass


def _draw_header(stdscr, subtitle: str) -> int:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    _safe_addstr(
        stdscr,
        0,
        max(0, (width - len(INSTALLER_BANNER)) // 2),
        INSTALLER_BANNER,
        curses.color_pair(_PAIR_HEADER) | curses.A_BOLD,
    )
    _safe_addstr(
        stdscr,
        1,
        max(0, (width - len(INSTALLER_TAGLINE)) // 2),
        INSTALLER_TAGLINE,
        curses.color_pair(_PAIR_HEADER),
    )
    if subtitle:
        _safe_addstr(stdscr, 3, 2, subtitle, curses.A_BOLD)
    _safe_addstr(stdscr, 4, 2, "─" * max(0, width - 4))
    return 6


def _confirm_quit(stdscr) -> bool:
    return _confirm(
        stdscr,
        "Quit installer?",
        "Exit without installing?\nNo disks have been modified.",
        default=False,
    )


def _confirm(stdscr, subtitle: str, message: str, *, danger: bool = False, default: bool = False) -> bool:
    lines = message.split("\n")
    pair = curses.color_pair(_PAIR_DANGER) | curses.A_BOLD if danger else curses.color_pair(_PAIR_GOOD)
    while True:
        first = _draw_header(stdscr, subtitle)
        for offset, line in enumerate(lines):
            _safe_addstr(stdscr, first + offset, 2, line, pair)
        hint = f"y = yes    n = no    (Enter = {'yes' if default else 'no'})"
        _safe_addstr(stdscr, first + len(lines) + 2, 2, hint, curses.A_BOLD)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("y"), ord("Y")):
            return True
        if key in (ord("n"), ord("N"), 27):
            return False
        if key in (curses.KEY_ENTER, 10, 13):
            return default


def _show_message(stdscr, text: str, *, subtitle: str = "Notice", danger: bool = False) -> None:
    pair = curses.color_pair(_PAIR_DANGER) | curses.A_BOLD if danger else curses.color_pair(_PAIR_HINT)
    first = _draw_header(stdscr, subtitle)
    lines = text.split("\n")
    for offset, line in enumerate(lines):
        _safe_addstr(stdscr, first + offset, 2, line, pair)
    _safe_addstr(stdscr, first + len(lines) + 2, 2, "Press any key to continue...", curses.A_DIM)
    stdscr.refresh()
    stdscr.getch()


def _run_list(
    stdscr,
    subtitle: str,
    rows: list[Row],
    *,
    multi: bool,
    checked: set[int] | None = None,
    cursor: int = 0,
    footer: str = "",
    extra_keys: dict[int, str] | None = None,
) -> tuple[str, object]:
    checked = set(checked or ())
    extra_keys = extra_keys or {}
    count = len(rows)
    cursor = max(0, min(cursor, count - 1)) if count else 0
    default_footer = (
        "↑/↓ move   Space toggle   Enter confirm   a all   n none   Esc quit"
        if multi
        else "↑/↓ move   Enter select   Esc quit"
    )

    while True:
        height, width = stdscr.getmaxyx()
        first = _draw_header(stdscr, subtitle)
        visible = max(1, height - first - 3)
        top = 0
        if count > visible:
            top = max(0, min(cursor - visible // 2, count - visible))

        for row_index in range(top, min(count, top + visible)):
            row = rows[row_index]
            y = first + (row_index - top)
            is_cursor = row_index == cursor
            prefix = ""
            if multi:
                prefix = "[x] " if row_index in checked else "[ ] "
            text = f"{prefix}{row.label}"
            if is_cursor:
                tail = f" {row.detail}" if row.detail else ""
                pad_len = max(0, width - 2 - len(text) - len(tail))
                line = f"{text}{tail}{' ' * pad_len}"
                _safe_addstr(stdscr, y, 2, line, curses.color_pair(_PAIR_CURSOR) | curses.A_BOLD)
            else:
                _safe_addstr(stdscr, y, 2, text)
                if row.detail:
                    _safe_addstr(stdscr, y, 2 + len(text) + 1, row.detail, curses.color_pair(row.detail_pair))

        if count > visible:
            _safe_addstr(stdscr, first + visible, 2, f"({cursor + 1}/{count})", curses.A_DIM)
        elif count == 0:
            _safe_addstr(stdscr, first, 2, "(nothing to select)", curses.color_pair(_PAIR_HINT))

        _safe_addstr(stdscr, height - 2, 2, footer or default_footer, curses.color_pair(_PAIR_HINT))
        stdscr.refresh()

        key = stdscr.getch()
        if count == 0:
            if key in (27, ord("q")) and _confirm_quit(stdscr):
                return "quit", None
            continue
        if key in (curses.KEY_UP, ord("k")):
            cursor = (cursor - 1) % count
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = (cursor + 1) % count
        elif multi and key == ord(" "):
            checked.symmetric_difference_update({cursor})
        elif multi and key == ord("a"):
            checked = set(range(count))
        elif multi and key == ord("n"):
            checked = set()
        elif key in (curses.KEY_ENTER, 10, 13):
            return "select", (checked if multi else cursor)
        elif key in extra_keys:
            return extra_keys[key], cursor
        elif key in (27, ord("q")):
            if _confirm_quit(stdscr):
                return "quit", None


def _show_status(stdscr, subtitle: str, text: str) -> None:
    first = _draw_header(stdscr, subtitle)
    _safe_addstr(stdscr, first, 2, text, curses.color_pair(_PAIR_HINT))
    stdscr.refresh()


def _ensure_network(stdscr, client: IwdClient | None = None, online_check=is_online) -> bool:
    """Block until the live session is online, offering Wi-Fi setup via iwd."""
    client = client or IwdClient()
    subtitle = "Internet connection — packages are downloaded during installation"
    _show_status(stdscr, subtitle, "Checking internet connection...")
    if online_check():
        return True

    networks: tuple[WifiNetwork, ...] = ()
    device: str | None = None
    error = ""
    while True:
        devices = client.devices()
        if device not in devices:
            device = devices[0] if devices else None
            networks = ()
        if device and not networks:
            _show_status(stdscr, subtitle, f"Scanning for Wi-Fi networks on {device}...")
            try:
                networks = client.scan(device)
            except NetworkError as scan_error:
                error = str(scan_error)

        rows = [
            Row(
                label=network.ssid,
                detail=f"{network.signal} dBm, {network.security}"
                + (", connected" if network.connected else ""),
                detail_pair=_PAIR_HINT,
            )
            for network in networks
        ]
        rescan_index = len(rows)
        rows.append(Row(label="[ Scan again / check connection ]"))
        if device is None:
            hint = "No Wi-Fi adapter found. Plug in Ethernet or USB tethering, then check again."
        else:
            hint = f"Not online. Pick a Wi-Fi network ({device}), or plug in Ethernet."
        footer = f"{error or hint}   Enter select   Esc quit"
        action, payload = _run_list(stdscr, subtitle, rows, multi=False, footer=footer)
        if action == "quit":
            return False
        error = ""
        if payload == rescan_index:
            _show_status(stdscr, subtitle, "Checking internet connection...")
            if online_check():
                return True
            networks = ()
            continue

        network = networks[payload]
        passphrase = None
        if network.security == "psk" and not network.known:
            passphrase = _text_input(
                stdscr,
                f"Wi-Fi passphrase for {network.ssid}",
                "Passphrase:",
                "",
                lambda value: (8 <= len(value) <= 63, "Passphrases are 8 to 63 characters."),
                secret=True,
            )
            if passphrase is None:
                return False
        _show_status(stdscr, subtitle, f"Connecting to {network.ssid}...")
        try:
            assert device is not None
            client.connect(device, network.ssid, passphrase)
        except NetworkError as connect_error:
            error = str(connect_error)
            continue
        _show_status(stdscr, subtitle, "Connected. Waiting for an internet connection...")
        for _ in range(10):
            if online_check():
                return True
            time.sleep(1)
        error = f"Joined {network.ssid} but the internet is still unreachable."
        networks = ()


def _select_persona(stdscr) -> str | None:
    rows = [
        Row(label=persona.title(), detail=_PERSONA_BLURBS.get(persona, ""), detail_pair=_PAIR_HINT)
        for persona in PERSONAS
    ]
    action, payload = _run_list(stdscr, "Choose how you plan to use protogenOS", rows, multi=False)
    if action == "quit":
        return None
    return PERSONAS[payload]


def _choice_row(choice: PackageChoice) -> Row:
    source = "" if choice.source == "official" else f", {choice.source.upper()}"
    marker = " [default]" if choice.default else ""
    return Row(label=f"{choice.label} ({choice.package}{source}){marker}")


def _select_group(stdscr, group: OptionGroup) -> tuple[str, ...] | None:
    label = group.name.replace("-", " ").title()

    if group.selection == "any-of":
        rows = [_choice_row(choice) for choice in group.choices]
        defaults = {index for index, choice in enumerate(group.choices) if choice.default}
        action, checked = _run_list(stdscr, f"{label} — choose any", rows, multi=True, checked=defaults)
        if action == "quit":
            return None
        return tuple(group.choices[index].identifier for index in sorted(checked))

    if group.selection == "optional":
        rows = [Row(label="(none)")] + [_choice_row(choice) for choice in group.choices]
        default_index = next((index + 1 for index, choice in enumerate(group.choices) if choice.default), 0)
        action, index = _run_list(stdscr, f"{label} — optional", rows, multi=False, cursor=default_index)
        if action == "quit":
            return None
        return () if index == 0 else (group.choices[index - 1].identifier,)

    rows = [_choice_row(choice) for choice in group.choices]
    default_index = next((index for index, choice in enumerate(group.choices) if choice.default), 0)
    action, index = _run_list(stdscr, f"{label} — choose one", rows, multi=False, cursor=default_index)
    if action == "quit":
        return None
    return (group.choices[index].identifier,)


def _confirm_aur(stdscr, packages: tuple[str, ...]) -> bool:
    message = "Build these AUR packages as the target user?\n" + "\n".join(
        f"  - {package}" for package in packages
    )
    return _confirm(stdscr, "AUR packages required", message, default=False)


def _describe_disk(disk: DiskInfo) -> str:
    removable = ", removable" if disk.removable else ""
    return f"{disk.path} — {disk.model}, {format_size(disk.size)}{removable}"


def _select_disk(stdscr, runner: CommandRunner, *, dry_run: bool = False) -> DiskInfo | None:
    if not dry_run and os.geteuid() != 0:
        raise InstallError("disk installation must run as root")

    while True:
        disks = list_install_disks(runner)
        if not disks:
            raise InstallError("no unused writable disks were found")

        rows = [
            Row(
                label=_describe_disk(disk),
                detail=("has existing partitions" if disk.partitioned else "empty, no partitions"),
                detail_pair=(_PAIR_HINT if disk.partitioned else _PAIR_GOOD),
            )
            for disk in disks
        ]
        action, payload = _run_list(
            stdscr,
            "Select the target disk",
            rows,
            multi=False,
            footer="↑/↓ move   Enter select   c cfdisk   Esc quit",
            extra_keys={ord("c"): "cfdisk"},
        )
        if action == "quit":
            return None
        if action == "cfdisk":
            target = disks[payload]
            if shutil.which("cfdisk") is None:
                _show_message(stdscr, "cfdisk is not available on this system.", danger=True)
                continue
            curses.def_prog_mode()
            curses.endwin()
            runner.run(["cfdisk", target.path], check=False)
            stdscr.clear()
            curses.reset_prog_mode()
            curses.curs_set(0)
            continue

        return disks[payload]


def _text_input(
    stdscr,
    subtitle: str,
    prompt: str,
    default: str,
    validate,
    *,
    secret: bool = False,
) -> str | None:
    buffer = list(default) if not secret else []
    error = ""
    while True:
        first = _draw_header(stdscr, subtitle)
        height, width = stdscr.getmaxyx()
        _safe_addstr(stdscr, first, 2, prompt)
        shown = ("*" * len(buffer)) if secret else "".join(buffer)
        field_line = (shown or " ") + " " * 2
        _safe_addstr(stdscr, first + 2, 2, field_line, curses.color_pair(_PAIR_CURSOR) | curses.A_BOLD)
        if error:
            _safe_addstr(stdscr, first + 4, 2, error, curses.color_pair(_PAIR_DANGER) | curses.A_BOLD)
        _safe_addstr(stdscr, height - 2, 2, "Enter confirm   Backspace edit   Esc quit", curses.color_pair(_PAIR_HINT))
        curses.curs_set(1)
        stdscr.move(first + 2, min(width - 1, 2 + len(shown)))
        stdscr.refresh()

        key = stdscr.getch()
        if key in (curses.KEY_ENTER, 10, 13):
            value = "".join(buffer)
            ok, message = validate(value)
            if ok:
                curses.curs_set(0)
                return value
            error = message
        elif key == 27:
            if _confirm_quit(stdscr):
                curses.curs_set(0)
                return None
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if buffer:
                buffer.pop()
        elif 32 <= key <= 126:
            buffer.append(chr(key))


def _select_timezone(stdscr, zones: tuple[str, ...], default: str = "UTC") -> str | None:
    query = ""
    cursor = zones.index(default) if default in zones else 0

    while True:
        matches = search_timezones(zones, query) if query else zones
        count = len(matches)
        cursor = max(0, min(cursor, count - 1)) if count else 0

        height, width = stdscr.getmaxyx()
        first = _draw_header(stdscr, "Timezone — type to search (e.g. AST, atlantic, tokyo)")
        _safe_addstr(
            stdscr, first, 2, f"Search: {query}", curses.color_pair(_PAIR_CURSOR) | curses.A_BOLD
        )
        list_top = first + 2
        visible = max(1, height - list_top - 3)
        top = max(0, min(cursor - visible // 2, max(0, count - visible)))

        for row_index in range(top, min(count, top + visible)):
            y = list_top + (row_index - top)
            text = matches[row_index]
            if row_index == cursor:
                pad = max(0, width - 2 - len(text))
                _safe_addstr(stdscr, y, 2, text + " " * pad, curses.color_pair(_PAIR_CURSOR) | curses.A_BOLD)
            else:
                _safe_addstr(stdscr, y, 2, text)

        if count == 0:
            _safe_addstr(stdscr, list_top, 2, "(no matches)", curses.color_pair(_PAIR_DANGER) | curses.A_BOLD)
        elif count > visible:
            _safe_addstr(stdscr, list_top + visible, 2, f"({cursor + 1}/{count})", curses.A_DIM)

        _safe_addstr(
            stdscr,
            height - 2,
            2,
            "Type to search   ↑/↓ move   Enter select   Esc quit",
            curses.color_pair(_PAIR_HINT),
        )
        curses.curs_set(1)
        stdscr.move(first, min(width - 1, 2 + len("Search: ") + len(query)))
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_UP:
            cursor -= 1
        elif key == curses.KEY_DOWN:
            cursor += 1
        elif key in (curses.KEY_ENTER, 10, 13):
            if count:
                curses.curs_set(0)
                return matches[cursor]
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if query:
                query = query[:-1]
                cursor = 0
        elif key == 27:
            if _confirm_quit(stdscr):
                curses.curs_set(0)
                return None
        elif 32 <= key <= 126:
            query += chr(key)
            cursor = 0


def _collect_password(stdscr, username: str) -> str | None:
    def _non_empty(value: str) -> tuple[bool, str]:
        return (bool(value), "Password cannot be empty.")

    while True:
        password = _text_input(stdscr, "Set a password", f"Password for {username}:", "", _non_empty, secret=True)
        if password is None:
            return None
        confirmation = _text_input(stdscr, "Confirm password", "Re-enter password:", "", _non_empty, secret=True)
        if confirmation is None:
            return None
        if confirmation != password:
            _show_message(stdscr, "Passwords did not match.", danger=True)
            continue
        return password


def _select_keyboard(stdscr, *, apply: bool) -> tuple[str, str] | None:
    layouts = list_layouts()
    rows = [Row(label=layout.description, detail=layout.code, detail_pair=_PAIR_HINT) for layout in layouts]
    default = next((index for index, layout in enumerate(layouts) if layout.code == "us"), 0)
    action, index = _run_list(stdscr, "Keyboard layout", rows, multi=False, cursor=default)
    if action == "quit":
        return None
    layout = layouts[index]
    variant = ""
    if layout.variants:
        variant_rows = [Row(label="Default")] + [Row(label=item.description) for item in layout.variants]
        action, choice = _run_list(stdscr, f"{layout.description} — variant", variant_rows, multi=False)
        if action == "quit":
            return None
        variant = "" if choice == 0 else layout.variants[choice - 1].code
    if apply:
        # Apply right away so passphrases typed later match the boot prompt.
        CommandRunner(quiet=True).run(["loadkeys", console_keymap(layout.code, variant)], capture_output=True, check=False)
    return layout.code, variant


def _describe_partition(part) -> str:
    details = ", ".join(item for item in (part.fstype or "unformatted", part.label) if item)
    return f"{part.path} — {format_size(part.size)} ({details})"


def _select_disk_layout(
    stdscr, disk: DiskInfo, layout: DiskLayout, firmware: str
) -> tuple[str, str | None, str | None, bool] | None:
    """Return (layout, root partition, boot partition, format boot)."""
    uefi = firmware == "uefi"
    largest = layout.largest_free.size if layout.largest_free else 0
    free_ok = uefi and layout.table == "gpt" and largest >= MIN_ROOT_BYTES + GIB
    candidates_ok = uefi and len(layout.partitions) >= 2
    options = [("erase", Row(label="Erase the entire disk", detail="deletes all data", detail_pair=_PAIR_DANGER))]
    if free_ok:
        options.append(
            ("free-space", Row(label="Install alongside other systems", detail=f"{format_size(largest)} free", detail_pair=_PAIR_GOOD))
        )
    if candidates_ok:
        options.append(("partitions", Row(label="Use existing partitions", detail="pick root and EFI partitions", detail_pair=_PAIR_HINT)))
    action, index = _run_list(stdscr, f"Installation type for {disk.path}", [row for _, row in options], multi=False)
    if action == "quit":
        return None
    kind = options[index][0]
    if kind == "erase":
        if not _confirm(
            stdscr,
            "Confirm disk selection",
            f"!!! WARNING !!!\nEverything on {disk.path} will be PERMANENTLY ERASED.",
            danger=True,
            default=False,
        ):
            return None
        return kind, None, None, False
    if kind == "free-space":
        return kind, None, None, False

    roots = [part for part in layout.partitions if part.size >= MIN_ROOT_BYTES and not part.mountpoints]
    if not roots:
        _show_message(stdscr, "No unmounted partition of at least 16 GiB was found.", danger=True)
        return None
    action, index = _run_list(stdscr, "Root partition (will be FORMATTED)", [Row(label=_describe_partition(part)) for part in roots], multi=False)
    if action == "quit":
        return None
    root = roots[index].path
    boots = [part for part in layout.partitions if part.path != root and not part.mountpoints]
    action, index = _run_list(stdscr, "EFI system partition (mounted at /boot)", [Row(label=_describe_partition(part)) for part in boots], multi=False)
    if action == "quit":
        return None
    boot = boots[index]
    format_boot = boot.fstype != "vfat" or _confirm(
        stdscr,
        "EFI system partition",
        f"Format {boot.path}?\nThis removes other systems' bootloaders stored on it.",
        danger=True,
        default=False,
    )
    return kind, root, boot.path, format_boot


def _collect_storage(
    stdscr, firmware: str, disk_layout: str
) -> tuple[str, bool, bool, str | None, str, str] | None:
    """Return (filesystem, btrfs_subvolumes, encrypt, passphrase, swap, bootloader)."""
    filesystems = [
        ("btrfs", "subvolumes + zstd compression [recommended]"),
        ("ext4", "classic and battle-tested"),
        ("xfs", "fast for large files"),
        ("f2fs", "designed for flash storage"),
    ]
    action, index = _run_list(
        stdscr,
        "Root filesystem",
        [Row(label=name, detail=detail, detail_pair=_PAIR_HINT) for name, detail in filesystems],
        multi=False,
    )
    if action == "quit":
        return None
    filesystem = filesystems[index][0]
    btrfs_subvolumes = filesystem != "btrfs" or _confirm(
        stdscr,
        "Btrfs subvolumes",
        "Create @, @home, @log, @pkg and @snapshots subvolumes?\n"
        "Recommended: needed for system snapshots and rollback.",
        default=True,
    )

    passphrase = None
    encrypt = _confirm(
        stdscr,
        "Disk encryption",
        "Encrypt the system with LUKS2?\nA passphrase will be required at every boot.",
        default=False,
    )
    if encrypt:
        def _valid(value: str) -> tuple[bool, str]:
            ok = len(value) >= 8 and all(32 <= ord(character) <= 126 for character in value)
            return ok, "Use at least 8 plain ASCII characters."

        while True:
            passphrase = _text_input(stdscr, "Encryption passphrase", "Passphrase:", "", _valid, secret=True)
            if passphrase is None:
                return None
            again = _text_input(stdscr, "Encryption passphrase", "Re-enter passphrase:", "", _valid, secret=True)
            if again is None:
                return None
            if again == passphrase:
                break
            _show_message(stdscr, "Passphrases did not match.", danger=True)

    swap = "zram" if _confirm(stdscr, "Swap", "Enable compressed swap in RAM (zram)?", default=True) else "none"

    bootloader = "grub"
    if firmware == "uefi":
        loaders = [
            ("grub", "detects other systems for dual boot" if disk_layout != "erase" else "works everywhere"),
            ("systemd-boot", "minimal and fast"),
            ("limine", "modern and lightweight"),
        ]
        action, index = _run_list(
            stdscr,
            "Bootloader",
            [Row(label=name, detail=detail, detail_pair=_PAIR_HINT) for name, detail in loaders],
            multi=False,
        )
        if action == "quit":
            return None
        bootloader = loaders[index][0]
    return filesystem, btrfs_subvolumes, encrypt, passphrase, swap, bootloader


def _collect_additional_users(stdscr, taken: set[str]) -> tuple[UserAccount, ...] | None:
    users: list[UserAccount] = []
    while _confirm(stdscr, "Additional users", "Create another user account?", default=False):
        name = _text_input(
            stdscr,
            "Additional user",
            "User name:",
            "",
            lambda value: (
                bool(USERNAME_PATTERN.fullmatch(value)) and value not in taken,
                "Use a new lowercase name (letters, numbers, _ or -).",
            ),
        )
        if name is None:
            return None
        password = _collect_password(stdscr, name)
        if password is None:
            return None
        sudo = _confirm(stdscr, "Additional user", f"Grant {name} sudo access?", default=False)
        users.append(UserAccount(name, password, sudo))
        taken.add(name)
    return tuple(users)


def _collect_install_config(
    stdscr,
    disk: DiskInfo,
    runner: CommandRunner,
    plan: InstallPlan,
    keyboard: tuple[str, str] = ("us", ""),
    *,
    dry_run: bool = False,
) -> InstallConfig | None:
    firmware = detect_firmware()
    # Reading the partition table is read-only, so dry runs show real options.
    layout = read_disk_layout(runner, disk.path)
    layout_choice = _select_disk_layout(stdscr, disk, layout, firmware)
    if layout_choice is None:
        return None
    disk_layout, root_partition, boot_partition, format_boot = layout_choice
    storage = _collect_storage(stdscr, firmware, disk_layout)
    if storage is None:
        return None
    filesystem, btrfs_subvolumes, encrypt, passphrase, swap, bootloader = storage
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
    action, checked = _run_list(
        stdscr,
        "Extras — choose any",
        [Row(label=offer.label, detail=offer.detail, detail_pair=_PAIR_HINT) for offer in offers],
        multi=True,
        checked={index for index, offer in enumerate(offers) if offer.default},
    )
    if action == "quit":
        return None
    features = feature_settings(offers[index].key for index in checked)

    hostname = _text_input(
        stdscr,
        f"System hostname (boot mode: {firmware.upper()})",
        "Hostname:",
        "protogenos",
        lambda value: (
            bool(HOSTNAME_PATTERN.fullmatch(value)),
            "Use only letters, numbers, and internal hyphens.",
        ),
    )
    if hostname is None:
        return None

    username = _text_input(
        stdscr,
        "Administrator account",
        "User name:",
        "proto",
        lambda value: (
            bool(USERNAME_PATTERN.fullmatch(value)) and value != "root",
            "Start with a lowercase letter/underscore; lowercase letters, numbers, _ or -. Not 'root'.",
        ),
    )
    if username is None:
        return None

    password = _collect_password(stdscr, username)
    if password is None:
        return None

    grant_sudo = _confirm(
        stdscr,
        "Administrator access",
        f"Grant {username} sudo (administrator) access?",
        default=True,
    )
    root_password = None
    if not grant_sudo:
        _show_message(
            stdscr,
            "Root will stay unlocked with its own password since this user won't have sudo.",
        )
        root_password = _collect_password(stdscr, "root")
        if root_password is None:
            return None

    additional_users = _collect_additional_users(stdscr, {username, "root"})
    if additional_users is None:
        return None

    timezone = _select_timezone(stdscr, list_timezones())
    if timezone is None:
        return None

    locale = _text_input(
        stdscr,
        "System locale",
        "Locale:",
        suggest_locale(timezone, keyboard[0]),
        lambda value: (
            bool(LOCALE_PATTERN.fullmatch(value)),
            "Use a UTF-8 locale such as en_US.UTF-8.",
        ),
    )
    if locale is None:
        return None

    mirror_country = _text_input(
        stdscr,
        "Package mirrors",
        "Country for mirrors (e.g. Germany; leave empty for automatic):",
        "",
        lambda value: (not value or bool(COUNTRY_PATTERN.fullmatch(value)), "Use a country name such as Germany."),
    )
    if mirror_country is None:
        return None

    kernel_headers = _confirm(
        stdscr,
        "Kernel headers",
        "Install kernel headers?\nNeeded for DKMS modules such as VirtualBox or NVIDIA drivers.",
        default=False,
    )

    server: dict[str, object] = {}
    if plan.persona == SERVER_PERSONA:
        collected = _collect_server_settings(stdscr, username)
        if collected is None:
            return None
        server = collected

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
        keyboard_layout=keyboard[0],
        keyboard_variant=keyboard[1],
        mirror_country=mirror_country,
        kernel_headers=kernel_headers,
        additional_users=additional_users,
        **features,
        **server,
    )
    try:
        config.validate()
        config.validate_for_persona(plan.persona)
    except InstallError as error:
        _show_message(stdscr, str(error), danger=True)
        return _collect_install_config(stdscr, disk, runner, plan, keyboard, dry_run=dry_run)
    return config


def _collect_ssh_keys(stdscr, username: str) -> tuple[str, ...] | None:
    keys: list[str] = []
    sources = [
        Row(label="Paste a public key", detail="ssh-ed25519 AAAA... comment", detail_pair=_PAIR_HINT),
        Row(label="Import from GitHub", detail="all keys of a GitHub account", detail_pair=_PAIR_HINT),
    ]
    while True:
        action, index = _run_list(
            stdscr, f"SSH key for {username} (password logins are disabled)", sources, multi=False
        )
        if action == "quit":
            return None
        try:
            if index == 1:
                name = _text_input(
                    stdscr, "Import SSH keys", "GitHub username:", "", lambda value: (bool(value), "Enter a username.")
                )
                if name is None:
                    return None
                _show_status(stdscr, "Import SSH keys", f"Fetching keys for {name}...")
                found = fetch_github_keys(name)
            else:
                line = _text_input(
                    stdscr, "Paste SSH key", "Public key:", "", lambda value: (bool(value.strip()), "Paste a public key.")
                )
                if line is None:
                    return None
                found = parse_authorized_keys(line)
        except ServerConfigError as error:
            _show_message(stdscr, str(error), danger=True)
            continue
        keys.extend(key for key in found if key not in keys)
        if not _confirm(stdscr, "SSH keys", f"{len(keys)} key(s) added.\nAdd another key?", default=False):
            return tuple(keys)


def _collect_static_network(stdscr) -> dict[str, object] | None:
    if not _confirm(stdscr, "Network", "Use a static IP address instead of DHCP?", default=False):
        return {}

    def _optional(value: str) -> tuple[bool, str]:
        return True, ""

    prompts = (
        ("Address with prefix (e.g. 192.168.1.10/24):", lambda value: (bool(value), "Enter an address such as 192.168.1.10/24.")),
        ("Gateway (empty for none):", _optional),
        ("DNS servers, separated by spaces (empty for none):", _optional),
        ("Network interface (empty for any wired interface):", _optional),
    )
    while True:
        answers: list[str] = []
        for prompt, validate in prompts:
            answer = _text_input(stdscr, "Static address", prompt, "", validate)
            if answer is None:
                return None
            answers.append(answer.strip())
        address, gateway, dns, interface = answers
        servers = tuple(dns.split())
        try:
            validate_static_network(address, gateway, servers, interface)
        except ServerConfigError as error:
            _show_message(stdscr, str(error), danger=True)
            continue
        return {
            "static_address": address,
            "static_gateway": gateway,
            "static_dns": servers,
            "static_interface": interface,
        }


def _collect_server_settings(stdscr, username: str) -> dict[str, object] | None:
    keys = _collect_ssh_keys(stdscr, username)
    if keys is None:
        return None
    network = _collect_static_network(stdscr)
    if network is None:
        return None
    return {"ssh_authorized_keys": keys, **network}
