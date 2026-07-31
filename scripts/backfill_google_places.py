"""Resolves each already-imported place's Google Timeline placeId against the
real Google Places API (New), overwriting the OSM/Nominatim-derived name (and,
where the current category is still a generic bucket, the category) with
Google's own data.

Motivated by how much manual "Fix this place" correction was still needed
after the OSM-based taxonomy work: a Nominatim reverse-geocode at a business's
coordinates is frequently a nearby road/building rather than the business
itself (see geocoding.py's own "honest limitation" note on this), whereas
Google's Places database - built from the same source Google Timeline used
to record the visit in the first place - already knows the real business
name most of the time.

Shares its Places API client (app/google_places.py) with the live
resolve_place pipeline and the "Fix this place" modal's Google-sourced
nearby list - see that module's own docstring for the Essentials-tier
field mask discipline this all depends on to stay free/cheap.

Only touches Place rows with a google_place_id and manually_corrected=False
- never overwrites a place a user has already corrected by hand, and treats
its own updates the same way afterwards (sets manually_corrected=True on any
row it changes) so a later OSM-based reclassify pass doesn't revert a
Google-sourced category back to a weaker OSM-tag guess.

Category is only ever replaced when the place is still sitting in a generic
bucket ("Other places", "Streets and roads", or "Transport") - a category
the OSM-tag based reclassify already resolved with confidence is left
alone, since Google's own type taxonomy isn't inherently more authoritative
than a correctly-matched OSM tag, only better than no real tag at all.
"Transport" is included as generic (not just the placeholder two) because,
unlike a direct amenity/shop tag match, geocoding.py's OSM rules derive it
largely from *infrastructure* tags (parking, fuel, charging_station,
bus_stop) that describe something adjacent to a place rather than the
place's own identity - Nominatim reverse-geocoding a business's coordinates
frequently snaps to a nearby parking-lot tag instead of the business itself
(confirmed live: "One Stop Bedford Avon", an actual convenience store,
carried "Transport" purely from a nearby amenity=parking tag). Real
stations/airports aren't at risk from this widening: Google's own taxonomy
maps train_station/bus_station/subway_station back to "Transport" too, so a
genuine station is a same-value no-op here, not a wrong overwrite.

A handful of placeIds resolve to a broad locality/transit-hub entry rather
than the actual specific spot - confirmed live ("Ashburnham Road" would
have been overwritten with plain "Bedford", the city it's already filed
under). A name that's just the place's own already-known city is skipped
outright rather than trusted.

Requires GOOGLE_PLACES_API_KEY in the environment (a key restricted to
"Places API (New)", from a Google Cloud project with billing enabled).

Usage:
    GOOGLE_PLACES_API_KEY=... python scripts/backfill_google_places.py [--apply]

Without --apply, prints the plan only (dry run) against the first 10 places.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.google_places import GOOGLE_TYPE_TO_CATEGORY, api_key, place_details  # noqa: E402
from app.models import Place  # noqa: E402

GENERIC_CATEGORIES = {"Other places", "Streets and roads", "Transport"}


def run(apply: bool) -> None:
    if not api_key():
        print("GOOGLE_PLACES_API_KEY not set in environment.")
        sys.exit(1)

    init_db()
    with SessionLocal() as session:
        places = (
            session.query(Place)
            .filter(Place.google_place_id.isnot(None), Place.manually_corrected.is_(False))
            .all()
        )
        total = len(places)
        print(f"{total} places to check{'' if apply else ' (dry run - first 10 only)'}")

        checked = updated = 0
        for place in places if apply else places[:10]:
            data = place_details(place.google_place_id)
            checked += 1
            if apply:
                time.sleep(0.1)
            if data is None:
                continue

            new_name = (data.get("displayName") or {}).get("text")
            primary_type = data.get("primaryType")
            mapped_category = GOOGLE_TYPE_TO_CATEGORY.get(primary_type)

            if new_name and place.city and new_name.strip().lower() == place.city.strip().lower():
                continue

            changed = False
            if new_name and new_name != place.name:
                print(f"place {place.id}: name {place.name!r} -> {new_name!r}")
                if apply:
                    place.name = new_name
                changed = True
            if mapped_category and mapped_category != place.category and place.category in GENERIC_CATEGORIES:
                print(f"place {place.id}: category {place.category!r} -> {mapped_category!r}")
                if apply:
                    place.category = mapped_category
                changed = True
            if changed:
                updated += 1
                if apply:
                    place.manually_corrected = True

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
