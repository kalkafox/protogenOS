"""Installation backend for protogenOS: validates choices and drives the install."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import bootloader as boot
from .config_io import export_config
from .hardware import VENDOR_NVIDIA, HardwareProfile, detect_hardware, read_efi_flag
from .keyboard import (
    LAYOUT_PATTERN,
    VARIANT_PATTERN,
    console_keymap,
    plasma_kxkbrc,
    vconsole_conf,
    x11_keyboard_conf,
)
from .mirrors import COUNTRY_PATTERN, enable_parallel_downloads, reflector_command
from .models import InstallPlan
from .network import (
    IWD_STORAGE,
    is_online,
    iwd_file_name,
    networkmanager_keyfile,
    read_iwd_credentials,
)
from .server import (
    FAIL2BAN_JAIL,
    GRUB_SERIAL_COMMAND,
    SERVER_ONLY_SETTINGS,
    SERVER_PERSONA,
    SSH_KEY_PATTERN,
    SSHD_CONFIG,
    UPDATE_DOWNLOAD_SERVICE,
    UPDATE_DOWNLOAD_TIMER,
    ServerConfigError,
    firewall_services,
    static_connection_keyfile,
    validate_static_network,
)
from .storage import (
    FILESYSTEM_PACKAGES,
    FILESYSTEM_TOOLS,
    GIB,
    MIN_ROOT_BYTES,
    PreparedStorage,
    StorageError,
    StorageManager,
    partition_path,
)


HOSTNAME_PATTERN = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}_[A-Za-z]{2,3}\.UTF-8$")
INSTALL_LOG = Path("/var/log/protogenos-install.log")
# Distinct from pacman/makepkg/mkinitcpio's own "==>" output.
STEP_PREFIX = "[protogenos] step "
WARNING_PREFIX = "[protogenos] warning: "
# pacman prints nothing useful while downloading into a pipe, so report the
# package cache size this often to tell a slow download from a stuck one.
HEARTBEAT_INTERVAL = 15.0
# Built from source: prebuilt -bin helpers link a specific libalpm soname and
# break whenever pacman bumps it.
AUR_HELPER = "yay"
AUR_HELPER_INSTALL = (
    "yay -S --noconfirm --needed --answerdiff None --answerclean None --removemake"
)

DISK_LAYOUTS = ("erase", "free-space", "partitions")
FILESYSTEMS = ("btrfs", "ext4", "xfs", "f2fs")
BOOTLOADERS = ("grub", "systemd-boot", "limine")
SWAP_MODES = ("zram", "none")
NVIDIA_DRIVERS = ("nouveau", "nvidia-open")
SECURE_BOOT_LOADERS = ("systemd-boot", "limine")
SNAPPER_ROOT_CONFIG = """SUBVOLUME="/"
FSTYPE="btrfs"
QGROUP=""
SPACE_LIMIT="0.5"
FREE_LIMIT="0.2"
ALLOW_USERS=""
ALLOW_GROUPS="wheel"
SYNC_ACL="yes"
BACKGROUND_COMPARISON="yes"
NUMBER_CREATE="yes"
NUMBER_CLEANUP="yes"
NUMBER_MIN_AGE="3600"
NUMBER_LIMIT="50"
NUMBER_LIMIT_IMPORTANT="10"
TIMELINE_CREATE="yes"
TIMELINE_CLEANUP="yes"
TIMELINE_MIN_AGE="3600"
TIMELINE_LIMIT_HOURLY="5"
TIMELINE_LIMIT_DAILY="7"
TIMELINE_LIMIT_WEEKLY="0"
TIMELINE_LIMIT_MONTHLY="0"
TIMELINE_LIMIT_QUARTERLY="0"
TIMELINE_LIMIT_YEARLY="0"
EMPTY_PRE_POST_CLEANUP="yes"
EMPTY_PRE_POST_MIN_AGE="3600"
"""
# Snapshots are read-only; a volatile overlay lets a snapshot boot to a desktop.
GRUB_BTRFS_CONFIG = 'GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS="systemd.volatile=overlay"\n'
# Proton games stall on split-lock detection's deliberate slowdown (SteamOS
# default). vm.max_map_count is already raised by Arch's filesystem package.
GAMING_SYSCTL_CONF = "kernel.split_lock_mitigate = 0\n"
# Large games overflow the default shader caches and recompile every launch.
GAMING_ENVIRONMENT_CONF = """# protogenOS gaming: keep up to 12 GB of compiled shaders.
MESA_SHADER_CACHE_MAX_SIZE=12G
__GL_SHADER_DISK_CACHE_SIZE=12000000000
"""
# /dev/ntsync lets Wine and Proton builds that support it emulate Windows
# synchronization primitives in the kernel.
NTSYNC_MODULES_CONF = "ntsync\n"
# Steam launch option: game-performance %command%
GAME_PERFORMANCE_SCRIPT = """#!/bin/sh
# Run a game with the performance power profile, restoring the previous
# profile when it exits. Based on CachyOS's game-performance.
if ! command -v powerprofilesctl >/dev/null 2>&1 || ! powerprofilesctl list | grep -q 'performance:'; then
    exec "$@"
fi
if [ -n "$GAME_PERFORMANCE_SCREENSAVER_ON" ]; then
    exec powerprofilesctl launch -p performance -r "game-performance" -- "$@"
fi
exec systemd-inhibit --why "game-performance is running" \\
    powerprofilesctl launch -p performance -r "game-performance" -- "$@"
