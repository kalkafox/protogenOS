import subprocess
import tempfile
import unittest
from pathlib import Path

from protogenos_installer.backend import CommandRunner, InstallConfig, InstallError
from protogenos_installer.storage import (
    GIB,
    MIB,
    SHRINK_HEADROOM,
    DiskLayout,
    PartitionInfo,
    StorageError,
    StorageManager,
    parse_ext4_minimum,
    parse_ntfs_minimum,
    shrink_info,
)

NTFS_INFO = """ntfsresize v2026.9.28 (libntfs-3g)
Device name        : /dev/sda3
NTFS volume version: 3.1
Cluster size       : 4096 bytes
Current volume size: 214748364288 bytes (214749 MB)
Space in use       : 64424 MB (30.0%)
You might resize at 64424509440 bytes or 64425 MB (freeing 150324 MB).
"""
HIBERNATED = """ntfsresize v2026.9.28 (libntfs-3g)
The NTFS partition is in an unsafe state. Please resume and shutdown
Windows fully (no hibernation or fast restarting), or mount the volume
read-only with the 'ro' mount option.
"""


class FakeRunner(CommandRunner):
    def __init__(self, outputs: dict[str, tuple[int, str]] | None = None) -> None:
        super().__init__()
        self.outputs = outputs or {}
        self.commands: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.lines: list[str] = []

    def run(self, args, *, input_text=None, capture_output=False, check=True):
        self.commands.append(list(args))
        self.inputs.append(input_text)
        code, stdout = self.outputs.get(args[0], (0, ""))
        return subprocess.CompletedProcess(args, code, stdout, "")

    def emit(self, line: str) -> None:
        self.lines.append(line)


def _layout(fstype: str = "ntfs", mountpoints: tuple[str, ...] = ()) -> DiskLayout:
    part = PartitionInfo(
        number=3, path="/dev/sda3", start=300 * MIB, end=300 * MIB + 200 * GIB - 1,
        size=200 * GIB, fstype=fstype, mountpoints=mountpoints,
    )
    return DiskLayout("/dev/sda", 256 * GIB, "gpt", 512, (part,), ())


class ShrinkInfoTests(unittest.TestCase):
    def test_parsers(self) -> None:
        self.assertEqual(parse_ntfs_minimum(NTFS_INFO), 64424509440)
        self.assertIsNone(parse_ntfs_minimum(HIBERNATED))
        self.assertEqual(
            parse_ext4_minimum(
                "Estimated minimum size of the filesystem: 1000\n", "Block count: 9\nBlock size:               4096\n"
            ),
            4096000,
        )

    def test_ntfs_minimum_includes_headroom(self) -> None:
        runner = FakeRunner({"ntfsresize": (0, NTFS_INFO)})
        info = shrink_info(runner, _layout(), "/dev/sda3")
        self.assertTrue(info.shrinkable)
        self.assertGreaterEqual(info.smallest_size, 64424509440 + SHRINK_HEADROOM)
        self.assertEqual(info.smallest_size % MIB, 0)
        self.assertEqual(runner.commands[0][:4], ["ntfsresize", "--info", "--force", "--no-action"])

    def test_hibernated_windows_explains_fast_startup(self) -> None:
        runner = FakeRunner({"ntfsresize": (1, HIBERNATED)})
        info = shrink_info(runner, _layout(), "/dev/sda3")
        self.assertFalse(info.shrinkable)
        self.assertIn("Fast Startup", info.reason)

    def test_unsupported_and_mounted_partitions(self) -> None:
        self.assertIn("BitLocker", shrink_info(FakeRunner(), _layout("BitLocker"), "/dev/sda3").reason)
        self.assertIn("can't be shrunk", shrink_info(FakeRunner(), _layout("xfs"), "/dev/sda3").reason)
        self.assertIn("mounted", shrink_info(FakeRunner(), _layout("ext4", ("/mnt",)), "/dev/sda3").reason)


class ShrinkConfigTests(unittest.TestCase):
    def _config(self, **overrides) -> InstallConfig:
        values = dict(
            disk="/dev/sda", hostname="proto", username="fox", user_password="pw",
            firmware="uefi", disk_layout="free-space", shrink_partition="/dev/sda3", shrink_size=100 * GIB,
        )
        values.update(overrides)
        return InstallConfig(**values)

    def test_shrinking_requires_alongside_layout_and_own_partition(self) -> None:
        with self.assertRaisesRegex(InstallError, "alongside"):
            self._config(disk_layout="erase")._validate_storage()
        with self.assertRaisesRegex(InstallError, "not a partition"):
            self._config(shrink_partition="/dev/sdb1")._validate_storage()
        self._config()._validate_storage()


class ShrinkStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manager(self, runner: FakeRunner) -> StorageManager:
        return StorageManager(runner, Path(self.temporary.name))

    def _config(self, size: int) -> InstallConfig:
        return InstallConfig(
            disk="/dev/sda", hostname="proto", username="fox", user_password="pw", firmware="uefi",
            disk_layout="free-space", shrink_partition="/dev/sda3", shrink_size=size,
        )

    def test_ntfs_is_test_run_then_shrunk_then_partition_resized(self) -> None:
        runner = FakeRunner({"ntfsresize": (0, NTFS_INFO)})
        manager = self._manager(runner)
        import protogenos_installer.storage as storage

        original = storage.read_disk_layout
        storage.read_disk_layout = lambda _runner, _disk: _layout()
        try:
            manager._shrink_partition(self._config(100 * GIB + 123))
        finally:
            storage.read_disk_layout = original
        resizes = [command for command in runner.commands if command[0] == "ntfsresize"][1:]
        self.assertEqual(resizes[0], ["ntfsresize", "--no-action", "--size", str(100 * GIB), "/dev/sda3"])
        self.assertEqual(resizes[1], ["ntfsresize", "--force", "--size", str(100 * GIB), "/dev/sda3"])
        sfdisk = runner.commands.index(["sfdisk", "--no-reread", "-N", "3", "/dev/sda"])
        self.assertGreater(sfdisk, runner.commands.index(resizes[1]))
        self.assertEqual(runner.inputs[sfdisk], f", {100 * GIB // 512}\n")

    def test_refuses_to_shrink_below_the_minimum(self) -> None:
        runner = FakeRunner({"ntfsresize": (0, NTFS_INFO)})
        import protogenos_installer.storage as storage

        original = storage.read_disk_layout
        storage.read_disk_layout = lambda _runner, _disk: _layout()
        try:
            with self.assertRaisesRegex(StorageError, "can't be smaller"):
                self._manager(runner)._shrink_partition(self._config(10 * GIB))
            with self.assertRaisesRegex(StorageError, "frees less"):
                self._manager(runner)._shrink_partition(self._config(190 * GIB))
        finally:
            storage.read_disk_layout = original
        self.assertFalse(any(command[:2] == ["ntfsresize", "--force"] for command in runner.commands))


if __name__ == "__main__":
    unittest.main()
