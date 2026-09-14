"""Detect CPU, GPU, and hypervisor details that decide extra target packages."""

from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    microcode: str | None = None
    gpu_vendors: tuple[str, ...] = ()
    hypervisor: str | None = None
    packages: tuple[str, ...] = ()
    services: tuple[str, ...] = field(default=())

    def describe(self) -> dict[str, object]:
        return {
            "microcode": self.microcode,
            "gpus": [_GPU_NAMES.get(vendor, vendor) for vendor in self.gpu_vendors],
            "hypervisor": self.hypervisor,
            "packages": list(self.packages),
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


def detect_gpu_vendors(pci_root: Path = Path("/sys/bus/pci/devices")) -> tuple[str, ...]:
    vendors: list[str] = []
    if not pci_root.is_dir():
        return ()
    for device in sorted(pci_root.iterdir()):
        try:
            device_class = (device / "class").read_text().strip()
            vendor = (device / "vendor").read_text().strip().lower()
        except OSError:
            continue
        # PCI base class 0x03 is "display controller" (VGA, 3D, other).
        if device_class.startswith("0x03") and vendor not in vendors:
            vendors.append(vendor)
    return tuple(vendors)


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
    gpu_vendors = detect_gpu_vendors(pci_root)
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
    )
