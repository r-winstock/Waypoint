"""Fills in city/country/country_code for Place rows that have a name but
never got address data at all.

Root cause (confirmed live via direct DB inspection, 463 affected rows on
the real dataset): _reverse_geocode() in app/geocoding.py caught any HTTP
failure (timeout, 429, 5xx) or bad JSON from Nominatim and returned None -
indistinguishable from "genuinely nothing here". resolve_place() then
permanently saved a Place row with city=None/country=None either way, no
retry, no way to tell the two cases apart afterwards. This mostly hit
scripts/import_google_timeline.py's one-off historical import, which ran
through thousands of brand-new coordinates continuously for hours - a
transient Nominatim failure somewhere in that run was inevitable.
_reverse_geocode() now retries once (see its own docstring/comment), which
reduces but doesn't eliminate the risk going forward; this script is the
one-off repair pass for rows already affected.

Deliberately ignores manually_corrected: that flag only ever reflects a
name/category correction (by a human, or scripts/backfill_google_places.py
sourcing a name from Google's Essentials-tier API, which never had address
data to offer in the first place - see app/google_places.py's own field-
mask docstring). City/country was never actually reviewed for these rows,
so backfilling it isn't overwriting a considered decision.

Only ever fills a gap - never touches name/category, never overwrites an
existing city/country value.

Usage:
    python scripts/backfill_missing_city_country.py [--apply]

Without --apply, prints the plan only (dry run) against the first 10 rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.geocoding import _reverse_geocode  # noqa: E402
from app.models import Place  # noqa: E402
from app.processing import _rebuild_trips  # noqa: E402


def run(apply: bool) -> None:
    init_db()
    with SessionLocal() as session:
        places = session.query(Place).filter(Place.city.is_(None)).order_by(Place.id).all()
        total = len(places)
        print(f"{total} places missing city{'' if apply else ' (dry run - first 10 only)'}")

        checked = updated = 0
        for place in places if apply else places[:10]:
            checked += 1
            data = _reverse_geocode(place.lat_round, place.lon_round)
            if data is None:
                continue

            address = data.get("address", {})
            city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
            country = address.get("country")
            code = address.get("country_code")
            country_code = code.upper() if code else None

            if not city and not country:
                continue

            print(f"place {place.id} ({place.name!r}): city={city!r} country={country!r}")
            if apply:
                if city:
                    place.city = city
                if country:
                    place.country = country
                if country_code:
                    place.country_code = country_code
            updated += 1

            if apply and checked % 50 == 0:
                session.commit()
                print(f"...{checked}/{total} checked, {updated} updated so far")

        if apply:
            session.commit()
            print(f"\nDone. Checked {checked}, updated {updated}.")
            print("Rebuilding computed trips so primary_city/primary_country pick up the fix...")
            _rebuild_trips(session)
            session.commit()
            print("Trips rebuilt.")
        else:
            print("\nDry run complete (first 10 only) - re-run with --apply for the full set.")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
