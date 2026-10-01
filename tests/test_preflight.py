import tempfile
import unittest
from pathlib import Path

from protogenos_installer.backend import DiskInfo
from protogenos_installer.preflight import (
    ERROR,
    OK,
    WARNING,
    check_clock,
    check_devices,
    check_disks,
    check_firmware,
    check_memory,
    check_power,
    describe_problems,
)
from protogenos_installer.preflight import Check

GIB = 1024**3


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_no_disks_is_an_error_that_mentions_ahci(self) -> None:
        check = check_disks([])
        self.assertEqual(check.status, ERROR)
        self.assertIn("AHCI", check.detail)

    def test_small_disks_are_an_error(self) -> None:
        check = check_disks([DiskInfo("/dev/sda", 8 * GIB, "USB", True, False)])
        self.assertEqual(check.status, ERROR)

    def test_usable_disk(self) -> None:
        check = check_disks([DiskInfo("/dev/nvme0n1", 512 * GIB, "Samsung", False, True)])
        self.assertEqual((check.status, check.title), (OK, "1 disk available"))

    def test_low_memory_warns(self) -> None:
        meminfo = self.root / "meminfo"
        _write(meminfo, "MemTotal:        1500000 kB")
        self.assertEqual(check_memory(meminfo).status, WARNING)
        _write(meminfo, "MemTotal:        8000000 kB")
        self.assertEqual(check_memory(meminfo).status, OK)

    def test_battery_without_charger_warns(self) -> None:
        _write(self.root / "BAT0/type", "Battery")
        _write(self.root / "BAT0/capacity", "20")
        _write(self.root / "AC/type", "Mains")
        _write(self.root / "AC/online", "0")
        check = check_power(self.root)
        self.assertEqual((check.status, check.title), (WARNING, "Battery at 20%"))
        _write(self.root / "AC/online", "1")
        self.assertEqual(check_power(self.root).status, OK)

    def test_peripheral_batteries_are_ignored(self) -> None:
        _write(self.root / "hidpp_battery_0/type", "Battery")
        _write(self.root / "hidpp_battery_0/scope", "Device")
        _write(self.root / "hidpp_battery_0/capacity", "5")
        self.assertEqual(check_power(self.root).status, OK)

    def test_firmware(self) -> None:
        self.assertEqual(check_firmware("bios", False).status, WARNING)
        self.assertEqual(check_firmware("uefi", True).status, WARNING)
        self.assertEqual(check_firmware("uefi", False).status, OK)

    def test_clock_in_the_past_warns(self) -> None:
        self.assertEqual(check_clock(lambda: 0.0).status, WARNING)

    def test_driverless_wifi_and_intel_raid(self) -> None:
        _write(self.root / "0000:02:00.0/class", "0x028000")
        _write(self.root / "0000:02:00.0/vendor", "0x14e4")
        _write(self.root / "0000:02:00.0/device", "0x43a0")
        _write(self.root / "0000:00:17.0/class", "0x010400")
        _write(self.root / "0000:00:17.0/vendor", "0x8086")
        ids = sorted(check.id for check in check_devices(self.root))
        self.assertEqual(ids, ["raid-mode", "wifi-driver"])
        (self.root / "0000:02:00.0/driver").mkdir()
        ids = [check.id for check in check_devices(self.root)]
        self.assertEqual(ids, ["raid-mode"])


class DescribeProblemsTests(unittest.TestCase):
    def test_lists_errors_first_and_skips_passing_checks(self) -> None:
        text = describe_problems(
            [
                Check("power", WARNING, "Battery at 20%", "Plug in."),
                Check("memory", OK, "8 GiB of memory"),
                Check("disks", ERROR, "No disk to install on"),
            ]
        )
        self.assertEqual(text, "Problem: No disk to install on\nWarning: Battery at 20%\n  Plug in.")


if __name__ == "__main__":
    unittest.main()
