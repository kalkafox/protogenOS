import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from protogenos_installer.backend import (
    CommandRunner,
    DownloadHeartbeat,
    InstallConfig,
    InstallError,
    InstallerBackend,
    detect_firmware,
    enable_multilib,
    list_install_disks,
    partition_path,
)
from protogenos_installer.cli import _choose_disk
from protogenos_installer.hardware import HardwareProfile
from protogenos_installer.models import InstallPlan


class FakeRunner(CommandRunner):
    def __init__(self, lsblk_data: dict[str, object] | None = None) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.inputs: list[str | None] = []
        self.lsblk_data = lsblk_data
        self.emitted: list[str] = []
        self.failing_scripts: tuple[str, ...] = ()
        self.parted_outputs: list[str] = []
        self.layout_lsblk: dict[str, object] = {"blockdevices": []}

    def emit(self, line: str) -> None:
        self.emitted.append(line)

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
        if command[0] == "lsblk" and "--json" in command and "PATH,SIZE,TYPE,MODEL,RO,RM,MOUNTPOINTS" in command:
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
            (target / "etc/mkinitcpio.conf").write_text(
                "MODULES=()\nHOOKS=(base systemd autodetect microcode modconf kms keyboard "
                "sd-vconsole block filesystems fsck)\n"
            )
        if command[0] == "findmnt":
            return subprocess.CompletedProcess(command, 1, "", "")
        if command[0] == "blkid":
            return subprocess.CompletedProcess(command, 0, "ROOT-UUID\n", "")
        if command[:2] == ("cryptsetup", "luksUUID"):
            return subprocess.CompletedProcess(command, 0, "LUKS-UUID\n", "")
        if command[0] == "parted" and "print" in command:
            output = self.parted_outputs.pop(0) if self.parted_outputs else ""
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[0] == "lsblk" and "PATH,PARTN,FSTYPE,LABEL,PARTLABEL,PARTTYPENAME,MOUNTPOINTS" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.layout_lsblk), "")
        if "runuser" in command and any(part in command[-1] for part in self.failing_scripts):
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "", "")


class FakeInstallerBackend(InstallerBackend):
    def _validate_environment(self, config: InstallConfig) -> None:
        return


