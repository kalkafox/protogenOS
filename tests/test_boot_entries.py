import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/add-installer-boot-entries"

SYSTEMD_BOOT_ENTRY = """title    protogenOS Live Environment (%ARCH%, UEFI)
sort-key 01
linux    /%INSTALL_DIR%/boot/%ARCH%/vmlinuz-linux
options  archisobasedir=%INSTALL_DIR%
"""
GRUB_CONFIG = """menuentry "protogenOS Live Environment (%ARCH%, ${archiso_platform})" --class arch --id 'archlinux' {
    linux /%INSTALL_DIR%/boot/%ARCH%/vmlinuz-linux archisobasedir=%INSTALL_DIR%
}

menuentry "Speech" --hotkey s --id 'archlinux-accessibility' {
    linux /vmlinuz accessibility=on
}
"""
SYSLINUX_CONFIG = """LABEL arch
TEXT HELP
Boot the protogenOS Live Environment on BIOS.
ENDTEXT
MENU LABEL protogenOS Live Environment (%ARCH%, BIOS)
APPEND archisobasedir=%INSTALL_DIR%

LABEL archspeech
APPEND accessibility=on
"""


@unittest.skipUnless(shutil.which("bash") and shutil.which("awk"), "needs bash and awk")
class InstallerBootEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.profile = Path(self.temporary.name)
        entries = self.profile / "efiboot/loader/entries"
        entries.mkdir(parents=True)
        (entries / "01-archiso-linux.conf").write_text(SYSTEMD_BOOT_ENTRY)
        (self.profile / "grub").mkdir()
        (self.profile / "grub/grub.cfg").write_text(GRUB_CONFIG)
        (self.profile / "syslinux").mkdir()
        (self.profile / "syslinux/archiso_sys-linux.cfg").write_text(SYSLINUX_CONFIG)
        subprocess.run(["bash", str(SCRIPT), str(self.profile)], check=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_systemd_boot_entry(self) -> None:
        entry = (self.profile / "efiboot/loader/entries/01-archiso-linux-tui.conf").read_text()
        self.assertIn("with text installer", entry)
        self.assertIn("sort-key 01a", entry)
        self.assertIn("options  archisobasedir=%INSTALL_DIR% protogenos.installer=tui", entry)

    def test_grub_entry_follows_default_entry(self) -> None:
        config = (self.profile / "grub/grub.cfg").read_text()
        self.assertEqual(config.count("protogenos.installer=tui"), 1)
        self.assertLess(config.index("'archlinux-tui'"), config.index("'archlinux-accessibility'"))
        self.assertIn(
            "linux /%INSTALL_DIR%/boot/%ARCH%/vmlinuz-linux archisobasedir=%INSTALL_DIR% protogenos.installer=tui",
            config,
        )

    def test_syslinux_label(self) -> None:
        config = (self.profile / "syslinux/archiso_sys-linux.cfg").read_text()
        self.assertIn("LABEL archtui\n", config)
        self.assertIn("MENU LABEL protogenOS Live Environment (%ARCH%, BIOS) with text installer", config)
        self.assertIn("APPEND archisobasedir=%INSTALL_DIR% protogenos.installer=tui", config)
        self.assertLess(config.index("LABEL archtui"), config.index("LABEL archspeech"))


if __name__ == "__main__":
    unittest.main()
