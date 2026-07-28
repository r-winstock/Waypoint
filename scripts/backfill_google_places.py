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

Only requests Place Details Essentials-tier fields (id, displayName,
primaryType) via the FieldMask header - this SKU has a 10,000/month free
cap, comfortably covering a personal timeline's place count. Deliberately
never requests rating/reviews/photos/openingHours fields, which are billed
at the far more expensive Pro/Enterprise tiers - Google bills a request at
its single most expensive requested field, so the field mask is the whole
cost story here.

Only touches Place rows with a google_place_id and manually_corrected=False
- never overwrites a place a user has already corrected by hand, and treats
its own updates the same way afterwards (sets manually_corrected=True on any
row it changes) so a later OSM-based reclassify pass doesn't revert a
Google-sourced category back to a weaker OSM-tag guess.

Category is only ever replaced when the place is still sitting in a generic
bucket ("Other places" or "Streets and roads") - a category the OSM-tag
based reclassify already resolved with confidence is left alone, since
Google's own type taxonomy isn't inherently more authoritative than a
correctly-matched OSM tag, only better than no real tag at all.

Requires GOOGLE_PLACES_API_KEY in the environment (a key restricted to
"Places API (New)", from a Google Cloud project with billing enabled - see
this script's companion setup instructions).

Usage:
    GOOGLE_PLACES_API_KEY=... python scripts/backfill_google_places.py [--apply]

Without --apply, prints the plan only (dry run) against the first 10 places.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Place  # noqa: E402

PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
# Essentials-tier fields only (id/displayName/primaryType) - see this
# script's own docstring for why straying from this list matters.
FIELD_MASK = "id,displayName,primaryType"

# Google's Places API (New) type taxonomy -> Waypoint's own category names.
# Not exhaustive - covers the common real-world types this personal dataset
# actually turned up; anything unmapped is left as whatever the OSM-based
# pipeline already resolved (see the "Other places"/"Streets and roads"
# gate below), the same "no confident category beats a wrong one" principle
# geocoding.py's own CATEGORY_RULES already follows.
GOOGLE_TYPE_TO_CATEGORY = {
    "restaurant": "Food and drink", "cafe": "Food and drink", "bar": "Food and drink",
    "bakery": "Food and drink", "meal_takeaway": "Food and drink", "meal_delivery": "Food and drink",
    "fast_food_restaurant": "Food and drink", "coffee_shop": "Food and drink", "pub": "Food and drink",
    "supermarket": "Shopping", "grocery_store": "Shopping", "shopping_mall": "Shopping",
    "clothing_store": "Shopping", "department_store": "Shopping", "convenience_store": "Shopping",
    "store": "Shopping", "electronics_store": "Shopping", "book_store": "Shopping",
    "hardware_store": "Shopping", "furniture_store": "Shopping", "shoe_store": "Shopping",
    "lodging": "Hotels", "hotel": "Hotels", "motel": "Hotels", "hostel": "Hotels",
    "bed_and_breakfast": "Hotels", "resort_hotel": "Hotels",
    "museum": "Culture", "art_gallery": "Culture", "tourist_attraction": "Culture",
    "church": "Culture", "hindu_temple": "Culture", "mosque": "Culture", "synagogue": "Culture",
    "place_of_worship": "Culture", "historical_landmark": "Culture", "monument": "Culture",
    "gym": "Sports", "fitness_center": "Sports", "stadium": "Sports", "sports_complex": "Sports",
    "golf_course": "Sports", "swimming_pool": "Sports", "sports_club": "Sports",
    "airport": "Airports", "international_airport": "Airports",
    "bank": "Banking and services", "atm": "Banking and services", "post_office": "Banking and services",
    "pharmacy": "Banking and services", "drugstore": "Banking and services",
    "school": "Education", "university": "Education", "primary_school": "Education",
    "secondary_school": "Education", "library": "Education", "preschool": "Education",
    "bus_station": "Transport", "train_station": "Transport", "subway_station": "Transport",
    "transit_station": "Transport", "light_rail_station": "Transport", "parking": "Transport",
    "gas_station": "Transport", "car_rental": "Transport", "taxi_stand": "Transport",
    "ferry_terminal": "Transport", "car_repair": "Transport", "car_wash": "Transport",
    "night_club": "Nightlife", "casino": "Nightlife",
    "hospital": "Healthcare", "doctor": "Healthcare", "dentist": "Healthcare",
    "veterinary_care": "Healthcare", "physiotherapist": "Healthcare", "medical_lab": "Healthcare",
    "movie_theater": "Entertainment", "amusement_park": "Entertainment", "zoo": "Entertainment",
    "aquarium": "Entertainment", "bowling_alley": "Entertainment", "amusement_center": "Entertainment",
    "park": "Parks and nature", "national_park": "Parks and nature", "campground": "Parks and nature",
    "state_park": "Parks and nature", "wildlife_park": "Parks and nature", "botanical_garden": "Parks and nature",
    "local_government_office": "Offices and services", "lawyer": "Offices and services",
    "real_estate_agency": "Offices and services", "insurance_agency": "Offices and services",
    "accounting": "Offices and services", "corporate_office": "Offices and services",
    "courthouse": "Offices and services", "embassy": "Offices and services",
    "route": "Streets and roads", "street_address": "Streets and roads", "premise": "Streets and roads",
}

GENERIC_CATEGORIES = {"Other places", "Streets and roads"}


def fetch_place_details(api_key: str, place_id: str) -> dict | None:
    try:
        resp = httpx.get(
            PLACE_DETAILS_URL.format(place_id=place_id),
            headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


def run(apply: bool) -> None:
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
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
            data = fetch_place_details(api_key, place.google_place_id)
            checked += 1
            if apply:
                time.sleep(0.1)
            if data is None:
                continue

            new_name = (data.get("displayName") or {}).get("text")
            primary_type = data.get("primaryType")
            mapped_category = GOOGLE_TYPE_TO_CATEGORY.get(primary_type)

            changed = False
            if new_name and new_name != place.name:
                print(f"place {place.id}: name {place.name!r} -> {new_name!r}")
                if apply:
                    place.name = new_name
                changed = True
            if mapped_category and place.category in GENERIC_CATEGORIES:
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
