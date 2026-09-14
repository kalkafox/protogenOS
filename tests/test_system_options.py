import tempfile
import unittest
from pathlib import Path

from protogenos_installer.backend import InstallConfig
from protogenos_installer.bootloader import add_encrypt_hook, kernel_cmdline, set_shell_variable
from protogenos_installer.config_io import ConfigFileError, export_config, export_credentials, load_documents
from protogenos_installer.keyboard import console_keymap, list_layouts
from protogenos_installer.mirrors import enable_parallel_downloads, parse_reflector_countries
from protogenos_installer.models import InstallPlan
from protogenos_installer.storage import parse_disk_layout


class KeyboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.model_map = self.root / "kbd-model-map"
        self.model_map.write_text(
            "# consolelayout xlayout xmodel xvariant xoptions\n"
            "uk\tgb\tpc105\t-\tterminate:ctrl_alt_bksp\n"
            "de\tde\tpc105\t-\tterminate:ctrl_alt_bksp\n"
            "de-latin1-nodeadkeys\tde\tpc105\tnodeadkeys\tterminate:ctrl_alt_bksp\n"
        )
        self.keymaps = self.root / "keymaps/i386/qwerty"
        self.keymaps.mkdir(parents=True)
        (self.keymaps / "fr.map.gz").write_text("")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _keymap(self, layout: str, variant: str = "") -> str:
        return console_keymap(layout, variant, model_map=self.model_map, keymaps_root=self.root / "keymaps")

    def test_console_keymap_mapping(self) -> None:
        self.assertEqual(self._keymap("gb"), "uk")
        self.assertEqual(self._keymap("de", "nodeadkeys"), "de-latin1-nodeadkeys")
        self.assertEqual(self._keymap("de", "neo"), "de")
        self.assertEqual(self._keymap("fr"), "fr")
        self.assertEqual(self._keymap("xx"), "us")

    def test_layout_catalog_includes_variants(self) -> None:
        rules = self.root / "base.lst"
        rules.write_text(
            "! model\n  pc105  Generic 105-key PC\n\n"
            "! layout\n  us  English (US)\n  de  German\n\n"
            "! variant\n  nodeadkeys  de: German (no dead keys)\n"
        )
        layouts = {layout.code: layout for layout in list_layouts(rules)}
        self.assertEqual(set(layouts), {"us", "de"})
        self.assertEqual(layouts["de"].variants[0].code, "nodeadkeys")
        self.assertEqual(layouts["de"].variants[0].description, "German (no dead keys)")


class MirrorTests(unittest.TestCase):
    def test_parse_reflector_countries(self) -> None:
        output = (
            "Country                Code Count\n"
            "---------------------- ---- -----\n"
            "Bosnia and Herzegovina BA       2\n"
            "Germany                DE     140\n"
        )
        countries = parse_reflector_countries(output)
        self.assertEqual([country.name for country in countries], ["Bosnia and Herzegovina", "Germany"])
        self.assertEqual(countries[1].count, 140)

    def test_parallel_downloads(self) -> None:
        self.assertIn("\nParallelDownloads = 5\n", enable_parallel_downloads("[options]\n#ParallelDownloads = 5\n"))
        self.assertIn("[options]\nParallelDownloads = 5\n", enable_parallel_downloads("[options]\nCheckSpace\n"))
        kept = "[options]\nParallelDownloads = 10\n"
        self.assertEqual(enable_parallel_downloads(kept), kept)


