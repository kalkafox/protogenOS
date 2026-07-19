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
from dataclasses import dataclass
from pathlib import Path

from .backend import (
    HOSTNAME_PATTERN,
    LOCALE_PATTERN,
    USERNAME_PATTERN,
    CommandRunner,
    DiskInfo,
    InstallConfig,
    InstallError,
    format_size,
    list_install_disks,
)
from .branding import INSTALLER_BANNER, INSTALLER_TAGLINE
from .models import InstallPlan, OptionGroup, PackageChoice
from .profiles import PERSONAS, ProfileRepository

_PAIR_HEADER = 1
_PAIR_CURSOR = 2
_PAIR_GOOD = 3
_PAIR_DANGER = 4
_PAIR_HINT = 5

_PERSONA_BLURBS = {
    "general": "Everyday desktop use with a browser and essentials.",
    "gamer": "Gaming-focused, includes Steam/Lutris and multilib support.",
    "developer": "Development tools, editors, and dotfiles.",
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

    runner = CommandRunner()
    disk = _select_disk(stdscr, runner)
    if disk is None:
        return WizardResult(plan=plan)

    config = _collect_install_config(stdscr, disk)
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


def _select_disk(stdscr, runner: CommandRunner) -> DiskInfo | None:
    if os.geteuid() != 0:
        raise InstallError("disk installation must run as root")

    while True:
        disks = list_install_disks(runner)
        if not disks:
            raise InstallError("no unused writable disks were found")

        rows = [
            Row(
                label=_describe_disk(disk),
                detail=("has existing partitions — will be erased" if disk.partitioned else "empty, no partitions — safe to use"),
                detail_pair=(_PAIR_DANGER if disk.partitioned else _PAIR_GOOD),
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

        disk = disks[payload]
        if disk.partitioned:
            confirmed = _confirm(
                stdscr,
                "Confirm disk selection",
                f"!!! WARNING !!!\n{disk.path} contains existing partitions and data.\n"
                "Continuing WILL PERMANENTLY ERASE everything on this disk.",
                danger=True,
                default=False,
            )
        else:
            confirmed = _confirm(
                stdscr,
                "Confirm disk selection",
                f"{disk.path} appears empty — no partitions detected.\n"
                "It will still be formatted before install.",
                danger=False,
                default=True,
            )
        if confirmed:
            return disk


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


def _collect_install_config(stdscr, disk: DiskInfo) -> InstallConfig | None:
    firmware = "uefi" if Path("/sys/firmware/efi").is_dir() else "bios"

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

    locale = _text_input(
        stdscr,
        "System locale",
        "Locale:",
        "en_US.UTF-8",
        lambda value: (
            bool(LOCALE_PATTERN.fullmatch(value)),
            "Use a UTF-8 locale such as en_US.UTF-8.",
        ),
    )
    if locale is None:
        return None

    timezone = _text_input(stdscr, "Timezone", "Timezone:", "UTC", lambda value: (True, ""))
    if timezone is None:
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
    try:
        config.validate()
    except InstallError as error:
        _show_message(stdscr, str(error), danger=True)
        return _collect_install_config(stdscr, disk)
    return config
