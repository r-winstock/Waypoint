"""Google Places API (New) client - shared by the live geocoding pipeline
(resolve_place, for brand-new places with no Google Timeline placeId of
their own), the "Fix this place" modal's Google-sourced nearby list, and
scripts/backfill_google_places.py (the one-off historical catch-up).

Every call here is deliberately Essentials-tier or free ("IDs Only")
- see GOOGLE_TYPE_TO_CATEGORY's neighbouring constants for exactly which
fields that means. Google bills a request at its single most expensive
requested field, so straying from this field mask anywhere is a real,
ongoing cost decision, not a free enrichment.

Every function here degrades to None/[] rather than raising when
GOOGLE_PLACES_API_KEY isn't configured, or a request fails - this is
always a best-effort enrichment on top of Nominatim (the live pipeline's
actual baseline), never a hard dependency.
"""
from __future__ import annotations

import math
import os
import time

import httpx

USER_AGENT = "Waypoint/0.1 (self-hosted personal timeline; contact rwinstock@hotmail.com)"
NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Essentials-tier fields only (id/displayName/primaryType/location) - see
# module docstring. Nearby Search itself only ever requests "places.id"
# (the free, unlimited "IDs Only" SKU) - getting a candidate's name/type/
# location is always a separate Place Details Essentials call per id,
# never bundled into the Nearby Search request itself, which would bill at
# a different (non-free) SKU.
DETAILS_FIELD_MASK = "id,displayName,primaryType,location"

# Self-throttled the same way geocoding.py/images.py throttle Nominatim/
# Wikipedia - Google's own rate limits are far more generous, but there's
# no reason to hammer even an authenticated API harder than necessary.
MIN_INTERVAL_S = 0.1
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def api_key() -> str | None:
    return os.environ.get("GOOGLE_PLACES_API_KEY")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Duplicated from app.processing (not imported) to avoid a circular
    # import, the same reasoning geocoding.py's own copy already documents.
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Google's Places API (New) type taxonomy -> Waypoint's own category names.
# Not exhaustive - covers the common real-world types this personal
# dataset actually turned up (see scripts/backfill_google_places.py, where
# this table was first built and validated against ~1,300 real places);
# anything unmapped is left as whatever the OSM-based pipeline already
# resolved, the same "no confident category beats a wrong one" principle
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


def nearby_place_ids(lat: float, lon: float, radius_m: float = 50.0, limit: int = 1) -> list[str]:
    key = api_key()
    if not key:
        return []
    _throttle()
    try:
        resp = httpx.post(
            NEARBY_SEARCH_URL,
            json={
                "maxResultCount": limit,
                "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": radius_m}},
            },
            headers={
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": "places.id",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        return [p["id"] for p in resp.json().get("places", []) if "id" in p]
    except httpx.HTTPError:
        return []


def place_details(place_id: str) -> dict | None:
    key = api_key()
    if not key:
        return None
    _throttle()
    try:
        resp = httpx.get(
            PLACE_DETAILS_URL.format(place_id=place_id),
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": DETAILS_FIELD_MASK, "User-Agent": USER_AGENT},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


def resolve_new_place(lat: float, lon: float) -> dict | None:
    """Best-effort name/category for a brand-new coordinate with no
    existing google_place_id (a live OwnTracks ping - only the historical
    Google Timeline import provides its own placeId directly). Returns
    None if no key is configured, nothing is found nearby, or the request
    fails - resolve_place's own Nominatim result is always the baseline
    this only ever enriches, never replaces outright."""
    ids = nearby_place_ids(lat, lon, radius_m=50.0, limit=1)
    if not ids:
        return None
    data = place_details(ids[0])
    if data is None:
        return None
    return {
        "google_place_id": data.get("id"),
        "name": (data.get("displayName") or {}).get("text"),
        "category": GOOGLE_TYPE_TO_CATEGORY.get(data.get("primaryType")),
    }


def find_nearby_google_places(lat: float, lon: float, radius_m: float = 100.0, limit: int = 8) -> list[dict]:
    """Candidates for the "Fix this place" modal's Google-sourced list -
    complements find_nearby_places' Overpass/OSM results with Google's own
    database, which often has real business names/categories Overpass's
    community-tagged data simply doesn't carry for that spot."""
    ids = nearby_place_ids(lat, lon, radius_m=radius_m, limit=limit)
    results = []
    for place_id in ids:
        data = place_details(place_id)
        if data is None:
            continue
        loc = data.get("location") or {}
        p_lat, p_lon = loc.get("latitude"), loc.get("longitude")
        results.append(
            {
                "google_place_id": data.get("id"),
                "name": (data.get("displayName") or {}).get("text"),
                "category": GOOGLE_TYPE_TO_CATEGORY.get(data.get("primaryType"), "Other places"),
                "distance_m": round(_haversine_m(lat, lon, p_lat, p_lon)) if p_lat is not None else None,
            }
        )
    results.sort(key=lambda r: r["distance_m"] if r["distance_m"] is not None else float("inf"))
    return results