"""
FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"
ZRAM_GENERATOR_CONF = """[zram0]
zram-size = min(ram / 2, 8192)
compression-algorithm = zstd
"""
# Arch Wiki recommendations for swap on zram.
ZRAM_SYSCTL_CONF = """vm.swappiness = 180
vm.watermark_boost_factor = 0
vm.watermark_scale_factor = 125
vm.page-cluster = 0
"""


class InstallError(RuntimeError):
    """Raised when installation cannot safely continue."""


@dataclass(frozen=True, slots=True)
class DiskInfo:
    path: str
    size: int
    model: str
    removable: bool
    partitioned: bool


@dataclass(frozen=True, slots=True)
class UserAccount:
    username: str
    password: str = field(repr=False)
    sudo: bool = False


def _is_console_typable(value: str) -> bool:
    return all(32 <= ord(character) <= 126 for character in value)


@dataclass(frozen=True, slots=True)
class InstallConfig:
    disk: str
    firmware: str
    hostname: str
    username: str
    user_password: str = field(repr=False)
    timezone: str = "UTC"
    locale: str = "en_US.UTF-8"
    grant_sudo: bool = True
    root_password: str | None = field(default=None, repr=False)
    filesystem: str = "btrfs"
    # Btrfs only: False formats a single flat volume without @ subvolumes.
    btrfs_subvolumes: bool = True
    disk_partitioned: bool = True
    disk_layout: str = "erase"
    root_partition: str | None = None
    boot_partition: str | None = None
    format_boot: bool = False
    encrypt: bool = False
    encryption_passphrase: str | None = field(default=None, repr=False)
    bootloader: str = "grub"
    swap: str = "zram"
    keyboard_layout: str = "us"
    keyboard_variant: str = ""
    mirror_country: str = ""
    kernel_headers: bool = False
    additional_users: tuple[UserAccount, ...] = ()
    # Optional features; front ends pick defaults from detected hardware.
    snapshots: bool = False
    flatpak: bool = False
    gaming_tweaks: bool = False
    nvidia_driver: str = "nouveau"
    fingerprint: bool = False
    tpm2_unlock: bool = False
    secure_boot: bool = False
    # Server persona only: key-only SSH, optional services, static networking.
    ssh_authorized_keys: tuple[str, ...] = ()
    cockpit: bool = False
    netdata: bool = False
    fail2ban: bool = False
    update_downloads: bool = False
    serial_console: bool = False
    static_address: str = ""
    static_gateway: str = ""
    static_dns: tuple[str, ...] = ()
    static_interface: str = ""

    def __post_init__(self) -> None:
        # JSON callers (web API, config files) pass users as plain objects.
        users = tuple(
            user if isinstance(user, UserAccount) else UserAccount(**user)
            for user in (self.additional_users or ())
        )
        object.__setattr__(self, "additional_users", users)
        # ...and lists where the dataclass expects tuples.
        for name in ("ssh_authorized_keys", "static_dns"):
            value = getattr(self, name)
            if isinstance(value, list):
                object.__setattr__(self, name, tuple(value))

    def validate(self, zoneinfo_root: Path = Path("/usr/share/zoneinfo")) -> None:
        if not self.disk.startswith("/dev/") or not Path(self.disk).name:
            raise InstallError(f"invalid target disk: {self.disk!r}")
        if self.firmware not in {"uefi", "bios"}:
            raise InstallError("firmware must be 'uefi' or 'bios'")
        if self.filesystem not in FILESYSTEMS:
            raise InstallError(f"filesystem must be one of: {', '.join(FILESYSTEMS)}")
        for name in (
            "btrfs_subvolumes", "snapshots", "flatpak", "gaming_tweaks", "fingerprint", "tpm2_unlock",
            "secure_boot", "cockpit", "netdata", "fail2ban", "update_downloads", "serial_console",
        ):
            if not isinstance(getattr(self, name), bool):
                raise InstallError(f"{name} must be true or false")
        if not HOSTNAME_PATTERN.fullmatch(self.hostname):
            raise InstallError("hostname must contain only letters, numbers, and hyphens")
        if not USERNAME_PATTERN.fullmatch(self.username):
            raise InstallError("username must start with a lowercase letter or underscore")
        if self.username == "root":
            raise InstallError("root is reserved; choose a separate administrator name")
        if not self.user_password:
            raise InstallError("user password cannot be empty")
        if not self.grant_sudo and not self.root_password:
            raise InstallError("root password cannot be empty when sudo access is declined")
        if not LOCALE_PATTERN.fullmatch(self.locale):
            raise InstallError("locale must look like en_US.UTF-8")
        timezone_path = (zoneinfo_root / self.timezone).resolve()
        try:
            timezone_path.relative_to(zoneinfo_root.resolve())
        except ValueError as error:
            raise InstallError("timezone escapes the zoneinfo directory") from error
        if not timezone_path.is_file():
            raise InstallError(f"unknown timezone: {self.timezone}")
        self._validate_storage()
        if self.bootloader not in BOOTLOADERS:
            raise InstallError(f"bootloader must be one of: {', '.join(BOOTLOADERS)}")
        if self.bootloader != "grub" and self.firmware != "uefi":
            raise InstallError(f"{self.bootloader} requires UEFI; use GRUB for BIOS systems")
        self._validate_features()
        if self.swap not in SWAP_MODES:
            raise InstallError(f"swap must be one of: {', '.join(SWAP_MODES)}")
        if not LAYOUT_PATTERN.fullmatch(self.keyboard_layout):
            raise InstallError(f"invalid keyboard layout: {self.keyboard_layout!r}")
        if not VARIANT_PATTERN.fullmatch(self.keyboard_variant):
            raise InstallError(f"invalid keyboard variant: {self.keyboard_variant!r}")
        if self.mirror_country and not COUNTRY_PATTERN.fullmatch(self.mirror_country):
            raise InstallError(f"invalid mirror country: {self.mirror_country!r}")
        self._validate_additional_users()
        self._validate_server_settings()

    def validate_for_persona(self, persona: str) -> None:
        """Check settings that depend on the chosen persona."""
        if persona == SERVER_PERSONA:
            if not self.ssh_authorized_keys:
                raise InstallError("a server needs at least one SSH public key; password logins are disabled")
            return
        for name, off in SERVER_ONLY_SETTINGS.items():
            if getattr(self, name) != off:
                raise InstallError(f"{name} is only available for the Server persona")

    def _validate_server_settings(self) -> None:
        if not isinstance(self.ssh_authorized_keys, tuple) or not all(
            isinstance(key, str) and SSH_KEY_PATTERN.fullmatch(key) for key in self.ssh_authorized_keys
        ):
            raise InstallError("ssh_authorized_keys must be a list of SSH public keys")
        if not isinstance(self.static_dns, tuple) or not all(isinstance(item, str) for item in self.static_dns):
            raise InstallError("static_dns must be a list of addresses")
        for name in ("static_address", "static_gateway", "static_interface"):
            if not isinstance(getattr(self, name), str):
                raise InstallError(f"{name} must be a string")
        try:
            validate_static_network(
                self.static_address, self.static_gateway, self.static_dns, self.static_interface
            )
        except ServerConfigError as error:
            raise InstallError(str(error)) from error

    def _validate_features(self) -> None:
        if self.snapshots and not (self.filesystem == "btrfs" and self.btrfs_subvolumes):
            raise InstallError("snapshots require Btrfs with subvolumes")
        if self.nvidia_driver not in NVIDIA_DRIVERS:
            raise InstallError(f"nvidia_driver must be one of: {', '.join(NVIDIA_DRIVERS)}")
        if self.tpm2_unlock and not self.encrypt:
            raise InstallError("TPM2 unlock requires disk encryption")
        if self.secure_boot:
            if self.firmware != "uefi":
                raise InstallError("Secure Boot requires UEFI")
            if self.bootloader not in SECURE_BOOT_LOADERS:
                raise InstallError(
                    f"Secure Boot is supported with {' or '.join(SECURE_BOOT_LOADERS)}, not {self.bootloader}"
                )

    def _validate_storage(self) -> None:
        if self.disk_layout not in DISK_LAYOUTS:
            raise InstallError(f"disk layout must be one of: {', '.join(DISK_LAYOUTS)}")
        if self.disk_layout != "erase" and self.firmware != "uefi":
            raise InstallError("installing alongside existing partitions requires UEFI")
        if self.disk_layout == "partitions":
            if not self.root_partition or not self.boot_partition:
                raise InstallError("choose both a root partition and an EFI system partition")
            for partition in (self.root_partition, self.boot_partition):
                if not partition.startswith(self.disk) or partition == self.disk:
                    raise InstallError(f"{partition} is not a partition of {self.disk}")
            if self.root_partition == self.boot_partition:
                raise InstallError("root and EFI system partitions must be different")
        if self.encrypt:
            passphrase = self.encryption_passphrase or ""
            if len(passphrase) < 8:
                raise InstallError("encryption passphrase must be at least 8 characters")
            if not _is_console_typable(passphrase):
                # The early-boot prompt only reliably accepts printable ASCII.
                raise InstallError("encryption passphrase must use printable ASCII characters")

    def _validate_additional_users(self) -> None:
        seen = {self.username, "root"}
        for user in self.additional_users:
            if not USERNAME_PATTERN.fullmatch(user.username):
                raise InstallError(f"invalid user name: {user.username!r}")
            if user.username in seen:
                raise InstallError(f"user name {user.username!r} is already taken")
            if not user.password:
                raise InstallError(f"password for {user.username} cannot be empty")
            seen.add(user.username)


class CommandRunner:
    """Execute commands without invoking a shell or printing secret input.

    With a log_path, command output is streamed line by line into both the
    terminal and the log instead of inheriting the terminal directly, so
    only installation runners should set it (interactive tools like cfdisk
    need the real terminal).
    """

    stream_output = False
    # Class-level so subclasses that skip __init__ still share it; heartbeat
    # threads emit alongside the command-output loop.
    _emit_lock = threading.Lock()

    def __init__(
        self, *, dry_run: bool = False, log_path: Path | None = None, quiet: bool = False
    ) -> None:
        self.dry_run = dry_run
        self.log_path = log_path
        # Quiet runners (used under curses) skip echoing commands to the terminal.
        self.quiet = quiet
        if log_path is not None:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.touch(mode=0o600, exist_ok=True)
            except OSError:
                # Not root (the backend reports that clearly later) or a
                # read-only filesystem: keep going without a log file.
                self.log_path = None

    def emit(self, line: str) -> None:
        with self._emit_lock:
            if not getattr(self, "quiet", False):
                print(line, flush=True)
        self._write_log(line)

    def _write_log(self, line: str) -> None:
        log_path = getattr(self, "log_path", None)
        if log_path is None:
            return
        try:
            with self._emit_lock, log_path.open("a") as log:
                log.write(line + "\n")
        except OSError:
            pass

    def run(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self.emit(f"+ {shlex.join(args)}")
        if self.dry_run:
            self.emit("  (dry-run: not executed)")
            stdout = "" if capture_output else None
            stderr = "" if capture_output else None
            return subprocess.CompletedProcess(args, 0, stdout, stderr)
        if capture_output or not (self.stream_output or self.log_path is not None):
            return subprocess.run(
                args,
                check=check,
                text=True,
                input=input_text,
                stdout=subprocess.PIPE if capture_output else None,
                stderr=subprocess.PIPE if capture_output else None,
            )
        # Binary pipes: text mode would turn pacman's progress-bar carriage
        # returns into newlines and flood the log with partial redraws.
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if input_text is not None:
            assert process.stdin is not None
            process.stdin.write(input_text.encode())
            process.stdin.close()
        output_lines: list[str] = []
        assert process.stdout is not None
        for raw_line in process.stdout:
            text = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            # Keep only the final redraw of a carriage-return-updated line.
            line = text.rstrip("\r").rsplit("\r", 1)[-1]
            output_lines.append(line)
            self.emit(line)
        returncode = process.wait()
        output = "\n".join(output_lines)
        if output:
            output += "\n"
        if check and returncode != 0:
            raise subprocess.CalledProcessError(returncode, args, output=output)
        return subprocess.CompletedProcess(args, returncode, output, "")


def directory_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                # pacman renames .part files as downloads finish.
                pass
    return total


class DownloadHeartbeat:
    """Periodically emit the size of a download directory from a thread."""

    def __init__(
        self,
        emit: Callable[[str], None],
        path: Path,
        *,
        interval: float = HEARTBEAT_INTERVAL,
    ) -> None:
        self.emit = emit
        self.path = path
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="download-heartbeat", daemon=True)

    def __enter__(self) -> DownloadHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def _loop(self) -> None:
        last_size = -1
        unchanged = 0.0
        while not self._stop.wait(self.interval):
            size = directory_size(self.path)
            if size == last_size:
                unchanged += self.interval
                # Also true while pacman unpacks already-downloaded packages.
                self.emit(
                    f"Package cache: {format_size(size)} (unchanged for {unchanged:.0f}s)"
                )
            else:
                unchanged = 0.0
                self.emit(f"Package cache: {format_size(size)} downloaded so far")
            last_size = size


def _mounted_paths(device: dict[str, object]) -> Iterable[str]:
    mountpoints = device.get("mountpoints") or []
    if isinstance(mountpoints, list):
        yield from (str(item) for item in mountpoints if item)
    for child in device.get("children") or []:
        if isinstance(child, dict):
            yield from _mounted_paths(child)


def list_install_disks(runner: CommandRunner | None = None) -> tuple[DiskInfo, ...]:
    active_runner = runner or CommandRunner()
    result = active_runner.run(
        [
            "lsblk",
            "--json",
            "--tree",  # without NAME in --output, lsblk otherwise flattens partitions
            "--bytes",
            "--output",
            "PATH,SIZE,TYPE,MODEL,RO,RM,MOUNTPOINTS",
        ],
        capture_output=True,
    )
    disks: list[DiskInfo] = []
    for device in json.loads(result.stdout).get("blockdevices", []):
        if device.get("type") != "disk" or bool(device.get("ro")):
            continue
        mountpoints = tuple(_mounted_paths(device))
        if any(path.startswith("/run/archiso") or path == "/" for path in mountpoints):
            continue
        path = str(device.get("path") or "")
        if not path.startswith("/dev/"):
            continue
        disks.append(
            DiskInfo(
                path=path,
                size=int(device.get("size") or 0),
                model=str(device.get("model") or "Unknown model").strip(),
                removable=bool(device.get("rm")),
                partitioned=bool(device.get("children")),
            )
        )
    return tuple(disks)


_TIMEZONE_SKIP_NAMES = {
    "zone.tab",
    "zone1970.tab",
    "iso3166.tab",
    "tzdata.zi",
    "leapseconds",
    "Factory",
    "posixrules",
    "leap-seconds.list",
}

# Common abbreviations users type that don't appear verbatim in IANA zone
# names, mapped to a keyword that does (e.g. "AST" -> Atlantic/* zones).
TIMEZONE_ABBREVIATION_HINTS = {
    "ast": "atlantic",
    "adt": "atlantic",
    "est": "eastern",
    "edt": "eastern",
    "cst": "central",
    "cdt": "central",
    "mst": "mountain",
    "mdt": "mountain",
    "pst": "pacific",
    "pdt": "pacific",
    "akst": "alaska",
    "hst": "hawaii",
    "gmt": "utc",
    "bst": "london",
    "cet": "paris",
    "eet": "athens",
    "jst": "tokyo",
    "aest": "sydney",
    "acst": "adelaide",
    "awst": "perth",
    "ist": "kolkata",
}


def list_timezones(zoneinfo_root: Path = Path("/usr/share/zoneinfo")) -> tuple[str, ...]:
    zones: list[str] = []
    if not zoneinfo_root.is_dir():
        return ()
    for path in zoneinfo_root.rglob("*"):
        if not path.is_file():
            continue
        name = str(path.relative_to(zoneinfo_root))
        if name in _TIMEZONE_SKIP_NAMES or name.startswith("."):
            continue
        if name.startswith(("posix/", "right/")):
            continue
        zones.append(name)
    return tuple(sorted(zones))


def matches_timezone_query(name: str, query: str) -> bool:
    lowered = query.strip().lower()
    if not lowered:
        return True
    haystack = name.replace("_", " ").replace("/", " ").lower()
    words = [word for word in re.split(r"\s+", lowered) if word]
    for word in words:
        candidate = TIMEZONE_ABBREVIATION_HINTS.get(word, word)
        if candidate not in haystack and word not in haystack:
            return False
    return True


def search_timezones(zones: Sequence[str], query: str) -> tuple[str, ...]:
    return tuple(zone for zone in zones if matches_timezone_query(zone, query))


def detect_firmware(efi_dir: Path = Path("/sys/firmware/efi")) -> str:
    """Return the boot mode the live environment was started with."""
    return "uefi" if efi_dir.is_dir() else "bios"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def enable_multilib(config: str) -> str:
    lines = config.splitlines()
    found = False
    in_multilib = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped in {"#[multilib]", "[multilib]"}:
            lines[index] = "[multilib]"
            found = True
            in_multilib = True
            continue
        if in_multilib and stripped.startswith("["):
            in_multilib = False
        if in_multilib and stripped == "#Include = /etc/pacman.d/mirrorlist":
            lines[index] = "Include = /etc/pacman.d/mirrorlist"
    if not found:
        raise InstallError("the live pacman configuration has no multilib section")
    return "\n".join(lines) + "\n"


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


class InstallerBackend:
    REQUIRED_COMMANDS = (
        "arch-chroot",
        "blkid",
        "blockdev",
        "genfstab",
        "findmnt",
        "lsblk",
        "mkfs.ext4",
        "mkfs.fat",
        "mount",
        "pacstrap",
        "parted",
        "partprobe",
        "reflector",
        "sync",
        "udevadm",
        "umount",
        "wipefs",
    )

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        target_root: Path | None = None,
        pacman_config: Path = Path("/etc/pacman.conf"),
        zoneinfo_root: Path = Path("/usr/share/zoneinfo"),
        locale_gen: Path = Path("/etc/locale.gen"),
        require_root: bool = True,
        dry_run: bool = False,
        online_check: Callable[[], bool] = is_online,
        hardware_detector: Callable[..., HardwareProfile] = detect_hardware,
        secure_boot_setup_mode: Callable[[], bool] = lambda: bool(read_efi_flag("SetupMode")),
        iwd_storage: Path = IWD_STORAGE,
        keymap_resolver: Callable[[str, str], str] = console_keymap,
    ) -> None:
        if target_root is None:
            # Dry-run defaults to a throwaway directory so it's safe even if
            # a caller forgets to pick one; real installs keep using /mnt.
            target_root = Path(tempfile.mkdtemp(prefix="protogenos-dryrun-")) if dry_run else Path("/mnt")
        if runner is None:
            log_path = target_root.parent / f"{target_root.name}-install.log" if dry_run else INSTALL_LOG
            runner = CommandRunner(dry_run=dry_run, log_path=log_path)
        self.runner = runner
        self.target_root = target_root
        self.pacman_config = pacman_config
        self.zoneinfo_root = zoneinfo_root
        self.locale_gen = locale_gen
        self.require_root = require_root
        self.dry_run = dry_run
        self.online_check = online_check
        self.hardware_detector = hardware_detector
        self.secure_boot_setup_mode = secure_boot_setup_mode
        self.iwd_storage = iwd_storage
        self.keymap_resolver = keymap_resolver
        self.warnings: list[str] = []
        self._step_index = 0
        self._step_total = 0

    def install(
        self,
        plan: InstallPlan,
        config: InstallConfig,
        *,
        before_unmount: Callable[[Path], None] | None = None,
    ) -> None:
        """Run the whole installation.

        before_unmount runs after a successful install while the new system
        is still mounted (the CLI uses it to offer a chroot shell).
        """
        config.validate(self.zoneinfo_root)
        config.validate_for_persona(plan.persona)
        self._step_index = 0
        self._step_total = 7 if plan.aur_packages else 6
        self.warnings = []
        self._step("Checking the installation environment")
        self._validate_environment(config)
        storage = StorageManager(self.runner, self.target_root, dry_run=self.dry_run)
        try:
            self._step("Preparing disks")
            if self.dry_run:
                self.target_root.mkdir(parents=True, exist_ok=True)
            prepared = storage.prepare(config)
            if self.dry_run:
                self._seed_dry_run_root(config)

            self._step("Installing packages")
            hardware = self.hardware_detector(
                multilib=plan.multilib_required, desktop=plan.desktop
            )
            self.runner.emit(f"Detected hardware: {hardware.describe()}")
            self._select_mirrors(config)
            self._refresh_keyring()
            keymap = self.keymap_resolver(config.keyboard_layout, config.keyboard_variant)
            # Before pacstrap: the initramfs it builds embeds the console
            # keymap used at the disk-unlock prompt.
            self._write_target("etc/vconsole.conf", vconsole_conf(keymap))
            self._install_packages(plan, config, hardware)
            self._write_fstab()

            self._step("Configuring the system")
            self._configure_system(plan, config, hardware)
            self._copy_network_config()
            if plan.persona == SERVER_PERSONA:
                self._configure_server(plan, config)

            self._step("Installing the bootloader")
            # Bootloader goes before optional AUR builds so a failed build
            # can never leave an unbootable system behind.
            self._configure_initramfs(config)
            self._install_bootloader(plan, config, prepared)
            if config.secure_boot:
                self._configure_secure_boot(plan, config)
            if config.tpm2_unlock:
                self._enroll_tpm2(config, prepared)

            if plan.aur_packages:
                self._step("Building AUR packages")
                self._install_aur_packages(plan, config)

            self._step("Finishing up")
            if config.snapshots:
                self._create_install_snapshot(config)
            for warning in self.warnings:
                self.runner.emit(f"{WARNING_PREFIX}{warning}")
            self._write_target(
                "var/log/protogenos-install.json",
                json.dumps(export_config(plan, config), indent=2) + "\n",
            )
            self._copy_install_log()
            if before_unmount is not None:
                before_unmount(self.target_root)
            self.runner.run(["sync"])
        except StorageError as error:
            raise InstallError(str(error)) from error
        except boot.BootConfigError as error:
            raise InstallError(str(error)) from error
        except (OSError, subprocess.CalledProcessError) as error:
            raise InstallError(f"installation command failed: {error}") from error
        finally:
            storage.teardown()

    def _step(self, title: str) -> None:
        self._step_index += 1
        self.runner.emit(f"{STEP_PREFIX}{self._step_index}/{self._step_total}: {title}")

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        self.runner.emit(f"{WARNING_PREFIX}{message}")

    # -- validation -------------------------------------------------------

    def _required_commands(self, config: InstallConfig) -> tuple[str, ...]:
        commands = list(self.REQUIRED_COMMANDS)
        commands += FILESYSTEM_TOOLS[config.filesystem]
        if config.encrypt:
            commands.append("cryptsetup")
        return tuple(commands)

    def _validate_environment(self, config: InstallConfig) -> None:
        disk = config.disk
        if not self.dry_run:
            if self.require_root and os.geteuid() != 0:
                raise InstallError("installation must run as root")
            missing = [
                command
                for command in self._required_commands(config)
                if shutil.which(command) is None
            ]
            if missing:
                raise InstallError(f"missing installation tools: {', '.join(missing)}")
        if not self.locale_gen.is_file():
            raise InstallError(f"locale catalog is unavailable: {self.locale_gen}")
        locale_pattern = re.compile(
            rf"^\s*#?\s*{re.escape(config.locale)}\s+UTF-8\s*$", re.MULTILINE
        )
        if not locale_pattern.search(self.locale_gen.read_text()):
            raise InstallError(f"locale is unavailable: {config.locale}")
        if self.dry_run:
            # Real disks aren't touched in dry-run mode, so the block-device
            # and mount-state checks below (which assume a real target) are
            # skipped; CommandRunner no-ops every command regardless.
            return
        if not self.online_check():
            raise InstallError(
                "no internet connection; packages are downloaded during installation. "
                "Connect with Ethernet or Wi-Fi (iwctl) and try again"
            )
        if not Path(disk).is_block_device():
            raise InstallError(f"target is not a block device: {disk}")
        size_result = self.runner.run(
            ["blockdev", "--getsize64", disk], capture_output=True
        )
        try:
            disk_size = int(size_result.stdout.strip())
        except ValueError as error:
            raise InstallError(f"could not determine target disk size: {disk}") from error
        if disk_size < MIN_ROOT_BYTES:
            raise InstallError(f"target disk must be at least {MIN_ROOT_BYTES // GIB} GiB")
        target_mount = self.runner.run(
            ["findmnt", "--noheadings", "--mountpoint", str(self.target_root)],
            capture_output=True,
            check=False,
        )
        if target_mount.returncode == 0:
            raise InstallError(f"installation mount point is already in use: {self.target_root}")
        if config.disk_layout == "erase":
            mounted = self.runner.run(
                ["lsblk", "--noheadings", "--raw", "--output", "MOUNTPOINTS", disk],
                capture_output=True,
                check=False,
            )
            if mounted.returncode == 0 and mounted.stdout.strip():
                raise InstallError(f"target disk or one of its partitions is mounted: {disk}")

    def _seed_dry_run_root(self, config: InstallConfig) -> None:
        """Fake the handful of pacstrap-produced files later steps read/edit.

        No real rootfs exists in dry-run mode (partitioning/pacstrap are
        no-ops), so later steps would otherwise hit missing files.
        """
        self._write_target("etc/locale.gen", f"#{config.locale} UTF-8\n")
        self._write_target("etc/default/grub", "GRUB_TIMEOUT=5\n")
        self._write_target(
            "etc/mkinitcpio.conf",
            "HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block filesystems fsck)\n",
        )

    # -- packages ---------------------------------------------------------

    def _select_mirrors(self, config: InstallConfig) -> None:
        # Without a country, rank mirrors worldwide; the live ISO otherwise
        # uses its full, unranked mirror list.
        result = self.runner.run(reflector_command(config.mirror_country), check=False)
        if result.returncode != 0:
            where = f"in {config.mirror_country}" if config.mirror_country else "worldwide"
            self._warn(f"could not rank mirrors {where}; using the default mirror list")

    def _refresh_keyring(self) -> None:
        # Signing keys rotate; an older ISO's keyring rejects current packages.
        self.runner.run(["pacman", "-Sy", "--noconfirm", "--needed", "archlinux-keyring"])

    @staticmethod
    def kernel_package(plan: InstallPlan) -> str:
        return (plan.selections.get("kernel") or ("linux",))[0]

    def _install_packages(
        self,
        plan: InstallPlan,
        config: InstallConfig,
        hardware: HardwareProfile | None = None,
    ) -> None:
        aur = set(plan.aur_packages)
        packages = [package for package in plan.packages if package not in aur]
        if plan.desktop:
            packages.extend(("sudo", "nano", "man-db", "bash-completion"))
        else:
            # Minimal keeps only an editor, plus sudo when an account needs it.
            packages.append("nano")
            if config.grant_sudo or any(user.sudo for user in config.additional_users):
                packages.append("sudo")
        packages.append(FILESYSTEM_PACKAGES[config.filesystem])
        if config.firmware == "uefi":
            packages.append("dosfstools")
        if config.bootloader == "grub":
            packages.append("grub")
            if config.firmware == "uefi":
                packages.append("efibootmgr")
            if config.disk_layout != "erase":
                packages.append("os-prober")
        elif config.bootloader == "limine":
            packages.extend(("limine", "efibootmgr"))
        if config.encrypt:
            packages.append("cryptsetup")
        if config.swap == "zram":
            packages.append("zram-generator")
        if config.kernel_headers:
            packages.append(f"{self.kernel_package(plan)}-headers")
        if hardware is not None:
            packages.extend(hardware.packages)
        packages = self._apply_feature_packages(plan, config, hardware, packages)
        if plan.aur_packages:
            packages.extend(("base-devel", "git"))

        pacman_text = enable_parallel_downloads(self.pacman_config.read_text())
        if plan.multilib_required:
            pacman_text = enable_multilib(pacman_text)
        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile("w", prefix="protogenos-pacman-", delete=False) as file:
                file.write(pacman_text)
                temporary_path = file.name
            # pacstrap without -c downloads into the target's own cache.
            cache = self.target_root / "var/cache/pacman/pkg"
            with DownloadHeartbeat(self.runner.emit, cache):
                self.runner.run(
                    [
                        "pacstrap",
                        "-K",
                        "-P",
                        "-C",
                        temporary_path,
                        str(self.target_root),
                        *_unique(packages),
                    ]
                )
        finally:
            if temporary_path:
                Path(temporary_path).unlink(missing_ok=True)
        self._write_target("etc/pacman.conf", pacman_text)

    def _apply_feature_packages(
        self,
        plan: InstallPlan,
        config: InstallConfig,
        hardware: HardwareProfile | None,
        packages: list[str],
    ) -> list[str]:
        if config.snapshots:
            packages.extend(("snapper", "snap-pac"))
            if config.bootloader == "grub":
                packages.extend(("grub-btrfs", "inotify-tools"))
        if config.flatpak:
            packages.append("flatpak")
        if config.gaming_tweaks:
            packages.extend(("gamemode", "power-profiles-daemon"))
            if plan.multilib_required:
                packages.append("lib32-gamemode")
        if config.nvidia_driver == "nvidia-open":
            # The proprietary stack replaces nouveau's Vulkan driver.
            packages = [package for package in packages if "vulkan-nouveau" not in package]
            kernel = self.kernel_package(plan)
            if kernel == "linux":
                packages.append("nvidia-open")
            else:
                packages.extend(("nvidia-open-dkms", f"{kernel}-headers"))
            packages.append("nvidia-utils")
            if plan.multilib_required:
                packages.append("lib32-nvidia-utils")
            if hardware is not None and any(vendor != VENDOR_NVIDIA for vendor in hardware.gpu_vendors):
                packages.append("nvidia-prime")
        if config.fingerprint:
            packages.append("fprintd")
        if config.tpm2_unlock:
            packages.append("tpm2-tss")
        if config.secure_boot:
            packages.append("sbctl")
        if "docker" in plan.selections.get("container", ()):
            packages.append("docker-compose")
        if config.cockpit:
            packages.append("cockpit")
            if "podman" in plan.selections.get("container", ()):
                packages.append("cockpit-podman")
        if config.netdata:
            packages.append("netdata")
        if config.fail2ban:
            packages.append("fail2ban")
        if config.update_downloads:
            # checkupdates needs fakeroot to sync its private database copy.
            packages.extend(("pacman-contrib", "fakeroot"))
        return packages

    def _write_fstab(self) -> None:
        result = self.runner.run(
            ["genfstab", "-U", str(self.target_root)], capture_output=True
        )
        # subvolid pins a btrfs mount to one subvolume ID and breaks
        # snapshot rollbacks; subvol= alone is enough.
        fstab = re.sub(r"subvolid=\d+,?|,subvolid=\d+", "", result.stdout or "")
        self._write_target("etc/fstab", fstab)

    # -- system configuration --------------------------------------------

    def _configure_system(
        self,
        plan: InstallPlan,
        config: InstallConfig,
        hardware: HardwareProfile | None = None,
    ) -> None:
        self._write_target("etc/hostname", f"{config.hostname}\n")
        self._write_target(
            "etc/hosts",
            "127.0.0.1 localhost\n"
            "::1 localhost\n"
            f"127.0.1.1 {config.hostname}.localdomain {config.hostname}\n",
        )
        self._write_target("etc/locale.conf", f"LANG={config.locale}\n")
        self._enable_locale(config.locale)
        self._write_release_metadata(plan)
        if plan.desktop:
            self._apply_desktop_theming(plan)
            self._configure_desktop_keyboard(config)
        if config.swap == "zram":
            self._write_target("etc/systemd/zram-generator.conf", ZRAM_GENERATOR_CONF)
            self._write_target("etc/sysctl.d/99-vm-zram-parameters.conf", ZRAM_SYSCTL_CONF)

        self._chroot("ln", "-sf", f"/usr/share/zoneinfo/{config.timezone}", "/etc/localtime")
        self._chroot("hwclock", "--systohc")
        self._chroot("locale-gen")
        self._create_users(config)
        self._configure_features(plan, config)
        self._enable_services(hardware, desktop=plan.desktop)
        if config.serial_console:
            self._chroot("systemctl", "enable", "serial-getty@ttyS0.service")

    def _configure_features(self, plan: InstallPlan, config: InstallConfig) -> None:
        if config.snapshots:
            self._configure_snapshots(config)
        if config.flatpak:
            added = self.runner.run(
                [
                    "arch-chroot",
                    str(self.target_root),
                    "flatpak",
                    "remote-add",
                    "--system",
                    "--if-not-exists",
                    "flathub",
                    FLATHUB_REPO,
                ],
                check=False,
            )
            if added.returncode != 0:
                self._warn("could not add the Flathub remote; add it later with flatpak remote-add")
        if config.gaming_tweaks:
            self._write_target("etc/sysctl.d/80-protogenos-gaming.conf", GAMING_SYSCTL_CONF)
            self._write_target("etc/environment.d/80-protogenos-gaming.conf", GAMING_ENVIRONMENT_CONF)
            self._write_target("etc/modules-load.d/ntsync.conf", NTSYNC_MODULES_CONF)
            self._write_target("usr/local/bin/game-performance", GAME_PERFORMANCE_SCRIPT)
            (self.target_root / "usr/local/bin/game-performance").chmod(0o755)
            for username in (config.username, *(user.username for user in config.additional_users)):
                self._chroot("usermod", "--append", "--groups", "gamemode", username)

    def _configure_snapshots(self, config: InstallConfig) -> None:
        # Written directly: `snapper create-config` wants to create .snapshots
        # itself, but the installer already mounts the @snapshots subvolume there.
        self._write_target("etc/snapper/configs/root", SNAPPER_ROOT_CONFIG)
        self._write_target("etc/conf.d/snapper", 'SNAPPER_CONFIGS="root"\n')
        snapshots = self.target_root / ".snapshots"
        snapshots.mkdir(exist_ok=True)
        snapshots.chmod(0o750)
        timers = ["snapper-timeline.timer", "snapper-cleanup.timer"]
        if config.bootloader == "grub":
            self._write_target("etc/default/grub-btrfs/config", GRUB_BTRFS_CONFIG)
            timers.append("grub-btrfsd.service")
        self._chroot("systemctl", "enable", *timers)

    def _create_install_snapshot(self, config: InstallConfig) -> None:
        created = self.runner.run(
            [
                "arch-chroot",
                str(self.target_root),
                "snapper",
                "--no-dbus",
                "-c",
                "root",
                "create",
                "--description",
                "protogenOS installation",
                "--userdata",
                "important=yes",
            ],
            check=False,
        )
        if created.returncode != 0:
            self._warn("could not create the initial snapshot")
        elif config.bootloader == "grub":
            # Add the new snapshot to GRUB's snapshot submenu right away.
            self._chroot("grub-mkconfig", "-o", "/boot/grub/grub.cfg")

    def _configure_desktop_keyboard(self, config: InstallConfig) -> None:
        layout, variant = config.keyboard_layout, config.keyboard_variant
        self._write_target("etc/X11/xorg.conf.d/00-keyboard.conf", x11_keyboard_conf(layout, variant))
        # Plasma reads its own kxkbrc once a user has one; seed new accounts.
        self._write_target("etc/skel/.config/kxkbrc", plasma_kxkbrc(layout, variant))

    def _create_users(self, config: InstallConfig) -> None:
        accounts = [UserAccount(config.username, config.user_password, config.grant_sudo)]
        accounts += list(config.additional_users)
        for account in accounts:
            useradd_args = ["useradd", "--create-home", "--shell", "/bin/bash"]
            if account.sudo:
                useradd_args += ["--groups", "wheel"]
            useradd_args.append(account.username)
            self._chroot(*useradd_args)
            self._chroot("chpasswd", input_text=f"{account.username}:{account.password}\n")
        if any(account.sudo for account in accounts):
            sudoers = self.target_root / "etc/sudoers.d/10-protogenos-wheel"
            sudoers.parent.mkdir(parents=True, exist_ok=True)
            sudoers.write_text("%wheel ALL=(ALL:ALL) ALL\n")
            sudoers.chmod(0o440)
        if config.grant_sudo:
            self._chroot("passwd", "--lock", "root")
        else:
            self._chroot("chpasswd", input_text=f"root:{config.root_password}\n")

    # Enabled only when the installed packages actually ship the unit.
    OPTIONAL_SERVICES = ("bluetooth.service", "cups.socket", "power-profiles-daemon.service")

    def _enable_services(self, hardware: HardwareProfile | None, *, desktop: bool = True) -> None:
        services = [
            "NetworkManager.service",
            "systemd-timesyncd.service",
            "fstrim.timer",
        ]
        if desktop:
            services.insert(1, "plasmalogin.service")
        candidates = [*self.OPTIONAL_SERVICES, *(hardware.services if hardware else ())]
        for unit in candidates:
            if (self.target_root / "usr/lib/systemd/system" / unit).exists():
                services.append(unit)
        self._chroot("systemctl", "enable", *services)

    def _copy_network_config(self) -> None:
        """Carry Wi-Fi networks joined in the live session into NetworkManager."""
        credentials = read_iwd_credentials(self.iwd_storage)
        for credential in credentials:
            name = iwd_file_name(credential.ssid, "nmconnection")
            path = self.target_root / "etc/NetworkManager/system-connections" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600, exist_ok=True)
            path.chmod(0o600)
            path.write_text(networkmanager_keyfile(credential))
            self.runner.emit(f"Saved Wi-Fi network {credential.ssid!r} for the installed system")

    def _configure_server(self, plan: InstallPlan, config: InstallConfig) -> None:
        """SSH, firewall, and optional services for the Server persona."""
        self._write_target("etc/ssh/sshd_config.d/10-protogenos.conf", SSHD_CONFIG)
        self._install_authorized_keys(config)
        for service in firewall_services(cockpit=config.cockpit, netdata=config.netdata):
            opened = self.runner.run(
                [
                    "arch-chroot",
                    str(self.target_root),
                    "firewall-offline-cmd",
                    "--zone=public",
                    f"--add-service={service}",
                ],
                check=False,
            )
            if opened.returncode != 0:
                self._warn(f"could not open the {service} firewall service; run 'sudo firewall-cmd --permanent --add-service={service}'")
        if config.fail2ban:
            self._write_target("etc/fail2ban/jail.d/10-protogenos.local", FAIL2BAN_JAIL)
        if config.update_downloads:
            self._write_target("etc/systemd/system/protogenos-download-updates.service", UPDATE_DOWNLOAD_SERVICE)
            self._write_target("etc/systemd/system/protogenos-download-updates.timer", UPDATE_DOWNLOAD_TIMER)
        if config.static_address:
            path = self.target_root / "etc/NetworkManager/system-connections/protogenos-static.nmconnection"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600, exist_ok=True)
            path.chmod(0o600)
            path.write_text(
                static_connection_keyfile(
                    config.static_address, config.static_gateway, config.static_dns, config.static_interface
                )
            )
        if "docker" in plan.selections.get("container", ()):
            # docker group membership is root-equivalent; only the administrator gets it.
            self._chroot("usermod", "--append", "--groups", "docker", config.username)

        services = ["sshd.service", "firewalld.service"]
        if "docker" in plan.selections.get("container", ()):
            services.append("docker.service")
        if config.cockpit:
            services.append("cockpit.socket")
        if config.netdata:
            services.append("netdata.service")
        if config.fail2ban:
            services.append("fail2ban.service")
        if config.update_downloads:
            services.append("protogenos-download-updates.timer")
        self._chroot("systemctl", "enable", *services)

    def _install_authorized_keys(self, config: InstallConfig) -> None:
        ssh_dir = f"/home/{config.username}/.ssh"
        self._write_target(f"home/{config.username}/.ssh/authorized_keys", "\n".join(config.ssh_authorized_keys) + "\n")
        self._chroot("chmod", "700", ssh_dir)
        self._chroot("chmod", "600", f"{ssh_dir}/authorized_keys")
        self._chroot("chown", "-R", f"{config.username}:{config.username}", ssh_dir)

    def _copy_install_log(self) -> None:
        log_path = getattr(self.runner, "log_path", None)
        if log_path is None or not Path(log_path).is_file():
            return
        destination = self.target_root / "var/log/protogenos-install.log"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(log_path, destination)
        destination.chmod(0o600)

    # -- initramfs and bootloader -----------------------------------------

    def _configure_initramfs(self, config: InstallConfig) -> None:
        if not config.encrypt and config.nvidia_driver != "nvidia-open":
            return
        path = self.target_root / "etc/mkinitcpio.conf"
        content = path.read_text()
        if config.encrypt:
            content = boot.add_encrypt_hook(content)
        if config.nvidia_driver == "nvidia-open":
            # kms would pull nouveau into early boot ahead of the NVIDIA module.
            content = boot.remove_hook(content, "kms")
        path.write_text(content)
        self._chroot("mkinitcpio", "-P")

    def _kernel_cmdline(
        self, config: InstallConfig, prepared: PreparedStorage, *, include_root: bool = True
    ) -> str:
        mkinitcpio = self.target_root / "etc/mkinitcpio.conf"
        systemd_initramfs = not mkinitcpio.is_file() or boot.uses_systemd_initramfs(
            mkinitcpio.read_text()
        )
        return boot.kernel_cmdline(
            root_uuid=prepared.root_uuid or "DRY-RUN-UUID",
            filesystem=config.filesystem,
            btrfs_subvolumes=config.btrfs_subvolumes,
            luks_uuid=(prepared.luks_uuid or "DRY-RUN-LUKS-UUID") if config.encrypt else "",
            tpm2=config.tpm2_unlock,
            systemd_initramfs=systemd_initramfs,
            zram=config.swap == "zram",
            serial_console=config.serial_console,
            include_root=include_root,
        )

    def _install_bootloader(
        self, plan: InstallPlan, config: InstallConfig, prepared: PreparedStorage
    ) -> None:
        if config.bootloader == "systemd-boot":
            self._install_systemd_boot(plan, config, prepared)
        elif config.bootloader == "limine":
            self._install_limine(plan, config, prepared)
        else:
            self._install_grub(config, prepared)

    def _configure_secure_boot(self, plan: InstallPlan, config: InstallConfig) -> None:
        self._chroot("sbctl", "create-keys")
        kernel = self.kernel_package(plan)
        if config.bootloader == "systemd-boot":
            # The .signed copy is what bootctl and systemd-boot-update install.
            source = "/usr/lib/systemd/boot/efi/systemd-bootx64.efi"
            self._chroot("sbctl", "sign", "--save", "--output", f"{source}.signed", source)
            efi_files = ["/boot/EFI/systemd/systemd-bootx64.efi", "/boot/EFI/BOOT/BOOTX64.EFI"]
        else:
            efi_files = ["/boot/EFI/limine/BOOTX64.EFI", "/boot/EFI/BOOT/BOOTX64.EFI"]
        # --save lets sbctl's pacman hook re-sign these after every update.
        for path in (*efi_files, f"/boot/vmlinuz-{kernel}"):
            self._chroot("sbctl", "sign", "--save", path)
        if not self.secure_boot_setup_mode():
            self._warn(
                "Secure Boot keys were created and boot files signed, but the firmware is not in "
                "Setup Mode; enable Setup Mode, then run 'sudo sbctl enroll-keys --microsoft'"
            )
            return
        enrolled = self.runner.run(
            ["arch-chroot", str(self.target_root), "sbctl", "enroll-keys", "--microsoft"],
            check=False,
        )
        if enrolled.returncode != 0:
            self._warn("could not enroll Secure Boot keys; run 'sudo sbctl enroll-keys --microsoft' after booting")

    def _enroll_tpm2(self, config: InstallConfig, prepared: PreparedStorage) -> None:
        # Bound to PCR 7 (Secure Boot state): changing Secure Boot settings
        # later falls back to the passphrase until the TPM slot is re-enrolled.
        mkinitcpio = self.target_root / "etc/mkinitcpio.conf"
        if mkinitcpio.is_file() and not boot.uses_systemd_initramfs(mkinitcpio.read_text()):
            self._warn("TPM2 unlock needs a systemd-based initramfs; skipping TPM2 enrollment")
            return
        enroll = ["systemd-cryptenroll", "--tpm2-device=auto", "--tpm2-pcrs=7"]
        if self.dry_run:
            enrolled = self.runner.run([*enroll, prepared.root_partition], check=False)
        else:
            # NamedTemporaryFile is created mode 0600.
            with tempfile.NamedTemporaryFile("w", prefix="protogenos-luks-", delete=True) as key_file:
                key_file.write(config.encryption_passphrase or "")
                key_file.flush()
                enrolled = self.runner.run(
                    [*enroll, f"--unlock-key-file={key_file.name}", prepared.root_partition],
                    check=False,
                )
        if enrolled.returncode != 0:
            self._warn("could not enroll the TPM2 chip; the disk will ask for its passphrase at boot")
        elif config.secure_boot:
            self._warn(
                "TPM2 unlock is bound to the current Secure Boot state; after enabling Secure Boot, "
                "re-enroll with 'sudo systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 "
                f"{prepared.root_partition}'"
            )

    def _install_grub(self, config: InstallConfig, prepared: PreparedStorage) -> None:
        path = self.target_root / "etc/default/grub"
        content = path.read_text()
        content = boot.set_shell_variable(content, "GRUB_DISTRIBUTOR", "protogenOS")
        extra = self._kernel_cmdline(config, prepared, include_root=False)
        if extra:
            content = boot.set_shell_variable(content, "GRUB_CMDLINE_LINUX", extra)
        if config.disk_layout != "erase":
            # Find Windows or other installed systems for dual boot.
            content = boot.set_shell_variable(content, "GRUB_DISABLE_OS_PROBER", "false")
        if config.serial_console:
            content = boot.set_shell_variable(content, "GRUB_TERMINAL_INPUT", "console serial")
            content = boot.set_shell_variable(content, "GRUB_TERMINAL_OUTPUT", "console serial")
            content = boot.set_shell_variable(content, "GRUB_SERIAL_COMMAND", GRUB_SERIAL_COMMAND)
        path.write_text(content)

        if config.firmware == "uefi":
            nvram = self.runner.run(
                [
                    "arch-chroot",
                    str(self.target_root),
                    "grub-install",
                    "--target=x86_64-efi",
                    "--efi-directory=/boot",
                    "--bootloader-id=protogenOS",
                ],
                check=False,
            )
            if nvram.returncode != 0:
                self._warn("could not register a UEFI boot entry; relying on the fallback path")
            # The removable path boots even when firmware forgets NVRAM entries.
            self._chroot(
                "grub-install",
                "--target=x86_64-efi",
                "--efi-directory=/boot",
                "--bootloader-id=protogenOS",
                "--removable",
            )
        else:
            self._chroot("grub-install", "--target=i386-pc", config.disk)
        self._chroot("grub-mkconfig", "-o", "/boot/grub/grub.cfg")

    def _has_fallback_initramfs(self, kernel: str) -> bool:
        return (self.target_root / f"boot/initramfs-{kernel}-fallback.img").is_file()

    def _install_systemd_boot(
        self, plan: InstallPlan, config: InstallConfig, prepared: PreparedStorage
    ) -> None:
        installed = self.runner.run(
            ["arch-chroot", str(self.target_root), "bootctl", "install", "--esp-path=/boot"],
            check=False,
        )
        if installed.returncode != 0:
            self._warn("could not register a UEFI boot entry; relying on the fallback path")
            self._chroot("bootctl", "install", "--esp-path=/boot", "--no-variables")
        kernel = self.kernel_package(plan)
        cmdline = self._kernel_cmdline(config, prepared)
        self._write_target("boot/loader/loader.conf", boot.systemd_boot_loader_conf())
        self._write_target("boot/loader/entries/protogenos.conf", boot.systemd_boot_entry(kernel, cmdline))
        if self._has_fallback_initramfs(kernel):
            self._write_target(
                "boot/loader/entries/protogenos-fallback.conf",
                boot.systemd_boot_entry(kernel, cmdline, fallback=True),
            )
        self._chroot("systemctl", "enable", "systemd-boot-update.service")

    def _install_limine(
        self, plan: InstallPlan, config: InstallConfig, prepared: PreparedStorage
    ) -> None:
        for directory in ("boot/EFI/limine", "boot/EFI/BOOT"):
            (self.target_root / directory).mkdir(parents=True, exist_ok=True)
        self._chroot("cp", "/usr/share/limine/BOOTX64.EFI", "/boot/EFI/limine/BOOTX64.EFI")
        self._chroot("cp", "/usr/share/limine/BOOTX64.EFI", "/boot/EFI/BOOT/BOOTX64.EFI")
        kernel = self.kernel_package(plan)
        self._write_target(
            "boot/limine.conf",
            boot.limine_conf(
                kernel,
                self._kernel_cmdline(config, prepared),
                fallback=self._has_fallback_initramfs(kernel),
            ),
        )
        self._write_target("etc/pacman.d/hooks/99-limine.hook", boot.LIMINE_PACMAN_HOOK)
        if prepared.esp_number is None:
            self._warn("could not register a UEFI boot entry; relying on the fallback path")
            return
        registered = self.runner.run(
            [
                "arch-chroot",
                str(self.target_root),
                "efibootmgr",
                "--create",
                "--disk",
                config.disk,
                "--part",
                str(prepared.esp_number),
                "--label",
                "protogenOS",
                "--loader",
                "\\EFI\\limine\\BOOTX64.EFI",
                "--unicode",
            ],
            check=False,
        )
        if registered.returncode != 0:
            self._warn("could not register a UEFI boot entry; relying on the fallback path")

    def _enable_locale(self, locale: str) -> None:
        path = self.target_root / "etc/locale.gen"
        content = path.read_text()
        pattern = re.compile(
            rf"^\s*#?\s*{re.escape(locale)}\s+UTF-8\s*$", re.MULTILINE
        )
        content, replacements = pattern.subn(f"{locale} UTF-8", content, count=1)
        if replacements != 1:
            raise InstallError(f"locale is unavailable in the installed system: {locale}")
        path.write_text(content)

    def _write_release_metadata(self, plan: InstallPlan) -> None:
        self._write_target(
            "usr/lib/os-release",
            'NAME="protogenOS"\n'
            'PRETTY_NAME="protogenOS"\n'
            "ID=protogenos\n"
            "ID_LIKE=arch\n"
            "BUILD_ID=rolling\n"
            f"VARIANT_ID={plan.persona}\n"
            'ANSI_COLOR="38;2;213;31;61"\n'
            "LOGO=protogenos\n"
            'HOME_URL="https://github.com/kalkafox/protogenOS"\n'
            'BUG_REPORT_URL="https://github.com/kalkafox/protogenOS/issues"\n',
        )
        self._write_target("etc/issue", "protogenOS \\r (\\l)\n")
        self._write_target(
            "etc/motd", "Welcome to protogenOS — furry-powered and Arch-based.\n"
        )

    def _apply_desktop_theming(self, plan: InstallPlan) -> None:
        icon_selected = "papirus" in plan.selections.get("icon-theme", ())
        theme_selected = "sweet" in plan.selections.get("global-theme", ())

        look_and_feel = (
            "com.github.vinceliuice.sweet-dark" if theme_selected else "org.kde.breezedark.desktop"
        )
        icon_theme = "Papirus-Dark" if icon_selected else "breeze-dark"

        kdeglobals_lines = [
            "[KDE]",
            f"LookAndFeel={look_and_feel}",
            "widgetStyle=Breeze",
            "",
            "[General]",
            "ColorScheme=BreezeDark",
            "",
            "[Icons]",
            f"Theme={icon_theme}",
            "",
        ]
        content = "\n".join(kdeglobals_lines).rstrip() + "\n"
        self._write_target("etc/skel/.config/kdeglobals", content)
        self._write_target("etc/xdg/kdeglobals", content)

        # kdeglobals' LookAndFeel key is only a record of the last-applied
        # package; Plasma never auto-applies it on its own. Force a real
        # first-login apply (dark plasma theme, colors, splash, decoration)
        # via a self-removing autostart entry.
        self._write_target(
            "usr/local/bin/protogenos-apply-theme",
            "#!/bin/sh\n"
            f"plasma-apply-lookandfeel -a {shlex.quote(look_and_feel)}\n"
            'rm -f "$HOME/.config/autostart/protogenos-apply-theme.desktop"\n',
        )
        (self.target_root / "usr/local/bin/protogenos-apply-theme").chmod(0o755)
        self._write_target(
            "etc/skel/.config/autostart/protogenos-apply-theme.desktop",
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Exec=/usr/local/bin/protogenos-apply-theme\n"
            "X-KDE-autostart-phase=1\n"
            "NoDisplay=true\n"
            "Name=protogenOS theme setup\n",
        )

    def _install_aur_packages(self, plan: InstallPlan, config: InstallConfig) -> None:
        """Build AUR packages with yay (resolves AUR dependencies).

        Failures are reported as warnings rather than aborting: the base
        system and bootloader are already in place at this point.
        """
        if not plan.aur_packages:
            return
        (self.target_root / "etc/pacman.conf").chmod(0o644)

        temporary_sudoers = self.target_root / "etc/sudoers.d/99-protogenos-aur"
        temporary_sudoers.parent.mkdir(parents=True, exist_ok=True)
        temporary_sudoers.write_text(
            f"{config.username} ALL=(ALL:ALL) NOPASSWD: ALL\n"
        )
        temporary_sudoers.chmod(0o440)
        failed: list[str] = []
        try:
            helper_ready = self._try_as_user(
                config, f"{self._makepkg_script(AUR_HELPER)} && {AUR_HELPER} --version"
            )
            if not helper_ready:
                self._warn(f"could not build the {AUR_HELPER} AUR helper; building packages directly")
            packages = " ".join(shlex.quote(package) for package in plan.aur_packages)
            if helper_ready and self._try_as_user(config, f"{AUR_HELPER_INSTALL} {packages}"):
                return
            # Retry one at a time so one broken PKGBUILD doesn't take the
            # rest down with it, and so the warning names the culprits.
            for package in plan.aur_packages:
                script = (
                    f"{AUR_HELPER_INSTALL} {shlex.quote(package)}"
                    if helper_ready
                    else self._makepkg_script(package)
                )
                if not self._try_as_user(config, script):
                    failed.append(package)
        finally:
            temporary_sudoers.unlink(missing_ok=True)
        if failed:
            self._warn(
                "these AUR packages failed to build and were skipped: "
                + ", ".join(failed)
                + " (see /var/log/protogenos-install.log)"
            )

    @staticmethod
    def _makepkg_script(package: str) -> str:
        build_path = f"/tmp/protogenos-aur-{package}"
        clone_url = f"https://aur.archlinux.org/{package}.git"
        return (
            f"rm -rf {shlex.quote(build_path)} && "
            f"git clone --depth 1 {shlex.quote(clone_url)} {shlex.quote(build_path)} && "
            f"cd {shlex.quote(build_path)} && makepkg -si --noconfirm --needed"
        )

    def _try_as_user(self, config: InstallConfig, script: str) -> bool:
        try:
            self._run_as_user(config, script)
        except subprocess.CalledProcessError:
            return False
        return True

    def _run_as_user(self, config: InstallConfig, script: str) -> None:
        self.runner.run(
            [
                "arch-chroot",
                str(self.target_root),
                "runuser",
                "--user",
                config.username,
                "--",
                "/bin/bash",
                "-lc",
                script,
            ]
        )

    def _chroot(self, *args: str, input_text: str | None = None) -> None:
        self.runner.run(
            ["arch-chroot", str(self.target_root), *args], input_text=input_text
        )

    def _write_target(self, relative_path: str, content: str) -> None:
        path = self.target_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
