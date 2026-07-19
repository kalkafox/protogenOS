import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from protogenos_installer.backend import (
    CommandRunner,
    InstallConfig,
    InstallError,
    InstallerBackend,
    enable_multilib,
    list_install_disks,
    partition_path,
)
from protogenos_installer.cli import _choose_disk
from protogenos_installer.models import InstallPlan


class FakeRunner(CommandRunner):
    def __init__(self, lsblk_data: dict[str, object] | None = None) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.inputs: list[str | None] = []
        self.lsblk_data = lsblk_data

    def run(
        self,
        args: list[str] | tuple[str, ...],
        *,
        input_text: str | None = None,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        self.commands.append(command)
        self.inputs.append(input_text)
        if command[0] == "lsblk" and "--json" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.lsblk_data), "")
        if command[0] == "genfstab":
            return subprocess.CompletedProcess(
                command, 0, "UUID=root / ext4 rw,relatime 0 1\n", ""
            )
        if command[0] == "pacstrap":
            target = Path(command[command.index("-C") + 2])
            (target / "etc/default").mkdir(parents=True, exist_ok=True)
            (target / "etc/locale.gen").write_text("#en_US.UTF-8 UTF-8\n")
            (target / "etc/default/grub").write_text('GRUB_TIMEOUT=5\n')
            (target / "usr/lib").mkdir(parents=True, exist_ok=True)
            (target / "etc/sudoers.d").mkdir(parents=True, exist_ok=True)
        if command[0] == "findmnt":
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class FakeInstallerBackend(InstallerBackend):
    def _validate_environment(self, config: InstallConfig) -> None:
        return


class InstallerBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "target"
        self.zoneinfo = self.root / "zoneinfo"
        self.zoneinfo.mkdir()
        (self.zoneinfo / "UTC").write_text("UTC")
        self.pacman_config = self.root / "pacman.conf"
        self.pacman_config.write_text(
            "[options]\n#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n"
        )
        self.locale_gen = self.root / "locale.gen"
        self.locale_gen.write_text("#en_US.UTF-8 UTF-8\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _plan(self, *, aur: tuple[str, ...] = (), multilib: bool = False) -> InstallPlan:
        return InstallPlan(
            persona="general",
            packages=("base", "linux", "linux-firmware", "networkmanager", "sddm", *aur),
            selections={"kernel": ("linux",)},
            aur_packages=aur,
            multilib_required=multilib,
        )

    def _config(self, firmware: str = "uefi") -> InstallConfig:
        return InstallConfig(
            disk="/dev/nvme0n1",
            firmware=firmware,
            hostname="proto-box",
            username="fox",
            user_password="correct horse battery staple",
        )

    def _backend(self, runner: FakeRunner) -> FakeInstallerBackend:
        return FakeInstallerBackend(
            runner,
            target_root=self.target,
            pacman_config=self.pacman_config,
            zoneinfo_root=self.zoneinfo,
            locale_gen=self.locale_gen,
            require_root=False,
        )

    def test_partition_paths_cover_common_device_names(self) -> None:
        self.assertEqual(partition_path("/dev/sda", 2), "/dev/sda2")
        self.assertEqual(partition_path("/dev/nvme0n1", 2), "/dev/nvme0n1p2")
        self.assertEqual(partition_path("/dev/mmcblk0", 2), "/dev/mmcblk0p2")

    def test_disk_listing_excludes_the_live_media(self) -> None:
        runner = FakeRunner(
            {
                "blockdevices": [
                    {
                        "path": "/dev/sda",
                        "size": 8_000_000_000,
                        "type": "disk",
                        "model": "Live USB",
                        "ro": False,
                        "rm": True,
                        "mountpoints": [None],
                        "children": [
                            {"mountpoints": ["/run/archiso/bootmnt"], "children": []}
                        ],
                    },
                    {
                        "path": "/dev/vda",
                        "size": 64_000_000_000,
                        "type": "disk",
                        "model": "Virtual Disk",
                        "ro": False,
                        "rm": False,
                        "mountpoints": [None],
                    },
                ]
            }
        )
        disks = list_install_disks(runner)
        self.assertEqual(tuple(disk.path for disk in disks), ("/dev/vda",))

    def test_disk_listing_flags_partitioned_disks(self) -> None:
        runner = FakeRunner(
            {
                "blockdevices": [
                    {
                        "path": "/dev/sda",
                        "size": 500_000_000_000,
                        "type": "disk",
                        "model": "Has Data",
                        "ro": False,
                        "rm": False,
                        "mountpoints": [None],
                        "children": [
                            {"mountpoints": [None], "children": []}
                        ],
                    },
                    {
                        "path": "/dev/vda",
                        "size": 64_000_000_000,
                        "type": "disk",
                        "model": "Blank Disk",
                        "ro": False,
                        "rm": False,
                        "mountpoints": [None],
                    },
                ]
            }
        )
        disks = {disk.path: disk for disk in list_install_disks(runner)}
        self.assertTrue(disks["/dev/sda"].partitioned)
        self.assertFalse(disks["/dev/vda"].partitioned)

    def test_choose_disk_cancels_to_none(self) -> None:
        runner = FakeRunner(
            {
                "blockdevices": [
                    {
                        "path": "/dev/vda",
                        "size": 64_000_000_000,
                        "type": "disk",
                        "model": "Blank Disk",
                        "ro": False,
                        "rm": False,
                        "mountpoints": [None],
                    }
                ]
            }
        )
        with patch("builtins.input", return_value="0"):
            self.assertIsNone(_choose_disk(runner))

    def test_choose_disk_requires_confirmation_before_returning(self) -> None:
        runner = FakeRunner(
            {
                "blockdevices": [
                    {
                        "path": "/dev/vda",
                        "size": 64_000_000_000,
                        "type": "disk",
                        "model": "Blank Disk",
                        "ro": False,
                        "rm": False,
                        "mountpoints": [None],
                    }
                ]
            }
        )
        with patch("builtins.input", side_effect=["1", "n", "1", "y"]):
            disk = _choose_disk(runner)
        self.assertEqual(disk.path, "/dev/vda")

    def test_choose_disk_launches_cfdisk_then_relists(self) -> None:
        runner = FakeRunner(
            {
                "blockdevices": [
                    {
                        "path": "/dev/vda",
                        "size": 64_000_000_000,
                        "type": "disk",
                        "model": "Blank Disk",
                        "ro": False,
                        "rm": False,
                        "mountpoints": [None],
                    }
                ]
            }
        )
        with patch("shutil.which", return_value="/usr/bin/cfdisk"), patch(
            "builtins.input", side_effect=["c", "1", "0"]
        ):
            self.assertIsNone(_choose_disk(runner))
        self.assertIn(("cfdisk", "/dev/vda"), runner.commands)

    def test_multilib_section_is_enabled(self) -> None:
        configured = enable_multilib(self.pacman_config.read_text())
        self.assertIn("[multilib]", configured)
        self.assertIn("Include = /etc/pacman.d/mirrorlist", configured)
        self.assertNotIn("#[multilib]", configured)

    def test_root_username_is_rejected_before_installation(self) -> None:
        config = InstallConfig(
            disk="/dev/vda",
            firmware="bios",
            hostname="protogenos",
            username="root",
            user_password="not-used",
        )
        with self.assertRaisesRegex(InstallError, "root is reserved"):
            config.validate(self.zoneinfo)

    def test_uefi_install_generates_complete_system(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(self._plan(), self._config())
        commands = runner.commands
        self.assertIn(
            ("parted", "--script", "/dev/nvme0n1", "set", "1", "esp", "on"),
            commands,
        )
        self.assertIn(
            ("mkfs.fat", "-F", "32", "-n", "PROTOEFI", "/dev/nvme0n1p1"),
            commands,
        )
        self.assertTrue(
            any("--target=x86_64-efi" in command for command in commands)
        )
        self.assertEqual(
            (self.target / "etc/hostname").read_text(), "proto-box\n"
        )
        self.assertIn("NAME=\"protogenOS\"", (self.target / "usr/lib/os-release").read_text())
        self.assertIn("UUID=root", (self.target / "etc/fstab").read_text())
        self.assertEqual((self.target / "etc/sudoers.d/10-protogenos-wheel").stat().st_mode & 0o777, 0o440)
        self.assertEqual(commands[-1], ("umount", "--recursive", str(self.target)))
        self.assertIn("fox:correct horse battery staple\n", runner.inputs)

    def test_declined_sudo_leaves_root_unlocked_with_its_own_password(self) -> None:
        runner = FakeRunner()
        config = InstallConfig(
            disk="/dev/nvme0n1",
            firmware="uefi",
            hostname="proto-box",
            username="fox",
            user_password="correct horse battery staple",
            grant_sudo=False,
            root_password="root-only-password",
        )
        self._backend(runner).install(self._plan(), config)
        commands = runner.commands
        useradd = next(command for command in commands if "useradd" in command)
        self.assertNotIn("--groups", useradd)
        self.assertNotIn("wheel", useradd)
        self.assertNotIn(
            ("arch-chroot", str(self.target), "passwd", "--lock", "root"), commands
        )
        self.assertFalse((self.target / "etc/sudoers.d/10-protogenos-wheel").exists())
        self.assertIn("root:root-only-password\n", runner.inputs)

    def test_declined_sudo_requires_a_root_password(self) -> None:
        config = InstallConfig(
            disk="/dev/vda",
            firmware="bios",
            hostname="protogenos",
            username="fox",
            user_password="correct horse battery staple",
            grant_sudo=False,
        )
        with self.assertRaisesRegex(InstallError, "root password cannot be empty"):
            config.validate(self.zoneinfo)

    def test_bios_install_uses_bios_boot_partition(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(self._plan(), self._config("bios"))
        self.assertIn(
            ("parted", "--script", "/dev/nvme0n1", "set", "1", "bios_grub", "on"),
            runner.commands,
        )
        self.assertIn(
            (
                "arch-chroot",
                str(self.target),
                "grub-install",
                "--target=i386-pc",
                "/dev/nvme0n1",
            ),
            runner.commands,
        )

    def test_aur_packages_build_as_target_user(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(
            self._plan(aur=("brave-bin",)), self._config()
        )
        pacstrap = next(command for command in runner.commands if command[0] == "pacstrap")
        self.assertNotIn("brave-bin", pacstrap)
        self.assertIn("base-devel", pacstrap)
        self.assertTrue(
            any(
                command[:7]
                == (
                    "arch-chroot",
                    str(self.target),
                    "runuser",
                    "--user",
                    "fox",
                    "--",
                    "git",
                )
                and "https://aur.archlinux.org/brave-bin.git" in command
                for command in runner.commands
            )
        )
        self.assertFalse((self.target / "etc/sudoers.d/99-protogenos-aur").exists())


if __name__ == "__main__":
    unittest.main()
