from __future__ import annotations

import json
import math
import time

import httpx
from sqlalchemy.orm import Session

from app.db import get_setting
from app.models import Place, Visit

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "Waypoint/0.1 (self-hosted personal timeline; contact rwinstock@hotmail.com)"

# Nominatim's usage policy caps anonymous use at 1 request/second.
MIN_INTERVAL_S = 1.05
_last_call = 0.0

# Rounding to 4dp is ~11m at the equator - fine granularity for "same place"
# without hammering the cache with near-duplicate coordinates.
ROUND_DP = 4

# GPS drift across repeat visits to the same real place (especially indoors,
# e.g. home) often rounds to a different ROUND_DP cell each time, missing the
# exact-match cache above and creating a fresh Place row per visit - ten
# separate "Ashmead Road, Bedford" rows for one house before this existed.
# Reused only when the freshly-resolved name+city also matches (see
# _find_duplicate_place), so two genuinely distinct same-named places within
# a city (e.g. two different Aldi branches) aren't wrongly merged.
PLACE_DEDUP_RADIUS_M = 300.0

# Expanded from the original 9 categories after finding live that "Other
# places" held the large majority of resolved places (980 of ~1400) -
# inspecting what was actually landing there (raw_json's category/type
# pairs, cross-checked directly against the real data) turned up several
# real, common OSM tag groups that had no bucket of their own: transit
# infrastructure, banking/pharmacy, schools, nightlife, healthcare, cinemas/
# theatres, and parks, plus a dedicated "Streets and roads" bucket for bare
# roads (highway=residential etc) and generic house/apartment buildings,
# which turned out to be most of what was left in "Other" once the above
# were split out. What's still genuinely left in "Other" after that is
# street furniture (a bench, a postbox, a defibrillator) - not a "place" in
# any meaningful sense, just what a GPS point nearest-matched to when
# nothing else was there, deliberately left uncategorised rather than
# inventing a bucket for it.
CATEGORY_RULES: dict[str, dict[str, str] | str] = {
    "amenity": {
        "restaurant": "Food and drink",
        "cafe": "Food and drink",
        "bar": "Food and drink",
        "pub": "Food and drink",
        "fast_food": "Food and drink",
        "food_court": "Food and drink",
        "ice_cream": "Food and drink",
        "bank": "Banking and services",
        "atm": "Banking and services",
        "bureau_de_change": "Banking and services",
        "post_office": "Banking and services",
        "pharmacy": "Banking and services",
        "school": "Education",
        "college": "Education",
        "university": "Education",
        "kindergarten": "Education",
        "library": "Education",
        "bus_station": "Transport",
        "bus_stop": "Transport",
        "ferry_terminal": "Transport",
        "fuel": "Transport",
        "charging_station": "Transport",
        "parking": "Transport",
        "bicycle_parking": "Transport",
        "car_rental": "Transport",
        "taxi": "Transport",
        "nightclub": "Nightlife",
        "casino": "Nightlife",
        "hospital": "Healthcare",
        "clinic": "Healthcare",
        "doctors": "Healthcare",
        "dentist": "Healthcare",
        "veterinary": "Healthcare",
        "place_of_worship": "Culture",
        "cinema": "Entertainment",
        "theatre": "Entertainment",
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
        "viewpoint": "Culture",
        "theme_park": "Entertainment",
        "zoo": "Entertainment",
    },
    "historic": "Culture",
    "leisure": {
        "sports_centre": "Sports",
        "stadium": "Sports",
        "fitness_centre": "Sports",
        "pitch": "Sports",
        "golf_course": "Sports",
        "park": "Parks and nature",
        "garden": "Parks and nature",
        "nature_reserve": "Parks and nature",
        "playground": "Parks and nature",
    },
    "sport": "Sports",
    "aeroway": {"aerodrome": "Airports"},
    "railway": {"station": "Transport", "halt": "Transport", "tram_stop": "Transport", "platform": "Transport", "stop": "Transport"},
    "office": {
        "company": "Offices and services",
        "estate_agent": "Offices and services",
        "government": "Offices and services",
        "lawyer": "Offices and services",
        "insurance": "Offices and services",
    },
    "natural": {"beach": "Parks and nature", "wood": "Parks and nature", "water": "Parks and nature"},
    # Bare roads/generic addresses - not a "place" in any meaningful sense,
    # but common enough (the large majority of what was landing in "Other
    # places") that a dedicated bucket is more honest than lumping a street
    # in with genuinely uncategorisable places like street furniture.
    "highway": {
        "bus_stop": "Transport",
        "residential": "Streets and roads",
        "unclassified": "Streets and roads",
        "pedestrian": "Streets and roads",
        "tertiary": "Streets and roads",
        "primary": "Streets and roads",
        "secondary": "Streets and roads",
    },
    "place": {"house": "Streets and roads"},
    "building": {"yes": "Streets and roads", "house": "Streets and roads", "apartments": "Streets and roads"},
}


