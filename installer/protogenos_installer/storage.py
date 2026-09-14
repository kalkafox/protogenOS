"""Disk layouts, LUKS encryption, filesystems, and target mounts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .backend import CommandRunner, InstallConfig

MIB = 1024**2
GIB = 1024**3
ESP_SIZE_MIB = 1024
BOOT_SIZE_MIB = 1024
MIN_ROOT_BYTES = 16 * GIB
LUKS_MAPPER = "cryptroot"
BTRFS_MOUNT_OPTIONS = "compress=zstd:1,noatime"
# (subvolume, mount point relative to the target root)
BTRFS_SUBVOLUMES = (
    ("@", ""),
    ("@home", "home"),
    ("@log", "var/log"),
    ("@pkg", "var/cache/pacman/pkg"),
    ("@snapshots", ".snapshots"),
)
FILESYSTEM_TOOLS = {
    "btrfs": ("mkfs.btrfs", "btrfs"),
    "ext4": ("mkfs.ext4",),
    "xfs": ("mkfs.xfs",),
    "f2fs": ("mkfs.f2fs",),
}
FILESYSTEM_PACKAGES = {
    "btrfs": "btrfs-progs",
    "ext4": "e2fsprogs",
    "xfs": "xfsprogs",
    "f2fs": "f2fs-tools",
}


class StorageError(RuntimeError):
    """Raised when the requested disk layout cannot be applied safely."""


def partition_path(disk: str, number: int) -> str:
    separator = "p" if Path(disk).name[-1].isdigit() else ""
    return f"{disk}{separator}{number}"


def needs_separate_boot(config: InstallConfig) -> bool:
    """BIOS GRUB can't read LUKS2 (argon2) volumes or feature-rich f2fs."""
    return config.firmware == "bios" and (config.encrypt or config.filesystem == "f2fs")


@dataclass(frozen=True, slots=True)
class PartitionInfo:
    number: int
    path: str
    start: int
    end: int
    size: int
    fstype: str = ""
    label: str = ""
    type_name: str = ""
    mountpoints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["mountpoints"] = list(self.mountpoints)
        return data


@dataclass(frozen=True, slots=True)
class FreeRegion:
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class DiskLayout:
    path: str
    size: int
    table: str | None
    sector_size: int = 512
    partitions: tuple[PartitionInfo, ...] = ()
    free_regions: tuple[FreeRegion, ...] = ()

    @property
    def largest_free(self) -> FreeRegion | None:
        return max(self.free_regions, key=lambda region: region.size, default=None)

    def partition(self, path: str) -> PartitionInfo | None:
        return next((part for part in self.partitions if part.path == path), None)

    def to_dict(self) -> dict[str, object]:
        largest = self.largest_free
        return {
            "path": self.path,
            "size": self.size,
            "table": self.table,
            "partitions": [part.to_dict() for part in self.partitions],
            "largest_free": largest.size if largest else 0,
        }


def _bytes(value: str) -> int:
    return int(value.rstrip("B"))


