"""Which optional features to offer for a machine and install choices.

Shared by the text front ends; the web GUI mirrors these rules in
FeaturesScreen.tsx so it can also explain unavailable features.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .hardware import FeatureSupport, HardwareProfile


@dataclass(frozen=True, slots=True)
class FeatureOffer:
    key: str
    label: str
    detail: str
    default: bool


def offered_features(
    persona: str,
    *,
    filesystem: str,
    btrfs_subvolumes: bool,
    encrypt: bool,
    firmware: str,
    bootloader: str,
    hardware: HardwareProfile,
    support: FeatureSupport,
) -> list[FeatureOffer]:
    offers: list[FeatureOffer] = []
    if filesystem == "btrfs" and btrfs_subvolumes:
        rollback = ", bootable from GRUB" if bootloader == "grub" else ""
        offers.append(
            FeatureOffer("snapshots", "System snapshots", f"snapper before/after package changes{rollback}", True)
        )
    offers.append(FeatureOffer("flatpak", "Flatpak", "apps from Flathub, also in Discover", persona != "minimal"))
    offers.append(
        FeatureOffer("gaming_tweaks", "Gaming tweaks", "GameMode for all users, no split-lock slowdown", persona == "gamer")
    )
    if hardware.nvidia_open_supported:
        offers.append(
            FeatureOffer("nvidia_open", "NVIDIA open driver", "full performance and CUDA instead of nouveau", True)
        )
    if support.fingerprint_reader:
        offers.append(
            FeatureOffer("fingerprint", "Fingerprint login", "fprintd; enroll in System Settings later", True)
        )
    if encrypt and support.tpm2:
        offers.append(
            FeatureOffer("tpm2_unlock", "TPM disk unlock", "unlock without typing; passphrase stays as fallback", False)
        )
    if firmware == "uefi" and bootloader in {"systemd-boot", "limine"}:
        enroll = "enrolls keys now" if support.secure_boot_setup_mode else "enroll keys after install (not in Setup Mode)"
        offers.append(FeatureOffer("secure_boot", "Secure Boot", f"sbctl keys and signed boot files; {enroll}", False))
    return offers


def feature_settings(chosen: Iterable[str]) -> dict[str, object]:
    """Translate chosen offer keys into InstallConfig keyword arguments."""
    keys = set(chosen)
    return {
        "snapshots": "snapshots" in keys,
        "flatpak": "flatpak" in keys,
        "gaming_tweaks": "gaming_tweaks" in keys,
        "nvidia_driver": "nvidia-open" if "nvidia_open" in keys else "nouveau",
        "fingerprint": "fingerprint" in keys,
        "tpm2_unlock": "tpm2_unlock" in keys,
        "secure_boot": "secure_boot" in keys,
    }
