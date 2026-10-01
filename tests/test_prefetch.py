import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from protogenos_installer.backend import prefetch_packages
from protogenos_installer.models import InstallPlan
from protogenos_installer.prefetch import (
    Prefetcher,
    copy_prefetched,
    Download,
    choose_downloads,
    parse_downloads,
    prefetch_config,
)

PACMAN_CONF = """[options]
#ParallelDownloads = 5
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

[core]
Include = /etc/pacman.d/mirrorlist

#[multilib]
#Include = /etc/pacman.d/mirrorlist
"""


class FakeProcess:
    def __init__(self, command, stdout="", returncode=0, on_run=None):
        self.command = command
        self.stdout = stdout
        self.returncode = returncode
        self.on_run = on_run

    def communicate(self):
        if self.on_run is not None:
            self.on_run(self.command)
        return self.stdout, ""

    def poll(self):
        return self.returncode

    def terminate(self):
        pass


def _settle(prefetcher: Prefetcher) -> None:
    """Wait for the background download to reach a final state."""
    deadline = time.monotonic() + 5
    while prefetcher.status()["state"] in {"preparing", "downloading"}:
        if time.monotonic() > deadline:
            raise AssertionError("prefetch did not settle")
        time.sleep(0.01)


class PrefetchConfigTests(unittest.TestCase):
    def test_config_skips_signatures_and_enables_multilib(self) -> None:
        text = prefetch_config(PACMAN_CONF, multilib=True)
        self.assertIn("SigLevel = Never", text)
        self.assertNotIn("Required", text)
        self.assertNotIn("LocalFileSigLevel", text)
        self.assertIn("ParallelDownloads = 15", text)
        self.assertIn("[multilib]\nInclude = /etc/pacman.d/mirrorlist", text)

    def test_config_points_repositories_at_ranked_mirrors(self) -> None:
        text = prefetch_config(PACMAN_CONF, multilib=True, mirrorlist=Path("/tmp/p/mirrorlist"))
        self.assertNotIn("/etc/pacman.d/mirrorlist", text)
        self.assertEqual(text.count("Include = /tmp/p/mirrorlist"), 2)

    def test_download_sizes(self) -> None:
        self.assertEqual(
            parse_downloads("foo 100 foo-1-1-x86_64.pkg.tar.zst\nwarning: noise\n"),
            [Download("foo", 100, "foo-1-1-x86_64.pkg.tar.zst")],
        )

    def test_largest_packages_that_fit_are_chosen(self) -> None:
        items = [Download("a", 50, "a"), Download("b", 300, "b"), Download("c", 120, "c"), Download("d", 90, "d")]
        self.assertEqual([item.name for item in choose_downloads(items, 400)], ["b", "d"])


class PrefetcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "prefetch"
        self.config = Path(self.temporary.name) / "pacman.conf"
        self.config.write_text(PACMAN_CONF)
        self.commands: list[list[str]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _popen(self, command, **_kwargs):
        self.commands.append(command)
        if command[0] == "reflector":
            def rank(_command):
                Path(command[command.index("--save") + 1]).write_text("Server = https://fast/$repo/os/$arch\n")
            return FakeProcess(command, on_run=rank)
        if "-Sp" in command:
            return FakeProcess(
                command,
                stdout="foo 1000 foo-1-1-x86_64.pkg.tar.zst\nbar 2000 bar-1-1-x86_64.pkg.tar.zst\n",
            )
        if "-Sw" in command:
            def download(_command):
                (self.root / "pkg/foo-1-1-x86_64.pkg.tar.zst").write_bytes(b"x" * 1000)
                (self.root / "pkg/bar-1-1-x86_64.pkg.tar.zst.part").write_bytes(b"x" * 10)
            return FakeProcess(command, on_run=download)
        return FakeProcess(command)

    def _prefetcher(self, memory: int = 64 * 1024**3, space: int = 64 * 1024**3) -> Prefetcher:
        return Prefetcher(
            root=self.root,
            pacman_config=self.config,
            memory=lambda: memory,
            space=lambda _path: space,
            popen=self._popen,
        )

    def test_downloads_into_its_own_database_and_cache(self) -> None:
        prefetcher = self._prefetcher()
        prefetcher.start(["foo", "bar"], multilib=False)
        _settle(prefetcher)
        status = prefetcher.status()
        self.assertEqual(status["state"], "done")
        self.assertEqual((status["packages"], status["total_bytes"]), (2, 3000))
        self.assertEqual(status["downloaded_bytes"], 1000)
        self.assertEqual([path.name for path in prefetcher.downloaded()], ["foo-1-1-x86_64.pkg.tar.zst"])
        self.assertEqual(self.commands[0][0], "reflector")
        self.assertIn(f"Include = {self.root / 'mirrorlist'}", (self.root / "pacman.conf").read_text())
        download = self.commands[-1]
        self.assertIn("-Sw", download)
        self.assertEqual(download[download.index("--dbpath") + 1], str(self.root / "db"))
        self.assertEqual(download[download.index("--cachedir") + 1], str(self.root / "pkg"))

    def test_downloads_only_what_fits_in_memory(self) -> None:
        from protogenos_installer.prefetch import MEMORY_RESERVE

        import protogenos_installer.prefetch as prefetch_module

        # Room for bar (2000 bytes) but not both.
        prefetcher = self._prefetcher(memory=MEMORY_RESERVE + 2500)
        original = prefetch_module.MIN_BUDGET
        prefetch_module.MIN_BUDGET = 0
        try:
            prefetcher.start(["foo", "bar"], multilib=False)
            _settle(prefetcher)
        finally:
            prefetch_module.MIN_BUDGET = original
        status = prefetcher.status()
        self.assertEqual(status["state"], "done")
        self.assertIn("limited by free memory", status["reason"])
        self.assertEqual((status["total_bytes"], status["planned_bytes"]), (3000, 2000))
        download = self.commands[-1]
        self.assertEqual(download[-4:], ["-Sw", "--nodeps", "--nodeps", "bar"])

    def test_skips_when_memory_is_short(self) -> None:
        prefetcher = self._prefetcher(memory=1024**3)
        prefetcher.start(["foo"], multilib=False)
        _settle(prefetcher)
        self.assertEqual(prefetcher.status()["state"], "skipped")
        self.assertFalse(any("-Sw" in command for command in self.commands))

    def test_start_reports_preparing_immediately(self) -> None:
        prefetcher = self._prefetcher()
        prefetcher.start(["foo"], multilib=False)
        self.assertIn(prefetcher.status()["state"], {"preparing", "downloading", "done"})
        _settle(prefetcher)

    def test_stop_cancels_a_pending_start(self) -> None:
        prefetcher = self._prefetcher()
        prefetcher.start(["foo"], multilib=False)
        prefetcher.stop()
        time.sleep(0.2)
        self.assertNotIn(prefetcher.status()["state"], {"preparing", "downloading"})

    def test_same_plan_is_not_restarted(self) -> None:
        prefetcher = self._prefetcher()
        prefetcher.start(["foo"], multilib=False)
        _settle(prefetcher)
        count = len(self.commands)
        prefetcher.start(["foo"], multilib=False)
        self.assertEqual(len(self.commands), count)

    def test_ranks_mirrors_once_and_falls_back_to_live_list(self) -> None:
        def failing_reflector(command, **kwargs):
            if command[0] == "reflector":
                self.commands.append(command)
                return FakeProcess(command, returncode=1)
            return self._popen(command, **kwargs)

        prefetcher = self._prefetcher()
        prefetcher.popen = failing_reflector
        prefetcher.start(["foo"], multilib=False)
        _settle(prefetcher)
        self.assertEqual(prefetcher.status()["state"], "done")
        self.assertIn("Include = /etc/pacman.d/mirrorlist", (self.root / "pacman.conf").read_text())

        prefetcher.popen = self._popen
        prefetcher.start(["foo", "bar"], multilib=False)
        _settle(prefetcher)
        prefetcher.start(["bar"], multilib=False)
        _settle(prefetcher)
        rankings = [command for command in self.commands if command[0] == "reflector"]
        self.assertEqual(len(rankings), 2)

    def test_copy_skips_partial_downloads(self) -> None:
        source = Path(self.temporary.name) / "source"
        source.mkdir()
        done = source / "foo-1-1-x86_64.pkg.tar.zst"
        done.write_bytes(b"abc")
        partial = source / "bar-1-1-x86_64.pkg.tar.zst.part"
        partial.write_bytes(b"ab")
        cache = Path(self.temporary.name) / "target/var/cache/pacman/pkg"
        self.assertEqual(copy_prefetched([done, partial], cache), (1, 3))
        self.assertEqual([path.name for path in cache.iterdir()], [done.name])
        # Copied packages leave RAM; partial downloads are left alone.
        self.assertFalse(done.exists())
        self.assertTrue(partial.exists())


class RankedMirrorReuseTests(unittest.TestCase):
    def test_install_reuses_ranked_list_without_a_country(self) -> None:
        from types import SimpleNamespace

        from protogenos_installer.backend import CommandRunner, InstallerBackend

        with tempfile.TemporaryDirectory() as directory:
            ranked = Path(directory) / "ranked"
            ranked.write_text("Server = https://fast/$repo/os/$arch\n")
            live = Path(directory) / "mirrorlist"
            live.write_text("Server = https://slow/$repo/os/$arch\n")
            ran: list[list[str]] = []

            class Runner(CommandRunner):
                def run(self, args, **_kwargs):
                    ran.append(list(args))
                    return subprocess.CompletedProcess(args, 0, "", "")

                def emit(self, line):
                    pass

            backend = InstallerBackend(
                Runner(), target_root=Path(directory) / "mnt", require_root=False,
                ranked_mirrorlist=ranked, live_mirrorlist=live,
            )
            backend._select_mirrors(SimpleNamespace(mirror_country=""))
            self.assertEqual(live.read_text(), ranked.read_text())
            self.assertEqual(ran, [])
            backend._select_mirrors(SimpleNamespace(mirror_country="Germany"))
            self.assertEqual(ran[0][0], "reflector")


class PrefetchPackagesTests(unittest.TestCase):
    def test_leaves_out_aur_packages_and_adds_build_tools(self) -> None:
        plan = InstallPlan(
            persona="general",
            packages=("base", "firefox", "brave-bin"),
            selections={},
            aur_packages=("brave-bin",),
            multilib_required=False,
        )
        packages = prefetch_packages(plan)
        self.assertNotIn("brave-bin", packages)
        self.assertIn("firefox", packages)
        self.assertIn("sudo", packages)
        self.assertIn("base-devel", packages)


if __name__ == "__main__":
    unittest.main()