def parse_disk_layout(disk: str, parted_output: str, lsblk_output: str) -> DiskLayout:
    """Combine `parted -m unit B print free` with lsblk filesystem details."""
    details: dict[int, dict[str, object]] = {}
    try:
        tree = json.loads(lsblk_output) if lsblk_output.strip() else {}
    except json.JSONDecodeError:
        tree = {}
    if not isinstance(tree, dict):
        tree = {}
    # lsblk nests partitions under the disk as "children" when it prints a
    # tree, but returns a flat list when the output has no NAME column.
    pending = list(tree.get("blockdevices") or [])
    while pending:
        device = pending.pop()
        if not isinstance(device, dict):
            continue
        pending.extend(device.get("children") or [])
        number = device.get("partn")
        if isinstance(number, int):
            details[number] = device

    size = 0
    table: str | None = None
    sector_size = 512
    partitions: list[PartitionInfo] = []
    free: list[FreeRegion] = []
    for raw_line in parted_output.splitlines():
        line = raw_line.strip().rstrip(";")
        fields = line.split(":")
        if line == "BYT" or len(fields) < 4:
            continue
        if fields[0] == disk:
            size = _bytes(fields[1])
            sector_size = int(fields[3]) if fields[3].isdigit() else 512
            table = fields[5] if len(fields) > 5 and fields[5] not in {"unknown", "loop"} else None
            continue
        if not fields[0].isdigit():
            continue
        start, end = _bytes(fields[1]), _bytes(fields[2])
        if len(fields) >= 5 and fields[4] == "free":
            # Align to whole MiB; partitions must start/end on those bounds.
            aligned_start = max(MIB, -(-start // MIB) * MIB)
            aligned_end = ((end + 1) // MIB) * MIB
            if aligned_end - aligned_start >= MIB:
                free.append(FreeRegion(aligned_start, aligned_end))
            continue
        number = int(fields[0])
        info = details.get(number, {})
        partitions.append(
            PartitionInfo(
                number=number,
                path=str(info.get("path") or partition_path(disk, number)),
                start=start,
                end=end,
                size=end - start + 1,
                fstype=str(info.get("fstype") or ""),
                label=str(info.get("label") or info.get("partlabel") or ""),
                type_name=str(info.get("parttypename") or ""),
                mountpoints=tuple(str(item) for item in info.get("mountpoints") or () if item),
            )
        )
    if table is None and size and not partitions:
        free = [FreeRegion(MIB, (size // MIB - 1) * MIB)]
    return DiskLayout(disk, size, table, sector_size, tuple(partitions), tuple(free))


def read_disk_layout(runner: CommandRunner, disk: str) -> DiskLayout:
    parted = runner.run(
        ["parted", "--script", "--machine", disk, "unit", "B", "print", "free"],
        capture_output=True,
        check=False,
    )
    lsblk = runner.run(
        [
            "lsblk",
            "--json",
            "--tree",
            "--bytes",
            "--output",
            "PATH,PARTN,FSTYPE,LABEL,PARTLABEL,PARTTYPENAME,MOUNTPOINTS",
            disk,
        ],
        capture_output=True,
        check=False,
    )
    return parse_disk_layout(disk, parted.stdout or "", lsblk.stdout or "")


@dataclass(slots=True)
class PreparedStorage:
    root_partition: str
    root_device: str
    boot_partition: str | None = None
    boot_is_esp: bool = False
    esp_number: int | None = None
    luks_uuid: str = ""
    root_uuid: str = ""


class StorageManager:
    def __init__(self, runner: CommandRunner, target_root: Path, *, dry_run: bool = False) -> None:
        self.runner = runner
        self.target_root = target_root
        self.dry_run = dry_run
        self.mount_attempted = False
        self.luks_opened = False

    # -- public -----------------------------------------------------------

    def prepare(self, config: InstallConfig) -> PreparedStorage:
        if config.disk_layout == "erase":
            root, boot, esp_number = self._erase_layout(config)
            format_boot = boot is not None
        elif config.disk_layout == "free-space":
            root, boot, esp_number = self._free_space_layout(config)
            format_boot = True
        else:
            root, boot, esp_number = self._existing_layout(config)
            format_boot = config.format_boot

        prepared = PreparedStorage(
            root_partition=root,
            root_device=root,
            boot_partition=boot,
            boot_is_esp=config.firmware == "uefi",
            esp_number=esp_number,
        )
        if config.encrypt:
            prepared.root_device = self._encrypt(root, config.encryption_passphrase or "")
            prepared.luks_uuid = self._capture(["cryptsetup", "luksUUID", root])
        self._format_root(config.filesystem, prepared.root_device)
        self._mount_root(config.filesystem, prepared.root_device)
        if boot is not None:
            self._prepare_boot(boot, esp=prepared.boot_is_esp, format_boot=format_boot)
        prepared.root_uuid = self._capture(
            ["blkid", "--match-tag", "UUID", "--output", "value", prepared.root_device]
        )
        return prepared

    def teardown(self) -> None:
        if self.mount_attempted:
            self.runner.run(["umount", "--recursive", str(self.target_root)], check=False)
        if self.luks_opened:
            self.runner.run(["cryptsetup", "close", LUKS_MAPPER], check=False)

    # -- layouts ----------------------------------------------------------

    def _parted(self, disk: str, *args: str) -> None:
        self.runner.run(["parted", "--script", disk, *args])

    def _settle(self, disk: str) -> None:
        self.runner.run(["partprobe", disk])
        self.runner.run(["udevadm", "settle"])

    def _erase_layout(self, config: InstallConfig) -> tuple[str, str | None, int | None]:
        disk = config.disk
        self.runner.run(["wipefs", "--all", "--force", disk])
        self._parted(disk, "mklabel", "gpt")
        if config.firmware == "uefi":
            self._parted(disk, "mkpart", "ESP", "fat32", "1MiB", f"{1 + ESP_SIZE_MIB}MiB")
            self._parted(disk, "set", "1", "esp", "on")
            self._parted(disk, "mkpart", "root", f"{1 + ESP_SIZE_MIB}MiB", "100%")
            self._settle(disk)
            return partition_path(disk, 2), partition_path(disk, 1), 1

        self._parted(disk, "mkpart", "BIOSBOOT", "1MiB", "3MiB")
        self._parted(disk, "set", "1", "bios_grub", "on")
        if needs_separate_boot(config):
            self._parted(disk, "mkpart", "boot", "3MiB", f"{3 + BOOT_SIZE_MIB}MiB")
            self._parted(disk, "mkpart", "root", f"{3 + BOOT_SIZE_MIB}MiB", "100%")
            self._settle(disk)
            return partition_path(disk, 3), partition_path(disk, 2), None
        self._parted(disk, "mkpart", "root", "3MiB", "100%")
        self._settle(disk)
        return partition_path(disk, 2), None, None

    def _free_space_layout(self, config: InstallConfig) -> tuple[str, str | None, int | None]:
        disk = config.disk
        if self.dry_run:
            self.runner.emit("  (dry-run: assuming free space after existing partitions)")
            return partition_path(disk, 4), partition_path(disk, 3), 3
        layout = read_disk_layout(self.runner, disk)
        if layout.table != "gpt":
            raise StorageError("installing into free space requires a GPT partition table")
        region = layout.largest_free
        if region is None or region.size < MIN_ROOT_BYTES + ESP_SIZE_MIB * MIB:
            raise StorageError(
                f"{disk} needs at least {(MIN_ROOT_BYTES // GIB) + 1} GiB of unallocated space"
            )
        sector = layout.sector_size
        esp_start = region.start
        root_start = esp_start + ESP_SIZE_MIB * MIB
        # Exact sector bounds: MiB end values get fuzzy rounding in parted
        # and can collide with the following partition.
        self._parted(
            disk, "mkpart", "ESP", "fat32", f"{esp_start // sector}s", f"{root_start // sector - 1}s"
        )
        self._parted(
            disk, "mkpart", "root", f"{root_start // sector}s", f"{region.end // sector - 1}s"
        )
        self._settle(disk)
        created = read_disk_layout(self.runner, disk)
        esp = next((part for part in created.partitions if part.start == esp_start), None)
        root = next((part for part in created.partitions if part.start == root_start), None)
        if esp is None or root is None:
            raise StorageError("could not find the newly created partitions")
        self._parted(disk, "set", str(esp.number), "esp", "on")
        self.runner.run(["udevadm", "settle"])
        return root.path, esp.path, esp.number

    def _existing_layout(self, config: InstallConfig) -> tuple[str, str | None, int | None]:
        root = config.root_partition or ""
        boot = config.boot_partition
        esp_number: int | None = None
        if not self.dry_run:
            layout = read_disk_layout(self.runner, config.disk)
            root_info = layout.partition(root)
            if root_info is None:
                raise StorageError(f"{root} is not a partition on {config.disk}")
            if root_info.size < MIN_ROOT_BYTES:
                raise StorageError(f"{root} must be at least {MIN_ROOT_BYTES // GIB} GiB")
            if root_info.mountpoints:
                raise StorageError(f"{root} is mounted")
            if boot:
                boot_info = layout.partition(boot)
                if boot_info is None:
                    raise StorageError(f"{boot} is not a partition on {config.disk}")
                if boot_info.mountpoints:
                    raise StorageError(f"{boot} is mounted")
                if not config.format_boot and boot_info.fstype != "vfat":
                    raise StorageError(f"{boot} is not a FAT32 EFI system partition")
                esp_number = boot_info.number
        self.runner.run(["wipefs", "--all", "--force", root])
        if boot and config.format_boot and esp_number is not None:
            self._parted(config.disk, "set", str(esp_number), "esp", "on")
        return root, boot, esp_number

    # -- encryption and filesystems ---------------------------------------

    def _encrypt(self, partition: str, passphrase: str) -> str:
        # --key-file=- reads the passphrase verbatim from stdin: nothing lands
        # in the process list or the log, and no trailing newline is added.
        self.runner.run(
            [
                "cryptsetup",
                "luksFormat",
                "--type",
                "luks2",
                "--batch-mode",
                "--label",
                "protogenos-luks",
                "--key-file=-",
                partition,
            ],
            input_text=passphrase,
        )
        self.runner.run(
            ["cryptsetup", "open", "--key-file=-", partition, LUKS_MAPPER], input_text=passphrase
        )
        self.luks_opened = True
        return f"/dev/mapper/{LUKS_MAPPER}"

    def _format_root(self, filesystem: str, device: str) -> None:
        commands = {
            "btrfs": ["mkfs.btrfs", "-f", "-L", "protogenos", device],
            "ext4": ["mkfs.ext4", "-F", "-L", "protogenos", device],
            "xfs": ["mkfs.xfs", "-f", "-L", "protogenos", device],
            "f2fs": ["mkfs.f2fs", "-f", "-l", "protogenos", "-O", "extra_attr,inode_checksum,sb_checksum", device],
        }
        self.runner.run(commands[filesystem])
        # Explicit types below: right after mkfs, blkid/udev may not have
        # probed the new filesystem yet and mount's autodetection guesses wrong.
        self.runner.run(["udevadm", "settle"])

    def _mount(self, device: str, relative: str, fstype: str, options: str | None = None) -> None:
        mount_point = self.target_root / relative if relative else self.target_root
        mount_point.mkdir(parents=True, exist_ok=True)
        command = ["mount", "-t", fstype]
        if options:
            command += ["-o", options]
        self.mount_attempted = True
        self.runner.run([*command, device, str(mount_point)])

    def _mount_root(self, filesystem: str, device: str) -> None:
        if filesystem != "btrfs":
            self._mount(device, "", filesystem, "noatime")
            return
        self._mount(device, "", "btrfs")
        for subvolume, _ in BTRFS_SUBVOLUMES:
            self.runner.run(["btrfs", "subvolume", "create", str(self.target_root / subvolume)])
        self.runner.run(["umount", str(self.target_root)])
        for subvolume, relative in BTRFS_SUBVOLUMES:
            self._mount(device, relative, "btrfs", f"subvol={subvolume},{BTRFS_MOUNT_OPTIONS}")

    def _prepare_boot(self, partition: str, *, esp: bool, format_boot: bool) -> None:
        if esp:
            if format_boot:
                self.runner.run(["mkfs.fat", "-F", "32", "-n", "PROTOEFI", partition])
                self.runner.run(["udevadm", "settle"])
            # umask keeps the random seed / loader files private to root.
            self._mount(partition, "boot", "vfat", "umask=0077")
        else:
            self.runner.run(["mkfs.ext4", "-F", "-L", "protoboot", partition])
            self.runner.run(["udevadm", "settle"])
            self._mount(partition, "boot", "ext4", "noatime")

    def _capture(self, args: list[str]) -> str:
        try:
            result = self.runner.run(args, capture_output=True)
        except subprocess.CalledProcessError as error:
            raise StorageError(f"{args[0]} failed: {error}") from error
        return (result.stdout or "").strip()
