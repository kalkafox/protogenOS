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
        self.assertIn("sort-key 01b", entry)
        self.assertIn("options  archisobasedir=%INSTALL_DIR% protogenos.installer=tui", entry)
        kiosk = (self.profile / "efiboot/loader/entries/01-archiso-linux-kiosk.conf").read_text()
        self.assertIn("with installer only", kiosk)
        self.assertIn("sort-key 01a", kiosk)
        self.assertIn("options  archisobasedir=%INSTALL_DIR% quiet splash protogenos.installer=kiosk", kiosk)

    def test_grub_entry_follows_default_entry(self) -> None:
        config = (self.profile / "grub/grub.cfg").read_text()
        self.assertEqual(config.count("protogenos.installer=tui"), 1)
        self.assertEqual(config.count("protogenos.installer=kiosk"), 1)
        self.assertLess(config.index("'archlinux'"), config.index("'archlinux-kiosk'"))
        self.assertLess(config.index("'archlinux-kiosk'"), config.index("'archlinux-tui'"))
        self.assertLess(config.index("'archlinux-tui'"), config.index("'archlinux-accessibility'"))
        self.assertIn('with installer only" --hotkey i --class', config)
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
        self.assertIn("LABEL archkiosk\n", config)
        self.assertLess(config.index("LABEL archkiosk"), config.index("LABEL archtui"))

    def test_default_and_kiosk_entries_show_the_splash(self) -> None:
        entries = self.profile / "efiboot/loader/entries"
        self.assertTrue((entries / "01-archiso-linux.conf").read_text().rstrip().endswith("quiet splash"))
        self.assertIn("protogenos.installer=kiosk", (entries / "01-archiso-linux-kiosk.conf").read_text())
        self.assertIn("quiet splash", (entries / "01-archiso-linux-kiosk.conf").read_text())
        self.assertNotIn("splash", (entries / "01-archiso-linux-tui.conf").read_text())
        grub = (self.profile / "grub/grub.cfg").read_text()
        self.assertEqual(grub.count("quiet splash"), 2)
        self.assertNotIn("installer=tui quiet", grub)
        self.assertNotIn("splash", grub.split("'archlinux-tui'")[1].split("}")[0])
        syslinux = (self.profile / "syslinux/archiso_sys-linux.cfg").read_text()
        self.assertEqual(syslinux.count("quiet splash"), 2)
        self.assertNotIn("splash", syslinux.split("LABEL archtui")[1].split("\n\n")[0])


if __name__ == "__main__":
    unittest.main()
