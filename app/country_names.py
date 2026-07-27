"""English display names for the country codes actually seen in this app's
own data, keyed by ISO 3166-1 alpha-2 (already what Place.country_code
stores). Deliberately scoped to real visited countries rather than a full
world list - the World tab only ever needs to display a country the app has
already resolved a place in, and Place.country (Nominatim's own, sometimes
local-language, name) is always kept as the fallback/subtitle for anything
not in this table, so a newly-visited country not yet added here still
displays correctly, just without an English override.
"""
from __future__ import annotations

COUNTRY_NAMES_EN: dict[str, str] = {
    "AE": "United Arab Emirates",
    "AT": "Austria",
    "AU": "Australia",
    "BS": "The Bahamas",
    "CA": "Canada",
    "DE": "Germany",
    "DO": "Dominican Republic",
    "ES": "Spain",
    "FR": "France",
    "GB": "United Kingdom",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IN": "India",
    "IT": "Italy",
    "LT": "Lithuania",
    "NL": "Netherlands",
    "NZ": "New Zealand",
    "PL": "Poland",
    "PT": "Portugal",
    "US": "United States",
    "ZA": "South Africa",
}


def country_name_en(country_code: str | None, fallback: str | None) -> str | None:
    if country_code and country_code.upper() in COUNTRY_NAMES_EN:
        return COUNTRY_NAMES_EN[country_code.upper()]
    return fallback
