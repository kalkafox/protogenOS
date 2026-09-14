import unittest

from protogenos_installer.features import feature_settings, offered_features
from protogenos_installer.hardware import FeatureSupport, HardwareProfile


def _offers(persona: str = "general", **overrides):
    values = dict(
        filesystem="btrfs",
        btrfs_subvolumes=True,
        encrypt=False,
        firmware="uefi",
        bootloader="grub",
        hardware=HardwareProfile(),
        support=FeatureSupport(),
    )
    values.update(overrides)
    return {offer.key: offer for offer in offered_features(persona, **values)}


class FeatureOfferTests(unittest.TestCase):
    def test_defaults_follow_persona(self) -> None:
        general = _offers()
        self.assertTrue(general["snapshots"].default)
        self.assertTrue(general["flatpak"].default)
        self.assertFalse(general["gaming_tweaks"].default)
        self.assertTrue(_offers("gamer")["gaming_tweaks"].default)
        self.assertFalse(_offers("minimal")["flatpak"].default)

    def test_hardware_dependent_offers_are_hidden_without_support(self) -> None:
        offers = _offers(encrypt=True)
        for key in ("nvidia_open", "fingerprint", "tpm2_unlock", "secure_boot"):
            self.assertNotIn(key, offers)

    def test_hardware_dependent_offers_appear_with_support(self) -> None:
        offers = _offers(
            encrypt=True,
            bootloader="systemd-boot",
            hardware=HardwareProfile(nvidia_open_supported=True),
            support=FeatureSupport(tpm2=True, fingerprint_reader=True, secure_boot_setup_mode=True),
        )
        self.assertTrue(offers["nvidia_open"].default)
        self.assertTrue(offers["fingerprint"].default)
        self.assertFalse(offers["tpm2_unlock"].default)
        self.assertIn("enrolls keys now", offers["secure_boot"].detail)

    def test_snapshots_need_btrfs_subvolumes(self) -> None:
        self.assertNotIn("snapshots", _offers(btrfs_subvolumes=False))
        self.assertNotIn("snapshots", _offers(filesystem="ext4"))

    def test_settings_map_to_install_config_fields(self) -> None:
        settings = feature_settings(["nvidia_open", "secure_boot"])
        self.assertEqual(settings["nvidia_driver"], "nvidia-open")
        self.assertTrue(settings["secure_boot"])
        self.assertFalse(settings["snapshots"])
        self.assertEqual(feature_settings([])["nvidia_driver"], "nouveau")


if __name__ == "__main__":
    unittest.main()