def _categorise(osm_category: str | None, osm_type: str | None) -> str:
    rule = CATEGORY_RULES.get(osm_category or "")
    if isinstance(rule, str):
        return rule
    if isinstance(rule, dict):
        return rule.get(osm_type or "", "Other places")
    return "Other places"


def categorise_from_raw_json(raw_json_data: dict) -> str:
    """Public entry point re-deriving a category from a Place's own stored
    raw_json (Nominatim's reverse-geocode response, keyed the same way as
    resolve_place's own live category/type read) - used to reclassify
    existing places against an updated CATEGORY_RULES without re-querying
    Nominatim at all, since the raw response was already saved."""
    return _categorise(raw_json_data.get("category"), raw_json_data.get("type"))


def _categorise_tags(tags: dict) -> str:
    """Same category rules as _categorise, but against a raw OSM tags dict
    (what Overpass returns) rather than Nominatim's single category/type
    pair - used by find_nearby_places."""
    for key in ("amenity", "shop", "tourism", "historic", "leisure", "sport", "aeroway", "railway", "office", "natural", "highway", "place", "building"):
        if key in tags:
            cat = _categorise(key, tags[key])
            if cat != "Other places":
                return cat
    return "Other places"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Duplicated from app.processing (not imported) to avoid a circular
    # import - processing.py already imports resolve_place from this module.
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def find_nearby_places(lat: float, lon: float, radius_m: float = 100.0, limit: int = 15) -> list[dict]:
    """Named OSM features near a coordinate, for the "this place is wrong,
    pick the right one" correction UI - Nominatim's reverse endpoint only
    ever returns its single best guess, so this uses Overpass (the standard
    tool for "what's nearby", also free/no-API-key like Nominatim)."""
    query = f"""
    [out:json][timeout:10];
    (
      node(around:{radius_m},{lat},{lon})[name];
      way(around:{radius_m},{lat},{lon})[name];
    );
    out center {limit};
    """
    try:
        resp = httpx.post(
            OVERPASS_URL, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=15.0
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        el_lat = el.get("lat") or el.get("center", {}).get("lat")
        el_lon = el.get("lon") or el.get("center", {}).get("lon")
        if not name or el_lat is None or el_lon is None:
            continue
        results.append(
            {
                "osm_type": el.get("type"),
                "osm_id": el.get("id"),
                "name": name,
                "category": _categorise_tags(tags),
                "lat": el_lat,
                "lon": el_lon,
                "distance_m": round(_haversine_m(lat, lon, el_lat, el_lon)),
            }
        )
    results.sort(key=lambda r: r["distance_m"])

    # OSM roads are frequently split into several way segments sharing the
    # same name (one per block) - Overpass returns each as a separate
    # element, which showed up as "Gladstone Street" appearing 2-3 times in
    # the alternatives list. Keep only the closest occurrence of each name.
    deduped: list[dict] = []
    seen_names: set[str] = set()
    for r in results:
        if r["name"] in seen_names:
            continue
        seen_names.add(r["name"])
        deduped.append(r)
    return deduped


def _find_duplicate_place(session: Session, lat: float, lon: float, name: str | None, city: str | None) -> Place | None:
    """An already-resolved place with the same name+city within
    PLACE_DEDUP_RADIUS_M, if any - see that constant for why this exists."""
    if not name:
        return None

    box_deg = (PLACE_DEDUP_RADIUS_M / 111_320) * 1.5  # generous prefilter box, exact haversine below
    lat_r, lon_r = round(lat, ROUND_DP), round(lon, ROUND_DP)
    query = session.query(Place).filter(
        Place.name == name,
        Place.lat_round.between(lat_r - box_deg, lat_r + box_deg),
        Place.lon_round.between(lon_r - box_deg, lon_r + box_deg),
    )
    query = query.filter(Place.city == city) if city is not None else query.filter(Place.city.is_(None))

    best, best_distance = None, PLACE_DEDUP_RADIUS_M
    for candidate in query.all():
        distance = _haversine_m(lat, lon, candidate.lat_round, candidate.lon_round)
        if distance <= best_distance:
            best, best_distance = candidate, distance
    return best


# Wider than PLACE_DEDUP_RADIUS_M (which the automatic background dedup uses
# conservatively) - this only ever surfaces candidates for a human to review
# and explicitly opt into merging via the "Fix this place" modal's "also
# apply to..." list, so a looser radius trading a few more false positives
# for fewer missed real duplicates is the right way round for a user-driven
# action.
SIMILAR_PLACE_RADIUS_M = 2000.0


def find_similar_places(session: Session, place: Place) -> list[dict]:
    """Other Place rows sharing this place's current name+city within
    SIMILAR_PLACE_RADIUS_M - candidates the user can choose to merge into
    this one when correcting it (the same real place resolved to more than
    one Place row, e.g. because they were more than PLACE_DEDUP_RADIUS_M
    apart, or existed before that automatic dedup did)."""
    if not place.name:
        return []
    box_deg = (SIMILAR_PLACE_RADIUS_M / 111_320) * 1.5
    query = session.query(Place).filter(
        Place.id != place.id,
        Place.name == place.name,
        Place.lat_round.between(place.lat_round - box_deg, place.lat_round + box_deg),
        Place.lon_round.between(place.lon_round - box_deg, place.lon_round + box_deg),
    )
    query = query.filter(Place.city == place.city) if place.city is not None else query.filter(Place.city.is_(None))

    out = []
    for candidate in query.all():
        distance = _haversine_m(place.lat_round, place.lon_round, candidate.lat_round, candidate.lon_round)
        if distance > SIMILAR_PLACE_RADIUS_M:
            continue
        visit_count = session.query(Visit).filter(Visit.place_id == candidate.id).count()
        out.append({"id": candidate.id, "name": candidate.name, "city": candidate.city, "distance_m": round(distance), "visit_count": visit_count})
    out.sort(key=lambda r: r["distance_m"])
    return out


def merge_places_into(session: Session, canonical_id: int, other_place_ids: list[int]) -> int:
    """Repoints every Visit from other_place_ids onto canonical_id and
    deletes those now-orphaned Place rows. Shared by scripts/dedupe_places.py
    (automatic, proximity-clustered) and the manual "also apply to..."
    option on place correction (user-selected, explicit) - same operation,
    different callers decide which place_ids to pass in."""
    repointed = 0
    for other_id in other_place_ids:
        if other_id == canonical_id:
            continue
        repointed += (
            session.query(Visit).filter(Visit.place_id == other_id).update({Visit.place_id: canonical_id})
        )
        other = session.get(Place, other_id)
        if other is not None:
            session.delete(other)
    return repointed


def create_or_reuse_place(
    session: Session, lat: float, lon: float, name: str, category: str,
    city: str | None, country: str | None, country_code: str | None,
) -> Place:
    """Get-or-create a Place from already-known fields (a search result the
    user picked, or details they typed by hand) rather than reverse-geocoding
    - used when converting a mis-classified travel segment into a visit
    (see app/api/events.py), where the real place is already known and a
    fresh Nominatim call would just be redundant. Still checks
    _find_duplicate_place first so this doesn't create yet another
    near-duplicate row for a place already resolved nearby."""
    duplicate = _find_duplicate_place(session, lat, lon, name, city)
    if duplicate is not None:
        return duplicate

    lat_r, lon_r = round(lat, ROUND_DP), round(lon, ROUND_DP)
    place = Place(
        lat_round=lat_r, lon_round=lon_r, name=name, category=category,
        city=city, country=country, country_code=country_code,
    )
    session.add(place)
    session.flush()
    return place


def _tag_home(session: Session, place: Place, lat: float, lon: float) -> None:
    """Auto-labels a place as "Home" when it falls within the configured
    home radius - the same setting _rebuild_trips already uses to decide
    what counts as "away", so the app already knows where home is. Only
    overrides the generic "Other places" fallback, and never a place the
    user has corrected themselves or that OSM tags already gave a more
    specific category."""
    if place.manually_corrected or place.category != "Other places":
        return
    home_lat = get_setting(session, "home_lat", "")
    home_lon = get_setting(session, "home_lon", "")
    if not home_lat or not home_lon:
        return
    radius_m = float(get_setting(session, "home_radius_m", "500"))
    if _haversine_m(lat, lon, float(home_lat), float(home_lon)) <= radius_m:
        place.category = "Home"


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
            _tag_home(session, cached_by_id, lat, lon)
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
        _tag_home(session, cached, lat, lon)
        return cached

    data = _reverse_geocode(lat, lon)
    name = category = city = country = country_code = raw_json = None
    if data is not None:
        address = data.get("address", {})
        name = data.get("name") or address.get("road") or data.get("display_name")
        category = _categorise(data.get("category"), data.get("type"))
        city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
        country = address.get("country")
        code = address.get("country_code")
        country_code = code.upper() if code else None
        raw_json = json.dumps(data)
    else:
        category = "Other places"

    duplicate = _find_duplicate_place(session, lat, lon, name, city)
    if duplicate is not None:
        if google_place_id is not None and duplicate.google_place_id is None:
            duplicate.google_place_id = google_place_id
        _tag_home(session, duplicate, lat, lon)
        return duplicate

    place = Place(
        lat_round=lat_r,
        lon_round=lon_r,
        google_place_id=google_place_id,
        name=name,
        category=category,
        city=city,
        country=country,
        country_code=country_code,
        raw_json=raw_json,
    )
    _tag_home(session, place, lat, lon)
    session.add(place)
    session.flush()
    return place


def _reverse_geocode(lat: float, lon: float, language: str | None = "en") -> dict | None:
    """language="en" (the default for every live resolve) asks Nominatim to
    translate administrative names (city/country) into English where it has
    a translation - without it, Nominatim returns whatever language the
    underlying OSM data itself was tagged in, which for many cities is the
    local name (confirmed live: "Milano" instead of "Milan", "Éire /
    Ireland" instead of "Ireland"). language=None is used by the one-off
    backfill script re-resolving already-cached places, which passes the
    English request explicitly and diffs the result against what's already
    stored (the local form) rather than needing a second call here."""
    _throttle()
    headers = {"User-Agent": USER_AGENT}
    if language:
        headers["Accept-Language"] = language
    try:
        resp = httpx.get(
            NOMINATIM_URL,
            params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1},
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


def search_places(query: str, near_lat: float | None = None, near_lon: float | None = None, limit: int = 8) -> list[dict]:
    """Free-text place search, for when the correct place isn't one of
    find_nearby_places' OSM-tagged neighbours (e.g. it's real but Overpass's
    radius/tagging missed it entirely) - the "Fix this place" modal's search
    box. Biased toward near_lat/near_lon with a soft viewbox (bounded=0, so a
    genuine match well outside it still comes back) since a corrected place
    is overwhelmingly likely to be near where the visit actually happened;
    only the label was wrong, not the location."""
    if not query.strip():
        return []

    params = {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": limit}
    if near_lat is not None and near_lon is not None:
        box_deg = 0.5  # ~55km soft bias box
        params["viewbox"] = f"{near_lon - box_deg},{near_lat + box_deg},{near_lon + box_deg},{near_lat - box_deg}"
        params["bounded"] = 0

    _throttle()
    try:
        resp = httpx.get(
            NOMINATIM_SEARCH_URL, params=params, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}, timeout=10.0
        )
        resp.raise_for_status()
        results = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    out = []
    for r in results:
        address = r.get("address", {})
        lat, lon = float(r["lat"]), float(r["lon"])
        code = address.get("country_code")
        out.append(
            {
                "name": r.get("name") or address.get("road") or r.get("display_name"),
                "display_name": r.get("display_name"),
                "category": _categorise(r.get("category"), r.get("type")),
                "lat": lat,
                "lon": lon,
                "city": address.get("city") or address.get("town") or address.get("village") or address.get("municipality"),
                "country": address.get("country"),
                "country_code": code.upper() if code else None,
                "distance_m": round(_haversine_m(near_lat, near_lon, lat, lon)) if near_lat is not None else None,
            }
        )
    return out
