"""Guarded whole-disk installation backend for protogenOS."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .models import InstallPlan


HOSTNAME_PATTERN = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}_[A-Za-z]{2,3}\.UTF-8$")


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

    def validate(self, zoneinfo_root: Path = Path("/usr/share/zoneinfo")) -> None:
        if not self.disk.startswith("/dev/") or not Path(self.disk).name:
            raise InstallError(f"invalid target disk: {self.disk!r}")
        if self.firmware not in {"uefi", "bios"}:
            raise InstallError("firmware must be 'uefi' or 'bios'")
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


class CommandRunner:
    """Execute commands without invoking a shell or printing secret input."""

    def run(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        print(f"+ {shlex.join(args)}")
        return subprocess.run(
            args,
            check=check,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
        )


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


def partition_path(disk: str, number: int) -> str:
    separator = "p" if Path(disk).name[-1].isdigit() else ""
    return f"{disk}{separator}{number}"


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
        "sync",
        "udevadm",
        "umount",
        "wipefs",
    )

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        target_root: Path = Path("/mnt"),
        pacman_config: Path = Path("/etc/pacman.conf"),
        zoneinfo_root: Path = Path("/usr/share/zoneinfo"),
        locale_gen: Path = Path("/etc/locale.gen"),
        require_root: bool = True,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.target_root = target_root
        self.pacman_config = pacman_config
        self.zoneinfo_root = zoneinfo_root
        self.locale_gen = locale_gen
        self.require_root = require_root

    def install(self, plan: InstallPlan, config: InstallConfig) -> None:
        config.validate(self.zoneinfo_root)
        self._validate_environment(config)
        mount_attempted = False
        try:
            root_partition, boot_partition = self._partition_disk(config)
            mount_attempted = True
            self._format_and_mount(config, root_partition, boot_partition)
            self._install_packages(plan, config)
            self._write_fstab()
            self._configure_system(plan, config)
            self._install_aur_packages(plan, config)
            self._install_bootloader(config)
            self.runner.run(["sync"])
        except (OSError, subprocess.CalledProcessError) as error:
            raise InstallError(f"installation command failed: {error}") from error
        finally:
            if mount_attempted:
                self.runner.run(
                    ["umount", "--recursive", str(self.target_root)], check=False
                )

    def _validate_environment(self, config: InstallConfig) -> None:
        disk = config.disk
        if self.require_root and os.geteuid() != 0:
            raise InstallError("installation must run as root")
        missing = [command for command in self.REQUIRED_COMMANDS if shutil.which(command) is None]
        if missing:
            raise InstallError(f"missing installation tools: {', '.join(missing)}")
        if not self.locale_gen.is_file():
            raise InstallError(f"locale catalog is unavailable: {self.locale_gen}")
        locale_pattern = re.compile(
            rf"^\s*#?\s*{re.escape(config.locale)}\s+UTF-8\s*$", re.MULTILINE
        )
        if not locale_pattern.search(self.locale_gen.read_text()):
            raise InstallError(f"locale is unavailable: {config.locale}")
        if not Path(disk).is_block_device():
            raise InstallError(f"target is not a block device: {disk}")
        size_result = self.runner.run(
            ["blockdev", "--getsize64", disk], capture_output=True
        )
        try:
            disk_size = int(size_result.stdout.strip())
        except ValueError as error:
            raise InstallError(f"could not determine target disk size: {disk}") from error
        if disk_size < 16 * 1024**3:
            raise InstallError("target disk must be at least 16 GiB")
        target_mount = self.runner.run(
            ["findmnt", "--noheadings", "--mountpoint", str(self.target_root)],
            capture_output=True,
            check=False,
        )
        if target_mount.returncode == 0:
            raise InstallError(f"installation mount point is already in use: {self.target_root}")
        mounted = self.runner.run(
            ["lsblk", "--noheadings", "--raw", "--output", "MOUNTPOINTS", disk],
            capture_output=True,
            check=False,
        )
        if mounted.returncode == 0 and mounted.stdout.strip():
            raise InstallError(f"target disk or one of its partitions is mounted: {disk}")

    def _partition_disk(self, config: InstallConfig) -> tuple[str, str | None]:
        disk = config.disk
        self.runner.run(["wipefs", "--all", "--force", disk])
        self.runner.run(["parted", "--script", disk, "mklabel", "gpt"])
        if config.firmware == "uefi":
            self.runner.run(
                ["parted", "--script", disk, "mkpart", "ESP", "fat32", "1MiB", "1025MiB"]
            )
            self.runner.run(["parted", "--script", disk, "set", "1", "esp", "on"])
            self.runner.run(
                ["parted", "--script", disk, "mkpart", "root", "ext4", "1025MiB", "100%"]
            )
            root_partition = partition_path(disk, 2)
            boot_partition: str | None = partition_path(disk, 1)
        else:
            self.runner.run(
                ["parted", "--script", disk, "mkpart", "BIOSBOOT", "1MiB", "3MiB"]
            )
            self.runner.run(["parted", "--script", disk, "set", "1", "bios_grub", "on"])
            self.runner.run(
                ["parted", "--script", disk, "mkpart", "root", "ext4", "3MiB", "100%"]
            )
            root_partition = partition_path(disk, 2)
            boot_partition = None
        self.runner.run(["partprobe", disk])
        self.runner.run(["udevadm", "settle"])
        return root_partition, boot_partition

    def _format_and_mount(
        self, config: InstallConfig, root_partition: str, boot_partition: str | None
    ) -> None:
        self.runner.run(["mkfs.ext4", "-F", "-L", "protogenos", root_partition])
        self.target_root.mkdir(parents=True, exist_ok=True)
        self.runner.run(["mount", root_partition, str(self.target_root)])
        if config.firmware == "uefi":
            if boot_partition is None:
                raise InstallError("UEFI installation is missing an EFI partition")
            self.runner.run(["mkfs.fat", "-F", "32", "-n", "PROTOEFI", boot_partition])
            efi_path = self.target_root / "boot/efi"
            efi_path.mkdir(parents=True, exist_ok=True)
            self.runner.run(["mount", boot_partition, str(efi_path)])

    def _install_packages(self, plan: InstallPlan, config: InstallConfig) -> None:
        aur = set(plan.aur_packages)
        packages = [package for package in plan.packages if package not in aur]
        packages.extend(("grub", "sudo"))
        if config.firmware == "uefi":
            packages.append("efibootmgr")
        if plan.aur_packages:
            packages.extend(("base-devel", "git"))

        pacman_text = self.pacman_config.read_text()
        if plan.multilib_required:
            pacman_text = enable_multilib(pacman_text)
        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile("w", prefix="protogenos-pacman-", delete=False) as file:
                file.write(pacman_text)
                temporary_path = file.name
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

    def _write_fstab(self) -> None:
        result = self.runner.run(
            ["genfstab", "-U", str(self.target_root)], capture_output=True
        )
        self._write_target("etc/fstab", result.stdout)

    def _configure_system(self, plan: InstallPlan, config: InstallConfig) -> None:
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
        self._set_grub_branding()

        self._chroot("ln", "-sf", f"/usr/share/zoneinfo/{config.timezone}", "/etc/localtime")
        self._chroot("hwclock", "--systohc")
        self._chroot("locale-gen")
        useradd_args = ["useradd", "--create-home", "--shell", "/bin/bash"]
        if config.grant_sudo:
            useradd_args += ["--groups", "wheel"]
        useradd_args.append(config.username)
        self._chroot(*useradd_args)
        self._chroot(
            "chpasswd", input_text=f"{config.username}:{config.user_password}\n"
        )
        if config.grant_sudo:
            self._chroot("passwd", "--lock", "root")
            sudoers = self.target_root / "etc/sudoers.d/10-protogenos-wheel"
            sudoers.parent.mkdir(parents=True, exist_ok=True)
            sudoers.write_text("%wheel ALL=(ALL:ALL) ALL\n")
            sudoers.chmod(0o440)
        else:
            self._chroot(
                "chpasswd", input_text=f"root:{config.root_password}\n"
            )
        self._chroot("systemctl", "enable", "NetworkManager.service", "sddm.service")

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

    def _set_grub_branding(self) -> None:
        path = self.target_root / "etc/default/grub"
        content = path.read_text()
        assignment = 'GRUB_DISTRIBUTOR="protogenOS"'
        if re.search(r"^GRUB_DISTRIBUTOR=.*$", content, re.MULTILINE):
            content = re.sub(
                r"^GRUB_DISTRIBUTOR=.*$", assignment, content, flags=re.MULTILINE
            )
        else:
            content += f"\n{assignment}\n"
        path.write_text(content)

    def _install_aur_packages(self, plan: InstallPlan, config: InstallConfig) -> None:
        if not plan.aur_packages:
            return
        temporary_sudoers = self.target_root / "etc/sudoers.d/99-protogenos-aur"
        temporary_sudoers.write_text(
            f"{config.username} ALL=(ALL:ALL) NOPASSWD: ALL\n"
        )
        temporary_sudoers.chmod(0o440)
        try:
            for package in plan.aur_packages:
                build_path = f"/tmp/protogenos-aur-{package}"
                self.runner.run(
                    [
                        "arch-chroot",
                        str(self.target_root),
                        "runuser",
                        "--user",
                        config.username,
                        "--",
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        f"https://aur.archlinux.org/{package}.git",
                        build_path,
                    ]
                )
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
                        f"cd {shlex.quote(build_path)} && makepkg -si --noconfirm --needed",
                    ]
                )
        finally:
            temporary_sudoers.unlink(missing_ok=True)

    def _install_bootloader(self, config: InstallConfig) -> None:
        if config.firmware == "uefi":
            self._chroot(
                "grub-install",
                "--target=x86_64-efi",
                "--efi-directory=/boot/efi",
                "--bootloader-id=protogenOS",
                "--removable",
            )
        else:
            self._chroot("grub-install", "--target=i386-pc", config.disk)
        self._chroot("grub-mkconfig", "-o", "/boot/grub/grub.cfg")

    def _chroot(self, *args: str, input_text: str | None = None) -> None:
        self.runner.run(
            ["arch-chroot", str(self.target_root), *args], input_text=input_text
        )

    def _write_target(self, relative_path: str, content: str) -> None:
        path = self.target_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
