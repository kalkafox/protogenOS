from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protogenos_installer import ProfileError, ProfileRepository
from protogenos_installer.cli import main
from protogenos_installer.profiles import load_options


PROFILES = Path(__file__).resolve().parents[1] / "profiles"


class ProfileRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ProfileRepository(PROFILES)

    def test_general_defaults_to_firefox(self) -> None:
        plan = self.repository.resolve("general")
        self.assertEqual(plan.selections["kernel"], ("linux",))
        self.assertIn("linux", plan.packages)
        self.assertEqual(plan.selections["browser"], ("firefox",))
        self.assertIn("firefox", plan.packages)
        self.assertFalse(plan.multilib_required)

    def test_browsers_are_any_of(self) -> None:
        plan = self.repository.resolve(
            "general", {"browser": ("firefox", "brave", "librewolf")}
        )
        self.assertEqual(
            plan.selections["browser"], ("firefox", "brave", "librewolf")
        )
        self.assertEqual(plan.aur_packages, ("brave-bin", "librewolf-bin"))

    def test_gamer_includes_lutris_and_requires_multilib(self) -> None:
        plan = self.repository.resolve(
            "gamer", {"gaming-launcher": ("steam", "lutris")}
        )
        self.assertIn("lutris", plan.packages)
        self.assertIn("steam", plan.packages)
        self.assertTrue(plan.multilib_required)

    def test_developer_layers_general_packages(self) -> None:
        plan = self.repository.resolve("developer")
        self.assertIn("discover", plan.packages)
        self.assertIn("base-devel", plan.packages)
        self.assertIn("neovim", plan.packages)

    def test_minimal_is_console_only(self) -> None:
        plan = self.repository.resolve("minimal")
        self.assertEqual(plan.packages, ("base", "linux-firmware", "networkmanager", "linux"))
        self.assertEqual(set(plan.selections), {"kernel"})
        self.assertEqual(plan.aur_packages, ())
        self.assertFalse(plan.desktop)

    def test_server_is_headless_with_lts_kernel_and_podman(self) -> None:
        plan = self.repository.resolve("server")
        self.assertFalse(plan.desktop)
        self.assertEqual(plan.selections["kernel"], ("linux-lts",))
        self.assertEqual(plan.selections["container"], ("podman",))
        for package in ("openssh", "firewalld", "linux-lts", "podman"):
            self.assertIn(package, plan.packages)
        for package in ("plasma-meta", "discover", "linux"):
            self.assertNotIn(package, plan.packages)
        self.assertEqual(set(plan.selections), {"kernel", "container"})

    def test_server_can_choose_docker_or_no_container_runtime(self) -> None:
        self.assertIn("docker", self.repository.resolve("server", {"container": ("docker",)}).packages)
        plan = self.repository.resolve("server", {"container": ()})
        self.assertNotIn("podman", plan.packages)

    def test_kernel_defaults_depend_on_persona(self) -> None:
        for persona in ("general", "gamer", "developer", "minimal"):
            with self.subTest(persona=persona):
                self.assertEqual(self.repository.resolve(persona).selections["kernel"], ("linux",))
        self.assertIn("linux-lts", {choice.identifier for group in self.repository.groups_for("general") for choice in group.choices})

    def test_persona_defaults_must_be_offered_to_those_personas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            options = Path(temporary) / "options.conf"
            options.write_text("kernel|one-of|linux|Linux|linux|official|server|general\n")
            with self.assertRaisesRegex(ProfileError, "subset of the profile list"):
                load_options(options)

    def test_desktop_personas_include_plasma(self) -> None:
        plan = self.repository.resolve("general")
        self.assertIn("plasma-meta", plan.packages)
        self.assertTrue(plan.desktop)

    def test_developer_editor_replaces_default(self) -> None:
        plan = self.repository.resolve("developer", {"editor": ("kate",)})
        self.assertIn("kate", plan.packages)
        self.assertNotIn("neovim", plan.packages)

    def test_invalid_choice_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProfileError, "invalid choices"):
            self.repository.resolve("general", {"browser": ("unknown",)})

    def test_future_dotfiles_package_is_not_installable(self) -> None:
        with self.assertRaisesRegex(ProfileError, "not installable yet"):
            self.repository.resolve("developer", {"dotfiles": ("dotconfig",)})

    def test_linux_zen_is_an_official_kernel_choice(self) -> None:
        plan = self.repository.resolve("gamer", {"kernel": ("linux-zen",)})
        self.assertEqual(plan.selections["kernel"], ("linux-zen",))
        self.assertIn("linux-zen", plan.packages)
        self.assertNotIn("linux", plan.packages)
        self.assertEqual(plan.aur_packages, ())

    def test_kernel_choice_cannot_be_empty(self) -> None:
        with self.assertRaisesRegex(ProfileError, "requires exactly one choice"):
            self.repository.resolve("general", {"kernel": ()})

    def test_installer_output_has_branded_title(self) -> None:
        output = StringIO()
        with patch("sys.stdout", output):
            result = main(["--persona", "general", "--non-interactive"])
        self.assertEqual(result, 0)
        self.assertIn("=== protogenOS Installer ===", output.getvalue())

    def test_interactive_installer_can_exit_to_shell(self) -> None:
        output = StringIO()
        with patch("sys.stdout", output), patch("builtins.input", return_value="0"):
            result = main([])
        self.assertEqual(result, 0)
        self.assertIn("0. Exit to shell", output.getvalue())
        self.assertIn("Installer closed", output.getvalue())

    def test_keyboard_interrupt_exits_without_traceback(self) -> None:
        output = StringIO()
        with patch("builtins.input", side_effect=KeyboardInterrupt), patch(
            "sys.stdout", output
        ):
            result = main([])
        self.assertEqual(result, 130)
        self.assertIn("Installation interrupted", output.getvalue())


if __name__ == "__main__":
    unittest.main()
