import tempfile
import unittest
from pathlib import Path

from protogenos_installer.hardware import EFI_GLOBAL_GUID, detect_features, detect_hardware


class HardwareDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cpuinfo = self.root / "cpuinfo"
        self.pci = self.root / "pci"
        self.dmi = self.root / "dmi"
        self.pci.mkdir()
        self.dmi.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _pci_device(self, name: str, device_class: str, vendor: str, device_id: str | None = None) -> None:
        device = self.pci / name
        device.mkdir()
        (device / "class").write_text(f"{device_class}\n")
        (device / "vendor").write_text(f"{vendor}\n")
        if device_id is not None:
            (device / "device").write_text(f"{device_id}\n")

    def _detect(self, **kwargs):
        return detect_hardware(
            cpuinfo=self.cpuinfo, pci_root=self.pci, dmi_root=self.dmi, **kwargs
        )

    def test_console_only_skips_graphics_and_gui_guest_tools(self) -> None:
        self.cpuinfo.write_text("vendor_id\t: AuthenticAMD\n")
        self._pci_device("0000:00:01.0", "0x030000", "0x1002")
        (self.dmi / "sys_vendor").write_text("QEMU\n")
        profile = self._detect(desktop=False)
        self.assertEqual(profile.packages, ("amd-ucode", "qemu-guest-agent"))

    def test_intel_cpu_and_amd_gpu_on_bare_metal(self) -> None:
        self.cpuinfo.write_text("vendor_id\t: GenuineIntel\n")
        self._pci_device("0000:00:02.0", "0x060000", "0x8086")  # host bridge, ignored
        self._pci_device("0000:03:00.0", "0x030000", "0x1002")
        profile = self._detect(multilib=True)
        self.assertEqual(profile.microcode, "intel-ucode")
        self.assertEqual(profile.gpu_vendors, ("0x1002",))
        self.assertIsNone(profile.hypervisor)
        for package in ("mesa", "lib32-mesa", "intel-ucode", "vulkan-radeon", "lib32-vulkan-radeon"):
            self.assertIn(package, profile.packages)
        self.assertNotIn("vulkan-intel", profile.packages)
        self.assertEqual(profile.describe()["gpus"], ["AMD"])

    def test_qemu_guest_gets_guest_tools(self) -> None:
        self.cpuinfo.write_text("vendor_id\t: AuthenticAMD\n")
        self._pci_device("0000:00:01.0", "0x030000", "0x1234")
        (self.dmi / "sys_vendor").write_text("QEMU\n")
        (self.dmi / "product_name").write_text("Standard PC (Q35 + ICH9, 2009)\n")
        profile = self._detect()
        self.assertEqual(profile.hypervisor, "qemu")
        self.assertIn("amd-ucode", profile.packages)
        self.assertIn("qemu-guest-agent", profile.packages)
        self.assertNotIn("lib32-mesa", profile.packages)

    def test_virtualbox_guest_enables_its_service(self) -> None:
        (self.dmi / "sys_vendor").write_text("innotek GmbH\n")
        profile = self._detect()
        self.assertIsNone(profile.microcode)
        self.assertIn("virtualbox-guest-utils", profile.packages)
        self.assertEqual(profile.services, ("vboxservice.service",))

    def test_nvidia_generation_decides_open_driver_support(self) -> None:
        self.cpuinfo.write_text("")
        self._pci_device("0000:01:00.0", "0x030000", "0x10de", "0x1c82")  # GTX 1050 Ti (Pascal)
        self.assertIs(self._detect().nvidia_open_supported, False)
        self._pci_device("0000:02:00.0", "0x030000", "0x10de", "0x2484")  # RTX 3070 (Ampere)
        self.assertIs(self._detect().nvidia_open_supported, True)

    def test_no_nvidia_gpu_leaves_open_driver_support_unset(self) -> None:
        self.cpuinfo.write_text("")
        self._pci_device("0000:03:00.0", "0x030000", "0x1002", "0x73bf")
        self.assertIsNone(self._detect().nvidia_open_supported)


class FeatureDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tpm = self.root / "tpm"
        self.usb = self.root / "usb"
        self.efivars = self.root / "efivars"
        self.hwdb = self.root / "fingerprint.hwdb"
        self.usb.mkdir()
        self.hwdb.write_text("# comment\nusb:v06CBp00BD*\n ID_AUTOSUSPEND=1\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _detect(self):
        return detect_features(tpm_root=self.tpm, usb_root=self.usb, hwdb=self.hwdb, efivars=self.efivars)

    def _efi_flag(self, name: str, value: int) -> None:
        self.efivars.mkdir(exist_ok=True)
        (self.efivars / f"{name}-{EFI_GLOBAL_GUID}").write_bytes(bytes([6, 0, 0, 0, value]))

    def test_bios_machine_without_devices(self) -> None:
        features = self._detect()
        self.assertFalse(features.tpm2)
        self.assertFalse(features.fingerprint_reader)
        self.assertIsNone(features.secure_boot_setup_mode)

    def test_detects_tpm2_reader_and_setup_mode(self) -> None:
        (self.tpm / "tpm0").mkdir(parents=True)
        (self.tpm / "tpm0/tpm_version_major").write_text("2\n")
        reader = self.usb / "1-4"
        reader.mkdir()
        (reader / "idVendor").write_text("06cb\n")
        (reader / "idProduct").write_text("00bd\n")
        self._efi_flag("SetupMode", 1)
        self._efi_flag("SecureBoot", 0)
        features = self._detect()
        self.assertTrue(features.tpm2)
        self.assertTrue(features.fingerprint_reader)
        self.assertIs(features.secure_boot_setup_mode, True)
        self.assertIs(features.secure_boot_enabled, False)

    def test_tpm12_and_unknown_usb_devices_are_ignored(self) -> None:
        (self.tpm / "tpm0").mkdir(parents=True)
        (self.tpm / "tpm0/tpm_version_major").write_text("1\n")
        mouse = self.usb / "1-2"
        mouse.mkdir()
        (mouse / "idVendor").write_text("046d\n")
        (mouse / "idProduct").write_text("c077\n")
        features = self._detect()
        self.assertFalse(features.tpm2)
        self.assertFalse(features.fingerprint_reader)


if __name__ == "__main__":
    unittest.main()
