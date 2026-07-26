from __future__ import annotations

import json
import time

import httpx
from sqlalchemy.orm import Session

from app.models import Place

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "Waypoint/0.1 (self-hosted personal timeline; contact rwinstock@hotmail.com)"

# Nominatim's usage policy caps anonymous use at 1 request/second.
MIN_INTERVAL_S = 1.05
_last_call = 0.0

# Rounding to 4dp is ~11m at the equator - fine granularity for "same place"
# without hammering the cache with near-duplicate coordinates.
ROUND_DP = 4

CATEGORY_RULES: dict[str, dict[str, str] | str] = {
    "amenity": {
        "restaurant": "Food and drink",
        "cafe": "Food and drink",
        "bar": "Food and drink",
        "pub": "Food and drink",
        "fast_food": "Food and drink",
        "food_court": "Food and drink",
        "ice_cream": "Food and drink",
    },
    "shop": "Shopping",
    "tourism": {
        "hotel": "Hotels",
        "guest_house": "Hotels",
        "hostel": "Hotels",
        "motel": "Hotels",
        "apartment": "Hotels",
        "museum": "Culture",
        "gallery": "Culture",
        "artwork": "Culture",
        "attraction": "Culture",
    },
    "historic": "Culture",
    "leisure": {
        "sports_centre": "Sports",
        "stadium": "Sports",
        "fitness_centre": "Sports",
        "pitch": "Sports",
        "golf_course": "Sports",
    },
    "sport": "Sports",
    "aeroway": {"aerodrome": "Airports"},
}


def _categorise(osm_category: str | None, osm_type: str | None) -> str:
    rule = CATEGORY_RULES.get(osm_category or "")
    if isinstance(rule, str):
        return rule
    if isinstance(rule, dict):
        return rule.get(osm_type or "", "Other places")
    return "Other places"


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def resolve_place(
    session: Session, lat: float, lon: float, google_place_id: str | None = None
) -> Place | None:
    """Look up (or reverse-geocode and cache) the place at these coordinates.

    Checks google_place_id first when given (Google's Timeline import): it's
    a precise, stable identifier, so it catches repeat visits to the same
    place even if rounded coordinates drift slightly between visits. Falls
    back to the rounded-coordinate cache either way.
    """

    if google_place_id is not None:
        cached_by_id = (
            session.query(Place).filter(Place.google_place_id == google_place_id).one_or_none()
        )
        if cached_by_id is not None:
            return cached_by_id

    lat_r, lon_r = round(lat, ROUND_DP), round(lon, ROUND_DP)
    cached = (
        session.query(Place)
        .filter(Place.lat_round == lat_r, Place.lon_round == lon_r)
        .one_or_none()
    )
    if cached is not None:
        if google_place_id is not None and cached.google_place_id is None:
            cached.google_place_id = google_place_id
        return cached

    data = _reverse_geocode(lat, lon)
    place = Place(lat_round=lat_r, lon_round=lon_r, google_place_id=google_place_id)
    if data is not None:
        address = data.get("address", {})
        place.name = data.get("name") or address.get("road") or data.get("display_name")
        place.category = _categorise(data.get("category"), data.get("type"))
        place.city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
        place.country = address.get("country")
        code = address.get("country_code")
        place.country_code = code.upper() if code else None
        place.raw_json = json.dumps(data)
    else:
        place.name = None
        place.category = "Other places"

    session.add(place)
    session.flush()
    return place


def _reverse_geocode(lat: float, lon: float) -> dict | None:
    _throttle()
    try:
        resp = httpx.get(
            NOMINATIM_URL,
            params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None
