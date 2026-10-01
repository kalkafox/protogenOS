"""Checks run before the installer asks anything that could go to waste.

Each check looks at the live machine and reports ``ok``, ``warning`` (the
install can go ahead, but the user should know), or ``error`` (it cannot).
Everything is read from /sys and /proc so the checks are fast and safe.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .backend import DiskInfo, detect_firmware, format_size, list_install_disks
from .hardware import read_efi_flag
from .storage import GIB, MIN_ROOT_BYTES

OK = "ok"
WARNING = "warning"
ERROR = "error"

# Below this the live system, pacstrap, and the GUI compete for memory.
LOW_MEMORY_BYTES = 2 * GIB
LOW_BATTERY_PERCENT = 30
# Packages are signed with dates; a clock far in the past fails TLS and
# signature checks. The first protogenOS ISO was built in 2026.
EARLIEST_SANE_YEAR = 2026
PCI_CLASS_WIFI = "0x0280"
PCI_CLASS_RAID = "0x0104"
VENDOR_INTEL = "0x8086"


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    status: str
    title: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def check_memory(meminfo: Path = Path("/proc/meminfo")) -> Check:
    total = 0
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    if total and total < LOW_MEMORY_BYTES:
        return Check(
            "memory",
            WARNING,
            f"Only {format_size(total)} of memory",
            "Installing works, but slowly. Close other applications in the live session.",
        )
    return Check("memory", OK, f"{format_size(total)} of memory" if total else "Memory")


def check_disks(disks: Sequence[DiskInfo]) -> Check:
    usable = [disk for disk in disks if disk.size >= MIN_ROOT_BYTES]
    if not disks:
        return Check(
            "disks",
            ERROR,
            "No disk to install on",
            "If this computer has an NVMe or SATA drive, set the storage mode in the "
            "firmware settings to AHCI instead of RAID or Intel RST.",
        )
    if not usable:
        return Check(
            "disks",
            ERROR,
            f"Every disk is smaller than {MIN_ROOT_BYTES // GIB} GiB",
            ", ".join(f"{disk.path} ({format_size(disk.size)})" for disk in disks),
        )
    largest = max(usable, key=lambda disk: disk.size)
    count = len(usable)
    return Check(
        "disks",
        OK,
        f"{count} disk{'s' if count != 1 else ''} available",
        f"Largest: {largest.model} ({format_size(largest.size)})",
    )


def check_power(power_root: Path = Path("/sys/class/power_supply")) -> Check:
    batteries: list[int] = []
    on_ac = False
    try:
        supplies = sorted(power_root.iterdir())
    except OSError:
        supplies = []
    for supply in supplies:
        kind = _read(supply / "type")
        if kind == "Battery" and _read(supply / "scope") != "Device":
            capacity = _read(supply / "capacity")
            if capacity.isdigit():
                batteries.append(int(capacity))
        elif kind in {"Mains", "USB"} and _read(supply / "online") == "1":
            on_ac = True
    if not batteries or on_ac:
        return Check("power", OK, "Connected to power" if batteries else "No battery")
    level = min(batteries)
    if level < LOW_BATTERY_PERCENT:
        return Check(
            "power",
            WARNING,
            f"Battery at {level}%",
            "Plug in the charger: running out of power while installing leaves an unbootable system.",
        )
    return Check(
        "power",
        WARNING,
        f"Running on battery ({level}%)",
        "Plugging in the charger is safer during installation.",
    )


def check_firmware(firmware: str, secure_boot_enabled: bool) -> Check:
    if firmware == "bios":
        return Check(
            "firmware",
            WARNING,
            "Started in legacy BIOS mode",
            "If this computer supports UEFI, restart and choose the UEFI entry of the USB "
            "drive in the boot menu; Secure Boot, systemd-boot, and Limine need it.",
        )
    if secure_boot_enabled:
        return Check(
            "firmware",
            WARNING,
            "Secure Boot is on",
            "Choose the Secure Boot extra later, or turn Secure Boot off in the firmware "
            "settings; otherwise the installed system won't start.",
        )
    return Check("firmware", OK, "UEFI firmware")


def check_clock(now: Callable[[], float] = time.time) -> Check:
    year = time.gmtime(now()).tm_year
    if year < EARLIEST_SANE_YEAR:
        return Check(
            "clock",
            WARNING,
            f"The clock says {year}",
            "Package downloads may fail their security checks. Connecting to the internet "
            "usually corrects the time; otherwise fix it in the firmware settings.",
        )
    return Check("clock", OK, "Clock is set")


def check_devices(pci_root: Path = Path("/sys/bus/pci/devices")) -> list[Check]:
    """Wi-Fi adapters without a driver, and Intel RAID/RST storage mode."""
    checks: list[Check] = []
    try:
        devices = sorted(pci_root.iterdir())
    except OSError:
        return checks
    for device in devices:
        device_class = _read(device / "class")[:6]
        vendor = _read(device / "vendor")
        if device_class == PCI_CLASS_WIFI and not (device / "driver").exists():
            checks.append(
                Check(
                    "wifi-driver",
                    WARNING,
                    "A Wi-Fi adapter has no driver",
                    f"PCI device {device.name} ({vendor}:{_read(device / 'device')}). Use a "
                    "wired connection or a USB Wi-Fi adapter to install.",
                )
            )
        if device_class == PCI_CLASS_RAID and vendor == VENDOR_INTEL:
            checks.append(
                Check(
                    "raid-mode",
                    WARNING,
                    "Storage is in RAID / Intel RST mode",
                    "Linux may not see the drives. If no disk shows up, set the storage mode "
                    "to AHCI in the firmware settings (Windows needs to be prepared first).",
                )
            )
    return checks


def run_checks(
    *,
    disks: Callable[[], Sequence[DiskInfo]] = list_install_disks,
    firmware: Callable[[], str] = detect_firmware,
    secure_boot: Callable[[], bool] = lambda: bool(read_efi_flag("SecureBoot")),
    meminfo: Path = Path("/proc/meminfo"),
    power_root: Path = Path("/sys/class/power_supply"),
    pci_root: Path = Path("/sys/bus/pci/devices"),
    now: Callable[[], float] = time.time,
) -> list[Check]:
    checks = [
        check_disks(disks()),
        check_memory(meminfo),
        check_power(power_root),
        check_firmware(firmware(), secure_boot()),
        check_clock(now),
    ]
    checks.extend(check_devices(pci_root))
    return checks


def describe_problems(checks: Sequence[Check]) -> str:
    """Plain-text warnings and errors for the text installers; empty if none."""
    lines: list[str] = []
    for check in sorted(checks, key=lambda item: item.status != ERROR):
        if check.status == OK:
            continue
        label = "Problem" if check.status == ERROR else "Warning"
        lines.append(f"{label}: {check.title}")
        if check.detail:
            lines.append(f"  {check.detail}")
    return "\n".join(lines)


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""