class BootConfigTests(unittest.TestCase):
    def test_legacy_initramfs_gets_encrypt_hook_and_keyboard(self) -> None:
        conf = "HOOKS=(base udev autodetect modconf block filesystems fsck)\n"
        self.assertEqual(
            add_encrypt_hook(conf),
            "HOOKS=(base udev keyboard autodetect modconf block encrypt filesystems fsck)\n",
        )

    def test_legacy_cmdline_uses_cryptdevice(self) -> None:
        cmdline = kernel_cmdline(
            root_uuid="R", filesystem="ext4", luks_uuid="L", systemd_initramfs=False
        )
        self.assertEqual(cmdline, "cryptdevice=UUID=L:cryptroot root=/dev/mapper/cryptroot rw")

    def test_set_shell_variable_replaces_commented_and_missing_keys(self) -> None:
        text = 'GRUB_TIMEOUT=5\n#GRUB_DISABLE_OS_PROBER=false\n'
        text = set_shell_variable(text, "GRUB_DISABLE_OS_PROBER", "false")
        text = set_shell_variable(text, "GRUB_CMDLINE_LINUX", "a b")
        self.assertEqual(text, "GRUB_TIMEOUT=5\nGRUB_DISABLE_OS_PROBER=false\nGRUB_CMDLINE_LINUX='a b'\n")


class DiskLayoutParsingTests(unittest.TestCase):
    def test_blank_disk_is_all_free(self) -> None:
        output = "BYT;\n/dev/vda:25769803776B:virtblk:512:512:unknown:Virtio Block Device:;\n"
        layout = parse_disk_layout("/dev/vda", output, "")
        self.assertIsNone(layout.table)
        self.assertGreater(layout.largest_free.size, 23 * 1024**3)

    def test_filesystem_details_from_flat_lsblk_output(self) -> None:
        parted = (
            "BYT;\n/dev/vda:32212254720B:virtblk:512:512:gpt:Virtio:;\n"
            "1:1048576B:315621375B:314572800B:fat32:EFI:boot, esp;\n"
        )
        lsblk = (
            '{"blockdevices": [{"path": "/dev/vda", "partn": null, "fstype": null},'
            ' {"path": "/dev/vda1", "partn": 1, "fstype": "vfat", "label": null, "partlabel": "EFI",'
            ' "parttypename": "EFI System", "mountpoints": []}]}'
        )
        partition = parse_disk_layout("/dev/vda", parted, lsblk).partitions[0]
        self.assertEqual((partition.fstype, partition.label, partition.type_name), ("vfat", "EFI", "EFI System"))

    def test_free_regions_are_mib_aligned(self) -> None:
        output = (
            "BYT;\n/dev/vda:25769803776B:virtblk:512:512:gpt:Virtio:;\n"
            "1:17408B:1048575B:1031168B:free;\n"
            "1:1048576B:1074790399B:1073741824B:fat32:ESP:boot, esp;\n"
            "1:1074790400B:25769786879B:24694996480B:free;\n"
        )
        layout = parse_disk_layout("/dev/vda", output, "")
        self.assertEqual(len(layout.partitions), 1)
        self.assertEqual(layout.largest_free.start % 1024**2, 0)
        self.assertEqual(layout.largest_free.end % 1024**2, 0)
        self.assertLessEqual(layout.largest_free.end, 25769786880)


class ConfigFileTests(unittest.TestCase):
    def test_round_trip_keeps_secrets_separate(self) -> None:
        plan = InstallPlan(
            persona="gamer",
            packages=("base",),
            selections={"kernel": ("linux-zen",)},
            aur_packages=("brave-bin",),
            multilib_required=True,
        )
        config = InstallConfig(
            disk="/dev/vda",
            firmware="uefi",
            hostname="proto",
            username="fox",
            user_password="user-secret",
            encrypt=True,
            encryption_passphrase="disk-secret",
            bootloader="systemd-boot",
            additional_users=[{"username": "kit", "password": "kit-secret", "sudo": True}],
        )
        exported = export_config(plan, config)
        self.assertNotIn("secret", repr(exported))
        persona, selections, allow_aur, loaded = load_documents(exported, export_credentials(config))
        self.assertEqual((persona, selections, allow_aur), ("gamer", {"kernel": ("linux-zen",)}, True))
        self.assertEqual(loaded, config)
        self.assertEqual(loaded.additional_users[0].password, "kit-secret")

    def test_unknown_settings_are_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigFileError, "unknown install settings"):
            load_documents({"version": 1, "persona": "general", "install": {"disk": "/dev/vda", "bogus": 1}})


if __name__ == "__main__":
    unittest.main()
