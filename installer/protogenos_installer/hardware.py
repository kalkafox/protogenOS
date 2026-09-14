"""Detect CPU, GPU, and hypervisor details that decide extra target packages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

VENDOR_INTEL = "0x8086"
VENDOR_AMD = "0x1002"
VENDOR_NVIDIA = "0x10de"

# Open-source Mesa/Vulkan stacks only: they work out of the box on every
# supported GPU generation, unlike the proprietary NVIDIA driver.
_GPU_PACKAGES = {
    VENDOR_INTEL: ("vulkan-intel", "intel-media-driver"),
    VENDOR_AMD: ("vulkan-radeon",),
    VENDOR_NVIDIA: ("vulkan-nouveau",),
}
_GPU_MULTILIB_PACKAGES = {
    VENDOR_INTEL: ("lib32-vulkan-intel",),
    VENDOR_AMD: ("lib32-vulkan-radeon",),
    VENDOR_NVIDIA: ("lib32-vulkan-nouveau",),
}
_GPU_NAMES = {VENDOR_INTEL: "Intel", VENDOR_AMD: "AMD", VENDOR_NVIDIA: "NVIDIA"}

# nvidia-open supports Turing (GTX 16xx / RTX 20xx) and newer. NVIDIA PCI
# device IDs grow with each generation; Turing starts at 0x1e00.
NVIDIA_TURING_FIRST_DEVICE = 0x1E00

# Installed by systemd; lists the USB IDs of every libfprint-supported reader.
FINGERPRINT_HWDB = Path("/usr/lib/udev/hwdb.d/60-autosuspend-fingerprint-reader.hwdb")
EFI_GLOBAL_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    microcode: str | None = None
    gpu_vendors: tuple[str, ...] = ()
    hypervisor: str | None = None
    packages: tuple[str, ...] = ()
    services: tuple[str, ...] = field(default=())
    # Set only when an NVIDIA GPU is present: True for Turing and newer.
    nvidia_open_supported: bool | None = None

    def describe(self) -> dict[str, object]:
        return {
            "microcode": self.microcode,
            "gpus": [_GPU_NAMES.get(vendor, vendor) for vendor in self.gpu_vendors],
            "hypervisor": self.hypervisor,
            "packages": list(self.packages),
            "nvidia_open_supported": self.nvidia_open_supported,
        }


@dataclass(frozen=True, slots=True)
class FeatureSupport:
    """Optional features the installer offers only when the machine can use them."""

    tpm2: bool = False
    fingerprint_reader: bool = False
    # None on BIOS systems; otherwise whether firmware accepts new keys now.
    secure_boot_setup_mode: bool | None = None
    secure_boot_enabled: bool | None = None

    def describe(self) -> dict[str, object]:
        return {
            "tpm2": self.tpm2,
            "fingerprint_reader": self.fingerprint_reader,
            "secure_boot_setup_mode": self.secure_boot_setup_mode,
            "secure_boot_enabled": self.secure_boot_enabled,
        }


def detect_microcode(cpuinfo: Path = Path("/proc/cpuinfo")) -> str | None:
    try:
        text = cpuinfo.read_text()
    except OSError:
        return None
    if "GenuineIntel" in text:
        return "intel-ucode"
    if "AuthenticAMD" in text:
        return "amd-ucode"
    return None


def detect_gpus(pci_root: Path = Path("/sys/bus/pci/devices")) -> tuple[tuple[str, int], ...]:
    """Return (vendor, device ID) for every display controller."""
    gpus: list[tuple[str, int]] = []
    if not pci_root.is_dir():
        return ()
    for device in sorted(pci_root.iterdir()):
        try:
            device_class = (device / "class").read_text().strip()
            vendor = (device / "vendor").read_text().strip().lower()
        except OSError:
            continue
        # PCI base class 0x03 is "display controller" (VGA, 3D, other).
        if not device_class.startswith("0x03"):
            continue
        try:
            device_id = int((device / "device").read_text().strip(), 16)
        except (OSError, ValueError):
            device_id = 0
        gpus.append((vendor, device_id))
    return tuple(gpus)


def detect_gpu_vendors(pci_root: Path = Path("/sys/bus/pci/devices")) -> tuple[str, ...]:
    return tuple(dict.fromkeys(vendor for vendor, _ in detect_gpus(pci_root)))


def detect_tpm2(tpm_root: Path = Path("/sys/class/tpm")) -> bool:
    try:
        devices = sorted(tpm_root.iterdir())
    except OSError:
        return False
    for device in devices:
        try:
            if (device / "tpm_version_major").read_text().strip() == "2":
                return True
        except OSError:
            continue
    return False


def _fingerprint_ids(hwdb: Path) -> set[tuple[str, str]]:
    try:
        text = hwdb.read_text()
    except OSError:
        return set()
    ids: set[tuple[str, str]] = set()
    for match in re.finditer(r"^usb:v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4})", text, re.MULTILINE):
        ids.add((match.group(1).lower(), match.group(2).lower()))
    return ids


def detect_fingerprint_reader(
    usb_root: Path = Path("/sys/bus/usb/devices"), hwdb: Path = FINGERPRINT_HWDB
) -> bool:
    supported = _fingerprint_ids(hwdb)
    if not supported:
        return False
    try:
        devices = sorted(usb_root.iterdir())
    except OSError:
        return False
    for device in devices:
        try:
            vendor = (device / "idVendor").read_text().strip().lower()
            product = (device / "idProduct").read_text().strip().lower()
        except OSError:
            continue
        if (vendor, product) in supported:
            return True
    return False


def read_efi_flag(name: str, efivars: Path = Path("/sys/firmware/efi/efivars")) -> bool | None:
    """Read a one-byte UEFI global variable such as SecureBoot or SetupMode."""
    try:
        data = (efivars / f"{name}-{EFI_GLOBAL_GUID}").read_bytes()
    except OSError:
        return None
    # efivarfs prefixes the value with 4 bytes of attributes.
    return len(data) >= 5 and data[4] == 1


def detect_features(
    *,
    tpm_root: Path = Path("/sys/class/tpm"),
    usb_root: Path = Path("/sys/bus/usb/devices"),
    hwdb: Path = FINGERPRINT_HWDB,
    efivars: Path = Path("/sys/firmware/efi/efivars"),
) -> FeatureSupport:
    uefi = efivars.is_dir()
    return FeatureSupport(
        tpm2=detect_tpm2(tpm_root),
        fingerprint_reader=detect_fingerprint_reader(usb_root, hwdb),
        secure_boot_setup_mode=bool(read_efi_flag("SetupMode", efivars)) if uefi else None,
        secure_boot_enabled=bool(read_efi_flag("SecureBoot", efivars)) if uefi else None,
    )


def detect_hypervisor(dmi_root: Path = Path("/sys/class/dmi/id")) -> str | None:
    def read(name: str) -> str:
        try:
            return (dmi_root / name).read_text().strip()
        except OSError:
            return ""

    vendor = read("sys_vendor")
    product = read("product_name")
    if vendor == "QEMU" or "KVM" in product or product.startswith("Standard PC"):
        return "qemu"
    if vendor.startswith("VMware"):
        return "vmware"
    if vendor == "innotek GmbH" or product == "VirtualBox":
        return "virtualbox"
    if vendor == "Microsoft Corporation" and product == "Virtual Machine":
        return "hyperv"
    return None


def detect_hardware(
    *,
    multilib: bool = False,
    desktop: bool = True,
    cpuinfo: Path = Path("/proc/cpuinfo"),
    pci_root: Path = Path("/sys/bus/pci/devices"),
    dmi_root: Path = Path("/sys/class/dmi/id"),
) -> HardwareProfile:
    microcode = detect_microcode(cpuinfo)
    gpus = detect_gpus(pci_root)
    gpu_vendors = tuple(dict.fromkeys(vendor for vendor, _ in gpus))
    nvidia_devices = [device for vendor, device in gpus if vendor == VENDOR_NVIDIA]
    hypervisor = detect_hypervisor(dmi_root)

    # Console-only installs skip graphics stacks and GUI guest helpers.
    packages: list[str] = ["mesa"] if desktop else []
    services: list[str] = []
    if multilib and desktop:
        packages.append("lib32-mesa")
    if microcode:
        packages.append(microcode)
    for vendor in gpu_vendors if desktop else ():
        packages.extend(_GPU_PACKAGES.get(vendor, ()))
        if multilib:
            packages.extend(_GPU_MULTILIB_PACKAGES.get(vendor, ()))
    if hypervisor == "qemu":
        packages.append("qemu-guest-agent")
        if desktop:
            packages.append("spice-vdagent")
    elif hypervisor == "vmware":
        packages.append("open-vm-tools")
        if desktop:
            packages.append("gtkmm3")
        services.append("vmtoolsd.service")
    elif hypervisor == "virtualbox":
        packages.append("virtualbox-guest-utils" if desktop else "virtualbox-guest-utils-nox")
        services.append("vboxservice.service")
    elif hypervisor == "hyperv":
        packages.append("hyperv")

    return HardwareProfile(
        microcode=microcode,
        gpu_vendors=gpu_vendors,
        hypervisor=hypervisor,
        packages=tuple(dict.fromkeys(packages)),
        services=tuple(services),
        nvidia_open_supported=(
            any(device >= NVIDIA_TURING_FIRST_DEVICE for device in nvidia_devices)
            if nvidia_devices
            else None
        ),
    )
