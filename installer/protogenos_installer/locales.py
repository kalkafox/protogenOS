"""Suggest a system locale from the chosen timezone and keyboard layout."""

from __future__ import annotations

from pathlib import Path

ZONE_TAB = Path("/usr/share/zoneinfo/zone.tab")
SUPPORTED_LOCALES = Path("/usr/share/i18n/SUPPORTED")
DEFAULT_LOCALE = "en_US.UTF-8"

# XKB layouts (and country codes) whose code is not their language code.
_LAYOUT_LANGUAGES = {
    "us": "en", "gb": "en", "ie": "en", "au": "en", "za": "en", "nz": "en",
    "br": "pt", "latam": "es", "ca": "fr", "ch": "de", "be": "nl", "at": "de",
    "se": "sv", "dk": "da", "no": "nb", "ee": "et", "cz": "cs", "gr": "el",
    "ua": "uk", "by": "be", "jp": "ja", "kr": "ko", "cn": "zh", "tw": "zh",
    "il": "he", "ir": "fa", "vn": "vi", "si": "sl", "rs": "sr", "in": "hi",
    "ge": "ka", "am": "hy", "kz": "kk", "pk": "ur", "ara": "ar", "dz": "ar",
    "ma": "ar", "eg": "ar", "iq": "ar", "sy": "ar", "epo": "eo",
}


def timezone_country(timezone: str, zone_tab: Path = ZONE_TAB) -> str | None:
    try:
        lines = zone_tab.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 3 and fields[2] == timezone:
            return fields[0].upper()
    return None


def supported_utf8_locales(supported: Path = SUPPORTED_LOCALES) -> tuple[str, ...]:
    try:
        lines = supported.read_text().splitlines()
    except OSError:
        return ()
    names = (line.split()[0] for line in lines if line.strip() and not line.startswith("#"))
    # Skip modifier variants such as ca_ES.UTF-8@valencia.
    return tuple(name for name in names if name.endswith(".UTF-8") and "@" not in name)


def suggest_locale(
    timezone: str,
    keyboard_layout: str = "",
    *,
    zone_tab: Path = ZONE_TAB,
    supported: Path = SUPPORTED_LOCALES,
) -> str:
    locales = supported_utf8_locales(supported)
    language = _LAYOUT_LANGUAGES.get(keyboard_layout, keyboard_layout)
    country = timezone_country(timezone, zone_tab)
    if country:
        in_country = {name for name in locales if name.split(".")[0].split("_")[-1] == country}
        # The keyboard's language first, then the country's main language:
        # the live session defaults to "us", so the timezone often says more.
        primary = _LAYOUT_LANGUAGES.get(country.lower(), country.lower())
        for preferred in (language, primary, "en"):
            match = f"{preferred}_{country}.UTF-8"
            if preferred and match in in_country:
                return match
    if language not in {"", "en"}:
        # No usable timezone (e.g. UTC): fall back to the layout's main locale.
        for name in locales:
            if name == f"{language}_{language.upper()}.UTF-8":
                return name
        matches = sorted(name for name in locales if name.startswith(f"{language}_"))
        if matches:
            return matches[0]
    return DEFAULT_LOCALE
