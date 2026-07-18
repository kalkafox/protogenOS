from pathlib import Path
import unittest

from protogenos_installer import ProfileError, ProfileRepository


PROFILES = Path(__file__).resolve().parents[1] / "profiles"


class ProfileRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ProfileRepository(PROFILES)

    def test_general_defaults_to_firefox(self) -> None:
        plan = self.repository.resolve("general")
        self.assertEqual(plan.selections["kernel"], ("arch",))
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

    def test_unpublished_custom_kernel_is_not_installable(self) -> None:
        with self.assertRaisesRegex(ProfileError, "not installable yet"):
            self.repository.resolve("gamer", {"kernel": ("performance",)})


if __name__ == "__main__":
    unittest.main()
