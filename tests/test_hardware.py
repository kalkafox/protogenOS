import tempfile
import unittest
from pathlib import Path

from protogenos_installer.hardware import detect_hardware


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

    def _pci_device(self, name: str, device_class: str, vendor: str) -> None:
        device = self.pci / name
        device.mkdir()
        (device / "class").write_text(f"{device_class}\n")
        (device / "vendor").write_text(f"{vendor}\n")

    def _detect(self, **kwargs):
        return detect_hardware(
            cpuinfo=self.cpuinfo, pci_root=self.pci, dmi_root=self.dmi, **kwargs
        )

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


if __name__ == "__main__":
    unittest.main()
