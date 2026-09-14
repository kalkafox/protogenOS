import tempfile
import unittest
from pathlib import Path

from protogenos_installer.locales import suggest_locale


class LocaleSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.zone_tab = root / "zone.tab"
        self.zone_tab.write_text(
            "# comment\n"
            "DE\t+5230+01322\tEurope/Berlin\n"
            "CA\t+4339-07923\tAmerica/Toronto\tEastern\n"
            "CH\t+4723+00832\tEurope/Zurich\n"
            "BR\t-2332-04637\tAmerica/Sao_Paulo\n"
        )
        self.supported = root / "SUPPORTED"
        self.supported.write_text(
            "de_DE.UTF-8 UTF-8\nde_DE ISO-8859-1\nde_CH.UTF-8 UTF-8\nfr_CH.UTF-8 UTF-8\n"
            "it_CH.UTF-8 UTF-8\nen_CA.UTF-8 UTF-8\nfr_CA.UTF-8 UTF-8\npt_BR.UTF-8 UTF-8\n"
            "en_US.UTF-8 UTF-8\nfr_FR.UTF-8 UTF-8\nca_ES.UTF-8@valencia UTF-8\n"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _suggest(self, timezone: str, layout: str = "") -> str:
        return suggest_locale(timezone, layout, zone_tab=self.zone_tab, supported=self.supported)

    def test_country_primary_language(self) -> None:
        self.assertEqual(self._suggest("Europe/Berlin", "us"), "de_DE.UTF-8")
        self.assertEqual(self._suggest("America/Sao_Paulo"), "pt_BR.UTF-8")

    def test_keyboard_picks_between_country_languages(self) -> None:
        self.assertEqual(self._suggest("America/Toronto", "us"), "en_CA.UTF-8")
        self.assertEqual(self._suggest("America/Toronto", "ca"), "fr_CA.UTF-8")
        self.assertEqual(self._suggest("Europe/Zurich", "fr"), "fr_CH.UTF-8")

    def test_default_layout_uses_country_language(self) -> None:
        self.assertEqual(self._suggest("Europe/Zurich", "us"), "de_CH.UTF-8")
        self.assertEqual(self._suggest("Europe/Zurich", "it"), "it_CH.UTF-8")

    def test_utc_falls_back_to_keyboard_then_english(self) -> None:
        self.assertEqual(self._suggest("UTC", "fr"), "fr_FR.UTF-8")
        self.assertEqual(self._suggest("UTC", "us"), "en_US.UTF-8")
        self.assertEqual(self._suggest("UTC"), "en_US.UTF-8")


if __name__ == "__main__":
    unittest.main()
