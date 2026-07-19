# Installer Architecture

`protogenos-install` is a Python wizard backed by standard Arch installation
tools. It first resolves the General Use, Gamer, or Developer profile and all
application choices into a package plan. Non-interactive mode stops there and
is safe for CI or plan inspection.

## Interactive Installation

By default the installer runs as a curses TUI: arrow keys move between
options, Space toggles checkboxes on multi-select groups (e.g. browsers),
Enter confirms a screen, and Esc/`q` asks for confirmation before quitting.
Pass `--lo-fi` to fall back to the original plain numbered prompts (also used
automatically when stdout/stdin aren't a real terminal, e.g. piped output or
`--non-interactive`).

After reviewing the plan, the user can select an unused writable disk and enter
a hostname, user name, locale, timezone, and password. Disks are listed with a
green "safe to use" tag when they have no partitions and a red "will be
erased" warning when they already contain partitions; selecting a disk shows a
matching confirmation before continuing. Press `c` on the disk list to drop
into `cfdisk` for manual partition management and return to the list
afterward. The backend validates the environment before changing the disk and
requires the exact confirmation `ERASE /dev/device`.

The complete selected disk is repartitioned as GPT:

| Boot mode | Partition 1 | Partition 2 |
| --- | --- | --- |
| UEFI | 1 GiB FAT32 EFI System Partition | ext4 root using remaining space |
| BIOS | 2 MiB BIOS boot partition | ext4 root using remaining space |

The backend uses `pacstrap` and `genfstab`, configures locale and timezone,
and enables NetworkManager and SDDM. It always creates the administrative
account, but sudo access is a yes/no prompt (default yes): granting it adds
the user to `wheel`, installs a `%wheel ALL=(ALL:ALL) ALL` sudoers rule, and
locks direct root login; declining it leaves root unlocked and prompts for a
separate root password instead, since a system with neither sudo nor a root
login would be unusable. GRUB is installed as the bootloader. Selected AUR
packages are cloned and built as the new unprivileged user; a temporary sudo
rule is removed afterward. Multilib is enabled only when the selected package
set requires it.

## Safety and Limitations

The live medium and mounted disks are excluded from the target list. The
backend refuses a non-block device, an already-mounted target, a busy `/mnt`, or
missing installation tools. Mounts are recursively cleaned up after success or
failure.

This first backend is intentionally whole-disk only. It does not yet support
dual boot, preserving partitions, LUKS encryption, LVM, RAID, Secure Boot,
manual mount layouts, alternate filesystems, or unattended disk installation.
Use a disposable VM disk while developing. Unit tests mock every destructive
command; QEMU is used for end-to-end validation.