class DetectFirmwareTests(unittest.TestCase):
    def test_detects_boot_mode_from_efi_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            efi_dir = Path(temporary) / "efi"
            self.assertEqual(detect_firmware(efi_dir), "bios")
            efi_dir.mkdir()
            self.assertEqual(detect_firmware(efi_dir), "uefi")


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

    def _plan(
        self, *, aur: tuple[str, ...] = (), multilib: bool = False, persona: str = "general"
    ) -> InstallPlan:
        return InstallPlan(
            persona=persona,
            packages=("base", "linux", "linux-firmware", "networkmanager", "plasma-login-manager", *aur),
            selections={"kernel": ("linux",)},
            aur_packages=aur,
            multilib_required=multilib,
        )

    def _config(self, firmware: str = "uefi", **overrides) -> InstallConfig:
        values = dict(
            disk="/dev/nvme0n1",
            firmware=firmware,
            hostname="proto-box",
            username="fox",
            user_password="correct horse battery staple",
        )
        values.update(overrides)
        return InstallConfig(**values)

    def _backend(
        self, runner: FakeRunner, hardware: HardwareProfile | None = None
    ) -> FakeInstallerBackend:
        profile = hardware or HardwareProfile()
        return FakeInstallerBackend(
            runner,
            target_root=self.target,
            pacman_config=self.pacman_config,
            zoneinfo_root=self.zoneinfo,
            locale_gen=self.locale_gen,
            require_root=False,
            hardware_detector=lambda **_: profile,
            iwd_storage=self.root / "iwd",
            keymap_resolver=lambda layout, variant: f"{layout}-console",
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

    def test_disk_listing_requests_a_tree(self) -> None:
        runner = FakeRunner({"blockdevices": []})
        list_install_disks(runner)
        self.assertIn("--tree", runner.commands[0])

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
        self.assertIn(
            (
                "mount",
                "-t",
                "btrfs",
                "-o",
                "subvol=@,compress=zstd:1,noatime",
                "/dev/nvme0n1p2",
                str(self.target),
            ),
            commands,
        )
        self.assertIn(
            ("mount", "-t", "vfat", "-o", "umask=0077", "/dev/nvme0n1p1", str(self.target / "boot")),
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

    def test_aur_packages_build_with_yay_as_target_user(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(
            self._plan(aur=("brave-bin",)), self._config()
        )
        pacstrap = next(command for command in runner.commands if command[0] == "pacstrap")
        self.assertNotIn("brave-bin", pacstrap)
        self.assertIn("base-devel", pacstrap)
        user_scripts = [
            command[-1]
            for command in runner.commands
            if command[:7]
            == ("arch-chroot", str(self.target), "runuser", "--user", "fox", "--", "/bin/bash")
        ]
        self.assertIn("https://aur.archlinux.org/yay.git", user_scripts[0])
        self.assertTrue(user_scripts[0].endswith("&& yay --version"))
        self.assertIn("yay -S --noconfirm --needed", user_scripts[1])
        self.assertTrue(user_scripts[1].endswith(" brave-bin"))
        self.assertFalse((self.target / "etc/sudoers.d/99-protogenos-aur").exists())

    def test_failed_aur_build_warns_after_bootloader_is_installed(self) -> None:
        runner = FakeRunner()
        runner.failing_scripts = ("yay.git", "brave-bin.git")
        backend = self._backend(runner)
        backend.install(self._plan(aur=("brave-bin", "sweet-theme-git")), self._config())
        grub_index = next(
            index for index, command in enumerate(runner.commands) if "grub-install" in command
        )
        first_aur_index = next(
            index for index, command in enumerate(runner.commands) if "runuser" in command
        )
        self.assertLess(grub_index, first_aur_index)
        self.assertEqual(len(backend.warnings), 2)
        self.assertIn("brave-bin", backend.warnings[1])
        self.assertNotIn("sweet-theme-git", backend.warnings[1])
        self.assertFalse((self.target / "etc/sudoers.d/99-protogenos-aur").exists())

    def test_install_adds_hardware_and_filesystem_packages(self) -> None:
        runner = FakeRunner()
        hardware = HardwareProfile(
            microcode="amd-ucode",
            packages=("mesa", "amd-ucode", "vulkan-radeon", "virtualbox-guest-utils"),
            services=("vboxservice.service",),
        )
        (self.target / "usr/lib/systemd/system").mkdir(parents=True)
        (self.target / "usr/lib/systemd/system/vboxservice.service").write_text("")
        (self.target / "usr/lib/systemd/system/bluetooth.service").write_text("")
        self._backend(runner, hardware).install(self._plan(), self._config())
        pacstrap = next(command for command in runner.commands if command[0] == "pacstrap")
        for package in ("amd-ucode", "vulkan-radeon", "btrfs-progs", "dosfstools", "nano"):
            self.assertIn(package, pacstrap)
        enable = next(
            command for command in runner.commands if command[2:4] == ("systemctl", "enable")
        )
        for unit in (
            "NetworkManager.service",
            "systemd-timesyncd.service",
            "fstrim.timer",
            "bluetooth.service",
            "vboxservice.service",
        ):
            self.assertIn(unit, enable)
        self.assertNotIn("cups.socket", enable)

    def test_minimal_install_skips_desktop_setup(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(self._plan(persona="minimal"), self._config())
        pacstrap = next(command for command in runner.commands if command[0] == "pacstrap")
        self.assertIn("nano", pacstrap)
        self.assertIn("sudo", pacstrap)
        self.assertNotIn("man-db", pacstrap)
        enable = next(
            command for command in runner.commands if command[2:4] == ("systemctl", "enable")
        )
        self.assertNotIn("plasmalogin.service", enable)
        self.assertIn("NetworkManager.service", enable)
        self.assertFalse((self.target / "etc/skel/.config/kdeglobals").exists())
        self.assertFalse((self.target / "etc/X11/xorg.conf.d/00-keyboard.conf").exists())
        self.assertTrue((self.target / "etc/vconsole.conf").exists())

    def test_keyring_is_refreshed_before_pacstrap(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(self._plan(), self._config())
        keyring = runner.commands.index(
            ("pacman", "-Sy", "--noconfirm", "--needed", "archlinux-keyring")
        )
        pacstrap = next(
            index for index, command in enumerate(runner.commands) if command[0] == "pacstrap"
        )
        self.assertLess(keyring, pacstrap)

    def test_install_reports_numbered_steps(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(self._plan(), self._config())
        steps = [line for line in runner.emitted if line.startswith("[protogenos] step ")]
        self.assertEqual(steps[0], "[protogenos] step 1/6: Checking the installation environment")
        self.assertEqual(steps[-1], "[protogenos] step 6/6: Finishing up")

    def test_live_wifi_networks_are_copied_to_networkmanager(self) -> None:
        iwd = self.root / "iwd"
        iwd.mkdir()
        (iwd / "=48c3b66d65.psk").write_text("[Security]\nPassphrase=hunter22\n")
        (iwd / "Cafe.open").write_text("")
        runner = FakeRunner()
        self._backend(runner).install(self._plan(), self._config())
        connections = self.target / "etc/NetworkManager/system-connections"
        home = (connections / "=48c3b66d65.nmconnection").read_text()
        self.assertIn("psk=hunter22", home)
        self.assertIn("key-mgmt=wpa-psk", home)
        self.assertEqual((connections / "=48c3b66d65.nmconnection").stat().st_mode & 0o777, 0o600)
        self.assertNotIn("wifi-security", (connections / "Cafe.nmconnection").read_text())

    def test_offline_environment_is_rejected(self) -> None:
        backend = InstallerBackend(
            FakeRunner(),
            target_root=self.target,
            pacman_config=self.pacman_config,
            zoneinfo_root=self.zoneinfo,
            locale_gen=self.locale_gen,
            require_root=False,
            online_check=lambda: False,
        )
        with patch("shutil.which", return_value="/usr/bin/tool"):
            with self.assertRaisesRegex(InstallError, "no internet connection"):
                backend.install(self._plan(), self._config())

    def test_runner_streams_output_into_log(self) -> None:
        log_path = self.root / "install.log"
        runner = CommandRunner(log_path=log_path)
        with patch("builtins.print"):
            result = runner.run(["sh", "-c", "printf 'one\\rtwo\\nthree\\n'"])
        self.assertEqual(result.stdout, "two\nthree\n")
        self.assertIn("two\nthree\n", log_path.read_text())

    def test_heartbeat_reports_growing_then_unchanged_cache(self) -> None:
        cache = self.root / "cache"
        cache.mkdir()
        lines: list[str] = []
        ready = threading.Event()

        def emit(line: str) -> None:
            lines.append(line)
            if len(lines) == 1:
                (cache / "linux.pkg.tar.zst.part").write_bytes(b"x" * 2048)
            if len(lines) == 3:
                ready.set()

        with DownloadHeartbeat(emit, cache, interval=0.01):
            self.assertTrue(ready.wait(5))
        self.assertEqual(lines[0], "Package cache: 0.0 B downloaded so far")
        self.assertEqual(lines[1], "Package cache: 2.0 KiB downloaded so far")
        self.assertTrue(lines[2].startswith("Package cache: 2.0 KiB (unchanged for "))

    def test_aur_multilib_persisted_to_target_pacman_conf(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(
            self._plan(aur=("brave-bin",), multilib=True), self._config()
        )
        configured = (self.target / "etc/pacman.conf").read_text()
        self.assertIn("[multilib]", configured)
        self.assertIn("Include = /etc/pacman.d/mirrorlist", configured)


    # -- P1: storage, encryption, bootloaders, system options ---------------

    def _chroot_commands(self, runner: FakeRunner) -> list[tuple[str, ...]]:
        return [command[2:] for command in runner.commands if command[:2] == ("arch-chroot", str(self.target))]

    def test_encrypted_install_formats_luks_and_adds_unlock_parameters(self) -> None:
        runner = FakeRunner()
        config = self._config(encrypt=True, encryption_passphrase="unlock-me-please")
        self._backend(runner).install(self._plan(), config)
        luks_format = next(command for command in runner.commands if command[:2] == ("cryptsetup", "luksFormat"))
        self.assertEqual(luks_format[-1], "/dev/nvme0n1p2")
        self.assertNotIn("unlock-me-please", " ".join(" ".join(command) for command in runner.commands))
        self.assertIn("unlock-me-please", runner.inputs)
        self.assertIn(("mkfs.btrfs", "-f", "-L", "protogenos", "/dev/mapper/cryptroot"), runner.commands)
        hooks = (self.target / "etc/mkinitcpio.conf").read_text()
        self.assertIn("block sd-encrypt filesystems", hooks)
        self.assertIn(("mkinitcpio", "-P"), self._chroot_commands(runner))
        grub = (self.target / "etc/default/grub").read_text()
        self.assertIn("GRUB_CMDLINE_LINUX='rd.luks.name=LUKS-UUID=cryptroot zswap.enabled=0'", grub)
        self.assertIn('GRUB_DISTRIBUTOR=protogenOS', grub)
        self.assertEqual(runner.commands[-1], ("cryptsetup", "close", "cryptroot"))
        self.assertIn(("cryptsetup", "luksFormat"), [command[:2] for command in runner.commands])

    def test_bios_encrypted_install_gets_separate_boot_partition(self) -> None:
        runner = FakeRunner()
        config = self._config("bios", encrypt=True, encryption_passphrase="unlock-me-please", filesystem="ext4")
        self._backend(runner).install(self._plan(), config)
        self.assertIn(("parted", "--script", "/dev/nvme0n1", "mkpart", "boot", "3MiB", "1027MiB"), runner.commands)
        self.assertIn(("mkfs.ext4", "-F", "-L", "protoboot", "/dev/nvme0n1p2"), runner.commands)
        self.assertEqual(
            next(command for command in runner.commands if command[:2] == ("cryptsetup", "luksFormat"))[-1],
            "/dev/nvme0n1p3",
        )

    def test_systemd_boot_writes_entries_with_full_cmdline(self) -> None:
        runner = FakeRunner()
        plan = InstallPlan(
            persona="general",
            packages=("base", "linux-zen", "linux-firmware"),
            selections={"kernel": ("linux-zen",)},
            aur_packages=(),
            multilib_required=False,
        )
        self._backend(runner).install(plan, self._config(bootloader="systemd-boot", kernel_headers=True))
        pacstrap = next(command for command in runner.commands if command[0] == "pacstrap")
        self.assertNotIn("grub", pacstrap)
        self.assertIn("linux-zen-headers", pacstrap)
        entry = (self.target / "boot/loader/entries/protogenos.conf").read_text()
        self.assertIn("linux   /vmlinuz-linux-zen", entry)
        self.assertIn("options root=UUID=ROOT-UUID rootflags=subvol=@ rw zswap.enabled=0", entry)
        self.assertIn(("bootctl", "install", "--esp-path=/boot"), self._chroot_commands(runner))

    def test_limine_registers_uefi_entry_on_esp(self) -> None:
        runner = FakeRunner()
        self._backend(runner).install(self._plan(), self._config(bootloader="limine", filesystem="xfs", swap="none"))
        conf = (self.target / "boot/limine.conf").read_text()
        self.assertIn("cmdline: root=UUID=ROOT-UUID rw\n", conf)
        efibootmgr = next(command for command in self._chroot_commands(runner) if command[0] == "efibootmgr")
        self.assertIn("--part", efibootmgr)
        self.assertEqual(efibootmgr[efibootmgr.index("--part") + 1], "1")
        self.assertTrue((self.target / "etc/pacman.d/hooks/99-limine.hook").is_file())
        self.assertFalse((self.target / "etc/systemd/zram-generator.conf").exists())
        self.assertIn(("mkfs.xfs", "-f", "-L", "protogenos", "/dev/nvme0n1p2"), runner.commands)

    def test_free_space_layout_creates_partitions_in_largest_gap(self) -> None:
        runner = FakeRunner()
        gib = 1024**3
        disk = "/dev/nvme0n1:100000000000B:nvme:512:512:gpt:Disk:;"
        before = "\n".join(
            [
                "BYT;",
                disk,
                "1:1048576B:105000000B:103951425B:fat32:EFI:boot, esp;",
                "2:105000001B:40000000000B:39894999999B:ntfs:Windows:msftdata;",
                "1:40000000001B:99999999999B:59999999999B:free;",
            ]
        )
        esp_start = ((40000000001 + 1024**2 - 1) // 1024**2) * 1024**2
        root_start = esp_start + 1024 * 1024**2
        after = "\n".join(
            [
                "BYT;",
                disk,
                f"3:{esp_start}B:{root_start - 1}B:{root_start - esp_start}B::ESP:;",
                f"4:{root_start}B:99999000000B:{99999000000 - root_start}B::root:;",
            ]
        )
        runner.parted_outputs = [before, after]
        config = self._config(disk_layout="free-space")
        self._backend(runner).install(self._plan(), config)
        mkparts = [command for command in runner.commands if command[:4] == ("parted", "--script", "/dev/nvme0n1", "mkpart")]
        self.assertEqual(mkparts[0][4:7], ("ESP", "fat32", f"{esp_start // 512}s"))
        self.assertIn(("parted", "--script", "/dev/nvme0n1", "set", "3", "esp", "on"), runner.commands)
        self.assertNotIn(("wipefs", "--all", "--force", "/dev/nvme0n1"), runner.commands)
        self.assertIn(("mkfs.btrfs", "-f", "-L", "protogenos", "/dev/nvme0n1p4"), runner.commands)
        grub = (self.target / "etc/default/grub").read_text()
        self.assertIn("GRUB_DISABLE_OS_PROBER=false", grub)
        self.assertIn("os-prober", next(command for command in runner.commands if command[0] == "pacstrap"))
        self.assertGreater(99999999999 - root_start, 16 * gib)

    def test_existing_partitions_layout_reuses_esp_without_formatting(self) -> None:
        runner = FakeRunner()
        runner.parted_outputs = [
            "\n".join(
                [
                    "BYT;",
                    "/dev/nvme0n1:100000000000B:nvme:512:512:gpt:Disk:;",
                    "1:1048576B:1074790399B:1073741824B:fat32:EFI:boot, esp;",
                    "2:1074790400B:99999999999B:98925209600B:ext4:data:;",
                ]
            )
        ]
        runner.layout_lsblk = {
            "blockdevices": [
                {
                    "path": "/dev/nvme0n1",
                    "children": [
                        {"path": "/dev/nvme0n1p1", "partn": 1, "fstype": "vfat", "mountpoints": [None]},
                        {"path": "/dev/nvme0n1p2", "partn": 2, "fstype": "ext4", "mountpoints": [None]},
                    ],
                }
            ]
        }
        config = self._config(
            disk_layout="partitions", root_partition="/dev/nvme0n1p2", boot_partition="/dev/nvme0n1p1"
        )
        backend = FakeInstallerBackend(
            runner,
            target_root=self.target,
            pacman_config=self.pacman_config,
            zoneinfo_root=self.zoneinfo,
            locale_gen=self.locale_gen,
            require_root=False,
            hardware_detector=lambda **_: HardwareProfile(),
            iwd_storage=self.root / "iwd",
            keymap_resolver=lambda layout, variant: layout,
        )
        backend.install(self._plan(), config)
        self.assertIn(("wipefs", "--all", "--force", "/dev/nvme0n1p2"), runner.commands)
        self.assertFalse(any(command[0] == "mkfs.fat" for command in runner.commands))
        self.assertFalse(any("mklabel" in command for command in runner.commands))

    def test_keyboard_zram_and_extra_users_are_configured(self) -> None:
        runner = FakeRunner()
        config = self._config(
            keyboard_layout="de",
            keyboard_variant="nodeadkeys",
            additional_users=[{"username": "kit", "password": "kit-password", "sudo": False}],
        )
        self._backend(runner).install(self._plan(), config)
        self.assertEqual((self.target / "etc/vconsole.conf").read_text(), "KEYMAP=de-console\n")
        xorg = (self.target / "etc/X11/xorg.conf.d/00-keyboard.conf").read_text()
        self.assertIn('Option "XkbVariant" "nodeadkeys"', xorg)
        self.assertIn("LayoutList=de", (self.target / "etc/skel/.config/kxkbrc").read_text())
        self.assertIn("zram-size", (self.target / "etc/systemd/zram-generator.conf").read_text())
        self.assertIn(("useradd", "--create-home", "--shell", "/bin/bash", "kit"), self._chroot_commands(runner))
        self.assertIn("kit:kit-password\n", runner.inputs)
        saved = json.loads((self.target / "var/log/protogenos-install.json").read_text())
        self.assertEqual(saved["install"]["keyboard_layout"], "de")
        self.assertNotIn("user_password", saved["install"])
        self.assertEqual(saved["install"]["additional_users"], [{"username": "kit", "sudo": False}])

    def test_fstab_drops_btrfs_subvolume_ids(self) -> None:
        runner = FakeRunner()
        backend = self._backend(runner)
        runner.run = lambda args, **kwargs: subprocess.CompletedProcess(
            args, 0, "UUID=x / btrfs rw,noatime,compress=zstd:1,subvolid=256,subvol=/@ 0 0\n", ""
        )
        self.target.mkdir(parents=True)
        backend._write_fstab()
        self.assertEqual(
            (self.target / "etc/fstab").read_text(),
            "UUID=x / btrfs rw,noatime,compress=zstd:1,subvol=/@ 0 0\n",
        )

    def test_before_unmount_hook_runs_while_mounted(self) -> None:
        runner = FakeRunner()
        seen: list[Path] = []
        self._backend(runner).install(self._plan(), self._config(), before_unmount=seen.append)
        self.assertEqual(seen, [self.target])
        self.assertEqual(runner.commands[-1], ("umount", "--recursive", str(self.target)))


class InstallConfigValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.zoneinfo = Path(self.temporary.name)
        (self.zoneinfo / "UTC").write_text("UTC")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _validate(self, **overrides) -> None:
        values = dict(disk="/dev/vda", firmware="uefi", hostname="proto", username="fox", user_password="pw")
        values.update(overrides)
        InstallConfig(**values).validate(self.zoneinfo)

    def test_rejections(self) -> None:
        cases = [
            ({"bootloader": "systemd-boot", "firmware": "bios"}, "requires UEFI"),
            ({"disk_layout": "free-space", "firmware": "bios"}, "requires UEFI"),
            ({"disk_layout": "partitions"}, "root partition"),
            ({"disk_layout": "partitions", "root_partition": "/dev/sdb2", "boot_partition": "/dev/vda1"}, "not a partition"),
            ({"encrypt": True, "encryption_passphrase": "short"}, "at least 8"),
            ({"encrypt": True, "encryption_passphrase": "pässphrase-long"}, "printable ASCII"),
            ({"filesystem": "zfs"}, "filesystem must be"),
            ({"keyboard_layout": "../evil"}, "keyboard layout"),
            ({"additional_users": [{"username": "fox", "password": "x"}]}, "already taken"),
            ({"additional_users": [{"username": "kit", "password": ""}]}, "cannot be empty"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(InstallError, message):
                    self._validate(**overrides)

    def test_valid_advanced_configuration(self) -> None:
        self._validate(
            disk_layout="partitions",
            root_partition="/dev/vda3",
            boot_partition="/dev/vda1",
            encrypt=True,
            encryption_passphrase="correct horse",
            bootloader="limine",
            filesystem="f2fs",
            keyboard_layout="fr",
            keyboard_variant="bepo",
            mirror_country="Germany",
            additional_users=[{"username": "kit", "password": "pw", "sudo": True}],
        )


if __name__ == "__main__":
    unittest.main()
