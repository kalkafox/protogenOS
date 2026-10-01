import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from protogenos_installer.profiles import load_package_manifest

ROOT = Path(__file__).resolve().parents[1]
LXC_PACKAGES = ROOT / "config/lxc.packages"
OVERLAY = ROOT / "overlays/lxc"
TEST_SCRIPT = ROOT / "scripts/test-lxc"

# Server persona packages that cannot work, or are replaced, in a container.
CONTAINER_EXCLUDED = {"linux-firmware", "networkmanager", "firewalld", "smartmontools"}


class LxcPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.packages = set(load_package_manifest(LXC_PACKAGES))

    def test_matches_server_persona_without_hardware_packages(self) -> None:
        server = set(load_package_manifest(ROOT / "profiles/base.packages"))
        server |= set(load_package_manifest(ROOT / "profiles/server.packages"))
        self.assertEqual(server - CONTAINER_EXCLUDED - self.packages, set())

    def test_has_no_kernel_firmware_or_bootloader(self) -> None:
        forbidden = CONTAINER_EXCLUDED | {
            "linux", "linux-lts", "linux-zen", "grub", "efibootmgr", "mkinitcpio",
        }
        self.assertEqual(self.packages & forbidden, set())

    def test_ships_docker(self) -> None:
        self.assertLessEqual({"docker", "docker-compose", "openssh"}, self.packages)

    def test_ships_editors(self) -> None:
        self.assertLessEqual({"nano", "vim"}, self.packages)


class LxcBuildScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (ROOT / "scripts/container-build-lxc").read_text()

    def test_disables_pacman_sandbox(self) -> None:
        for setting in ("DisableSandboxFilesystem", "DisableSandboxSyscalls"):
            self.assertIn(f"s/^#\\?{setting}$/{setting}/", self.script)

    def test_links_vi_to_vim(self) -> None:
        self.assertIn('ln -sf ../../bin/vim "${rootfs}/usr/local/bin/vi"', self.script)

    def test_first_boot_does_not_prompt_on_the_console(self) -> None:
        self.assertIn('mask systemd-firstboot.service', self.script)

    def test_masks_units_an_unprivileged_container_cannot_start(self) -> None:
        for unit in ("sys-kernel-config.mount", "sys-kernel-debug.mount", "systemd-journald-audit.socket"):
            self.assertIn(unit, self.script)
        self.assertIn('ln -sf ../usr/share/zoneinfo/UTC "${rootfs}/etc/localtime"', self.script)

    def test_enables_sshd(self) -> None:
        self.assertIn("sshd.service", self.script)


class LxcOverlayTests(unittest.TestCase):
    def test_root_logs_in_with_password_or_keys(self) -> None:
        config = (OVERLAY / "etc/ssh/sshd_config.d/10-protogenos.conf").read_text()
        self.assertIn("PermitRootLogin yes", config)
        self.assertIn("PasswordAuthentication yes", config)

    def test_keyring_is_created_on_first_boot(self) -> None:
        unit = (OVERLAY / "etc/systemd/system/protogenos-pacman-init.service").read_text()
        self.assertIn("ConditionPathExists=!/etc/pacman.d/gnupg/trustdb.gpg", unit)
        self.assertIn("ExecStart=/usr/bin/pacman-key --populate archlinux", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_network_wait_does_not_stall_boot(self) -> None:
        dropin = OVERLAY / "etc/systemd/system/systemd-networkd-wait-online.service.d/10-protogenos.conf"
        self.assertIn("--any --timeout=30", dropin.read_text())

    def test_prompt_only_replaces_arch_default(self) -> None:
        prompt = OVERLAY / "usr/share/protogenos/bash-prompt.sh"
        for initial, changed in (("[\\u@\\h \\W]\\$ ", True), ("custom> ", False)):
            result = subprocess.run(
                ["bash", "-c", f'PS1="$1"; . {prompt}; printf %s "$PS1"', "bash", initial],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(result.stdout != initial, changed, initial)


    def test_nesting_warning_only_when_journald_lacks_credentials(self) -> None:
        check = OVERLAY / "usr/share/protogenos/nesting-check.sh"
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "systemctl"
            for status, warned in (("243", True), ("0", False)):
                fake.write_text(f"#!/bin/sh\necho {status}\n")
                fake.chmod(0o755)
                result = subprocess.run(
                    ["bash", "-ic", f". {check}"], capture_output=True, text=True,
                    env=os.environ | {"PATH": f"{directory}:/usr/bin:/bin", "HOME": directory},
                )
                self.assertEqual("nesting" in result.stderr, warned, status)


class TestLxcScriptTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(TEST_SCRIPT), *args], capture_output=True, text=True, env=os.environ | {"PATH": "/usr/bin:/bin"},
        )

    def test_dry_run_creates_unprivileged_nested_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "protogenos-server_20260101-1_amd64.tar.zst"
            template.touch()
            result = self.run_script("--dry-run", "--host", "root@pve", "--vmid", "900", "--template", str(template))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ssh root@pve pct create 900 local:vztmpl/protogenos-server_20260101-1_amd64.tar.zst", result.stdout)
        self.assertIn("--unprivileged 1", result.stdout)
        self.assertIn("--features nesting=1\\,keyctl=1", result.stdout)
        self.assertIn("--ostype archlinux", result.stdout)
        self.assertNotIn("destroy", result.stdout)

    def test_rejects_non_numeric_vmid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "protogenos-server_20260101-1_amd64.tar.zst"
            template.touch()
            result = self.run_script("--dry-run", "--vmid", "abc", "--template", str(template))
        self.assertEqual(result.returncode, 2)

    def test_requires_a_template(self) -> None:
        result = self.run_script("--dry-run", "--template", "/nonexistent.tar.zst")
        self.assertEqual(result.returncode, 1)
        self.assertIn("No LXC template found", result.stderr)


if __name__ == "__main__":
    unittest.main()
