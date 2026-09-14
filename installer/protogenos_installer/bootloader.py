"""Kernel command lines, initramfs hooks, and bootloader configuration text."""

from __future__ import annotations

import re
import shlex

from .storage import LUKS_MAPPER

LIMINE_PACMAN_HOOK = """[Trigger]
Operation = Install
Operation = Upgrade
Type = Package
Target = limine

[Action]
Description = Deploying Limine after upgrade...
When = PostTransaction
Exec = /usr/bin/sh -c 'cp /usr/share/limine/BOOTX64.EFI /boot/EFI/limine/BOOTX64.EFI && cp /usr/share/limine/BOOTX64.EFI /boot/EFI/BOOT/BOOTX64.EFI'
"""


class BootConfigError(RuntimeError):
    """Raised when a boot configuration file can't be adjusted safely."""


_HOOKS_PATTERN = re.compile(r"^HOOKS=\((?P<hooks>[^)]*)\)", re.MULTILINE)


def uses_systemd_initramfs(mkinitcpio_conf: str) -> bool:
    match = _HOOKS_PATTERN.search(mkinitcpio_conf)
    return bool(match and "systemd" in match.group("hooks").split())


def add_encrypt_hook(mkinitcpio_conf: str) -> str:
    """Insert sd-encrypt (systemd initramfs) or encrypt before filesystems."""
    match = _HOOKS_PATTERN.search(mkinitcpio_conf)
    if match is None:
        raise BootConfigError("mkinitcpio.conf has no HOOKS=(...) line")
    hooks = match.group("hooks").split()
    encrypt_hook = "sd-encrypt" if "systemd" in hooks else "encrypt"
    if encrypt_hook in hooks:
        return mkinitcpio_conf
    if "keyboard" not in hooks:
        # The passphrase prompt needs keyboard drivers before autodetect
        # would otherwise drop them.
        position = hooks.index("autodetect") if "autodetect" in hooks else len(hooks)
        hooks.insert(position, "keyboard")
    position = hooks.index("filesystems") if "filesystems" in hooks else len(hooks)
    hooks.insert(position, encrypt_hook)
    return (
        mkinitcpio_conf[: match.start()]
        + f"HOOKS=({' '.join(hooks)})"
        + mkinitcpio_conf[match.end() :]
    )


def luks_kernel_parameters(luks_uuid: str, *, systemd_initramfs: bool) -> list[str]:
    if systemd_initramfs:
        return [f"rd.luks.name={luks_uuid}={LUKS_MAPPER}"]
    return [f"cryptdevice=UUID={luks_uuid}:{LUKS_MAPPER}"]


def kernel_cmdline(
    *,
    root_uuid: str,
    filesystem: str,
    btrfs_subvolumes: bool = True,
    luks_uuid: str = "",
    systemd_initramfs: bool = True,
    zram: bool = False,
    include_root: bool = True,
) -> str:
    """Full command line for loaders that don't generate one (systemd-boot, Limine).

    With include_root=False it returns only the extras GRUB needs on top of
    what grub-mkconfig derives itself (root=, rootflags=).
    """
    parameters: list[str] = []
    if luks_uuid:
        parameters += luks_kernel_parameters(luks_uuid, systemd_initramfs=systemd_initramfs)
    if include_root:
        if luks_uuid:
            parameters.append(f"root=/dev/mapper/{LUKS_MAPPER}")
        else:
            parameters.append(f"root=UUID={root_uuid}")
        if filesystem == "btrfs" and btrfs_subvolumes:
            parameters.append("rootflags=subvol=@")
        parameters.append("rw")
    if zram:
        # zswap in front of zram swap just double-compresses pages.
        parameters.append("zswap.enabled=0")
    return " ".join(parameters)


def set_shell_variable(text: str, key: str, value: str) -> str:
    """Set KEY="value" in a shell-style config such as /etc/default/grub."""
    assignment = f"{key}={shlex.quote(value) if value else chr(34) * 2}"
    pattern = re.compile(rf"^#?\s*{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(lambda _: assignment, text, count=1)
    return text.rstrip("\n") + f"\n{assignment}\n"


def systemd_boot_loader_conf(timeout: int = 3) -> str:
    return f"default protogenos.conf\ntimeout {timeout}\nconsole-mode max\neditor no\n"


def systemd_boot_entry(kernel: str, cmdline: str, *, fallback: bool = False) -> str:
    suffix = "-fallback" if fallback else ""
    title = "protogenOS (fallback initramfs)" if fallback else "protogenOS"
    return (
        f"title   {title}\n"
        f"linux   /vmlinuz-{kernel}\n"
        f"initrd  /initramfs-{kernel}{suffix}.img\n"
        f"options {cmdline}\n"
    )


def limine_conf(kernel: str, cmdline: str, *, fallback: bool = False, timeout: int = 3) -> str:
    entries = [("protogenOS", f"initramfs-{kernel}.img")]
    if fallback:
        entries.append(("protogenOS (fallback initramfs)", f"initramfs-{kernel}-fallback.img"))
    text = f"timeout: {timeout}\n"
    for title, initramfs in entries:
        text += (
            "\n"
            f"/{title}\n"
            "    protocol: linux\n"
            f"    path: boot():/vmlinuz-{kernel}\n"
            f"    cmdline: {cmdline}\n"
            f"    module_path: boot():/{initramfs}\n"
        )
    return text
