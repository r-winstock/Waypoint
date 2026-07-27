"""One-off re-resolve of every already-cached Place's name/city into English.

Nominatim returns address components in whatever language the underlying OSM
data was tagged in unless asked otherwise - every place resolved before
geocoding.py started sending Accept-Language: en on every live lookup is
stuck showing the local form ("Milano" instead of "Milan", a Dubai road name
in Arabic instead of transliterated/English). Confirmed live this wasn't
just cosmetic: Milan's own Wikipedia article is titled "Milan", so a photo
search for the literal string "Milano" never matched it at all - the city
had no card photo purely because of the language mismatch.

Re-queries Nominatim (Accept-Language: en) for each affected place's exact
already-stored lat_round/lon_round - same coordinate, so the result is the
same real place, just asked for in English this time. When the English
name/city differs from what's currently stored, the *old* (local) value is
kept as name_local/city_local for display as a subtitle, and the field
itself is overwritten with the English form. Never touches a place the user
has manually corrected. Self-throttled by geocoding._throttle the same as
every other Nominatim call, so this takes roughly 1 second per place -
expect ~20-30 minutes for a full history's worth of places.

Usage:
    python scripts/backfill_english_names.py [--apply]

Without --apply, prints the plan only (dry run) against a handful of
examples rather than re-querying everything, to sanity-check first.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.geocoding import _reverse_geocode  # noqa: E402
from app.models import Place  # noqa: E402


def run(apply: bool) -> None:
    init_db()
    with SessionLocal() as session:
        places = (
            session.query(Place)
            .filter(Place.manually_corrected.is_(False))
            .filter((Place.name.isnot(None)) | (Place.city.isnot(None)))
            .all()
        )
        total = len(places)
        print(f"{total} places to check{'' if apply else ' (dry run - first 10 only)'}")

        checked = updated = 0
        for place in places if apply else places[:10]:
            data = _reverse_geocode(place.lat_round, place.lon_round, language="en")
            checked += 1
            if data is None:
                continue
            address = data.get("address", {})
            new_name = data.get("name") or address.get("road") or data.get("display_name")
            new_city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")

            changed = False
            if new_name and place.name and new_name != place.name:
                print(f"place {place.id}: name {place.name!r} -> {new_name!r}")
                if apply:
                    place.name_local = place.name
                    place.name = new_name
                changed = True
            if new_city and place.city and new_city != place.city:
                print(f"place {place.id}: city {place.city!r} -> {new_city!r}")
                if apply:
                    place.city_local = place.city
                    place.city = new_city
                changed = True
            if changed:
                updated += 1

            if apply and checked % 100 == 0:
                session.commit()
                print(f"...{checked}/{total} checked, {updated} updated so far")

        if apply:
            session.commit()
            print(f"\nDone. Checked {checked}, updated {updated}.")
        else:
            print("\nDry run complete (first 10 only) - re-run with --apply for the full set.")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
