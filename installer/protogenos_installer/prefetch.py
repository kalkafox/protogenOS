"""Download a plan's packages in the background while the user keeps choosing.

The graphical installer starts a prefetch once the persona and applications
are known. reflector first ranks mirrors worldwide by speed into the
prefetch's own mirror list (the live system's list stays untouched; the
installation ranks again for the chosen country). pacman then downloads into a tmpfs cache with its own empty database,
so it resolves every dependency as a fresh system would and never touches the
live system's package database. When the installation starts, the prefetch
stops and the finished downloads are copied into the target's package cache,
where pacstrap finds them instead of downloading them again.

Packages are downloaded without signature checks; pacstrap verifies every
package against the new system's keyring before installing it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .mirrors import enable_parallel_downloads, reflector_command

PREFETCH_ROOT = Path("/tmp/protogenos-prefetch")
LIVE_MIRRORLIST = Path("/etc/pacman.d/mirrorlist")
PACKAGE_SUFFIXES = (".pkg.tar.zst", ".pkg.tar.xz")
# Headroom left free in RAM and tmpfs: the live system and its desktop run
# from memory too.
MEMORY_RESERVE = 1024**3
SPACE_RESERVE = 256 * 1024**2
# Below this, downloading ahead isn't worth starting.
MIN_BUDGET = 64 * 1024**2


@dataclass(slots=True)
class PrefetchStatus:
    # idle, preparing, downloading, done, skipped, failed, or stopped
    state: str = "idle"
    packages: int = 0
    # Everything the plan needs, and the part of it that fits in memory.
    total_bytes: int = 0
    planned_bytes: int = 0
    downloaded_bytes: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "packages": self.packages,
            "total_bytes": self.total_bytes,
            "planned_bytes": self.planned_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "reason": self.reason,
        }


def prefetch_config(config: str, *, multilib: bool, mirrorlist: Path | None = None) -> str:
    """The live pacman.conf with fast downloads and no signature checks.

    With mirrorlist, repositories use that ranked list instead of the live
    system's.
    """
    from .backend import enable_multilib

    text = enable_parallel_downloads(config)
    if multilib:
        text = enable_multilib(text)
    lines: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*LocalFileSigLevel\s*=", line):
            continue
        if re.match(r"^\s*SigLevel\s*=", line):
            line = "SigLevel = Never"
        elif mirrorlist is not None and re.match(
            rf"^\s*Include\s*=\s*{re.escape(str(LIVE_MIRRORLIST))}\s*$", line
        ):
            line = f"Include = {mirrorlist}"
        lines.append(line)
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class Download:
    name: str
    size: int
    filename: str


DOWNLOAD_FORMAT = "%n %s %f"


def parse_downloads(output: str) -> list[Download]:
    """Resolved packages from ``pacman -Sp --print-format '%n %s %f'``."""
    downloads: list[Download] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1].isdigit():
            downloads.append(Download(fields[0], int(fields[1]), fields[2]))
    return downloads


def choose_downloads(downloads: Sequence[Download], budget: int) -> list[Download]:
    """The largest packages that fit the budget: the most bytes saved later."""
    chosen: list[Download] = []
    for download in sorted(downloads, key=lambda item: item.size, reverse=True):
        if download.size <= budget:
            chosen.append(download)
            budget -= download.size
    return chosen


def available_memory(meminfo: Path = Path("/proc/meminfo")) -> int:
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def free_space(path: Path) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def is_package_file(path: Path) -> bool:
    return path.is_file() and path.name.endswith(PACKAGE_SUFFIXES)


class Prefetcher:
    """Runs at most one background download; a new plan replaces the old one."""

    def __init__(
        self,
        *,
        root: Path = PREFETCH_ROOT,
        pacman_config: Path = Path("/etc/pacman.conf"),
        online_check: Callable[[], bool] | None = None,
        memory: Callable[[], int] = available_memory,
        space: Callable[[Path], int] = free_space,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self.root = root
        self.cache = root / "pkg"
        self.database = root / "db"
        self.config_path = root / "pacman.conf"
        self.mirrorlist = root / "mirrorlist"
        self.pacman_config = pacman_config
        self.online_check = online_check
        self.memory = memory
        self.space = space
        self.popen = popen
        self._lock = threading.Lock()
        self._status = PrefetchStatus()
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._request: tuple[tuple[str, ...], bool] | None = None
        # File names of the current plan's packages, for progress.
        self._planned_files: frozenset[str] = frozenset()

    # -- control ------------------------------------------------------------

    def start(self, packages: Sequence[str], *, multilib: bool) -> None:
        """Begin (or restart) downloading; returns at once with state "preparing"."""
        request = (tuple(sorted(set(packages))), multilib)
        with self._lock:
            if request == self._request and self._status.state not in {"failed", "stopped"}:
                return
            self._request = request
            self._status = PrefetchStatus(state="preparing")
        # Stopping a previous download can take a few seconds; don't make
        # the caller wait for it.
        threading.Thread(target=self._restart, args=(request,), daemon=True).start()

    def _restart(self, request: tuple[tuple[str, ...], bool]) -> None:
        self._halt()
        with self._lock:
            if request != self._request:
                return  # a newer plan already replaced this one
            self._generation += 1
            generation = self._generation
            self._status = PrefetchStatus(state="preparing")
            self._thread = threading.Thread(
                target=self._run, args=(generation, request[0], request[1]), daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop downloading; packages that finished stay usable."""
        with self._lock:
            self._request = None
            if self._status.state in {"preparing", "downloading"}:
                self._status.state = "stopped"
        self._halt()

    def _halt(self) -> None:
        with self._lock:
            self._generation += 1
            process = self._process
            thread = self._thread
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=15)

    def status(self) -> dict[str, object]:
        with self._lock:
            status = PrefetchStatus(**self._status.to_dict())
        if status.state in {"downloading", "done", "stopped"}:
            with self._lock:
                planned = self._planned_files
            status.downloaded_bytes = sum(
                path.stat().st_size for path in self.downloaded() if path.name in planned
            )
        return status.to_dict()

    def downloaded(self) -> list[Path]:
        """Completed package files; partial downloads end in ``.part``."""
        if not self.cache.is_dir():
            return []
        return sorted(path for path in self.cache.iterdir() if is_package_file(path))

    # -- worker -------------------------------------------------------------

    def _current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def _finish(self, generation: int, state: str, reason: str = "") -> None:
        with self._lock:
            if generation == self._generation:
                self._status.state = state
                self._status.reason = reason

    def _pacman(self, generation: int, *args: str) -> subprocess.CompletedProcess[str] | None:
        (self.database / "db.lck").unlink(missing_ok=True)
        return self._command(
            generation,
            [
                "pacman",
                "--config", str(self.config_path),
                "--dbpath", str(self.database),
                "--cachedir", str(self.cache),
                "--noconfirm",
                *args,
            ],
        )

    def _command(
        self, generation: int, command: list[str]
    ) -> subprocess.CompletedProcess[str] | None:
        """Run a stoppable command; None when this prefetch was replaced."""
        with self._lock:
            if generation != self._generation:
                return None
            process = self.popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            self._process = process
        stdout, stderr = process.communicate()
        with self._lock:
            if self._process is process:
                self._process = None
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def _run(self, generation: int, packages: tuple[str, ...], multilib: bool) -> None:
        try:
            if self.online_check is not None and not self.online_check():
                self._finish(generation, "skipped", "no internet connection")
                return
            (self.database / "local").mkdir(parents=True, exist_ok=True)
            self.cache.mkdir(parents=True, exist_ok=True)

            # Ranked once per boot; a later plan reuses the list.
            if not self.mirrorlist.is_file():
                ranked = self._command(
                    generation, reflector_command(mirrorlist=str(self.mirrorlist))
                )
                if ranked is None:
                    return
                if ranked.returncode != 0 or not self.mirrorlist.is_file():
                    # reflector unreachable: download from the live list.
                    self.mirrorlist.unlink(missing_ok=True)
            mirrorlist = self.mirrorlist if self.mirrorlist.is_file() else None
            self.config_path.write_text(
                prefetch_config(
                    self.pacman_config.read_text(), multilib=multilib, mirrorlist=mirrorlist
                )
            )

            synced = self._pacman(generation, "-Sy")
            if synced is None:
                return
            if synced.returncode != 0:
                self._finish(generation, "failed", _last_line(synced.stderr))
                return

            sized = self._pacman(generation, "-Sp", "--print-format", DOWNLOAD_FORMAT, *packages)
            if sized is None:
                return
            if sized.returncode != 0:
                self._finish(generation, "failed", _last_line(sized.stderr))
                return
            downloads = parse_downloads(sized.stdout)
            present = {path.name for path in self.downloaded()}
            pending = [item for item in downloads if item.filename not in present]
            # tmpfs lives in RAM: both limits matter.
            budget = min(
                self.memory() - MEMORY_RESERVE, self.space(self.cache) - SPACE_RESERVE
            )
            chosen = choose_downloads(pending, budget) if budget >= MIN_BUDGET else []
            total = sum(item.size for item in downloads)
            planned = total - sum(item.size for item in pending) + sum(item.size for item in chosen)
            with self._lock:
                if generation != self._generation:
                    return
                self._status.packages = len(downloads)
                self._status.total_bytes = total
                self._status.planned_bytes = planned
                self._planned_files = frozenset(item.filename for item in downloads)
            if pending and not chosen:
                self._finish(generation, "skipped", "not enough free memory to download ahead")
                return
            partial = len(chosen) < len(pending)

            if chosen:
                with self._lock:
                    if generation != self._generation:
                        return
                    self._status.state = "downloading"
                # The list already holds every dependency; -dd keeps pacman
                # from adding the ones left out to save memory.
                downloaded = self._pacman(
                    generation, "-Sw", "--nodeps", "--nodeps", *(item.name for item in chosen)
                )
                if downloaded is None:
                    return
                if downloaded.returncode != 0:
                    self._finish(generation, "failed", _last_line(downloaded.stderr))
                    return
            if partial:
                self._finish(
                    generation, "done", "limited by free memory; the rest downloads while installing"
                )
                return
            self._finish(generation, "done")
        except (OSError, ValueError) as error:
            self._finish(generation, "failed", str(error))


def copy_prefetched(packages: Sequence[Path], cache: Path) -> tuple[int, int]:
    """Move finished downloads into a target cache; returns (count, bytes).

    Each package leaves the RAM-backed prefetch cache once copied, giving the
    memory back to the rest of the installation.
    """
    cache.mkdir(parents=True, exist_ok=True)
    count = size = 0
    for package in packages:
        if not is_package_file(package):
            continue
        destination = cache / package.name
        package_size = package.stat().st_size
        if not destination.exists():
            shutil.copyfile(package, destination)
            count += 1
            size += package_size
        package.unlink(missing_ok=True)
    return count, size


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else "pacman failed"
