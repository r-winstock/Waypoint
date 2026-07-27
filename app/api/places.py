from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.geocoding import categorise_from_raw_json, find_nearby_places, find_similar_places, merge_places_into, search_places
from app.models import Place, Visit

router = APIRouter()


@router.get("/api/places")
def get_place_categories(session: Session = Depends(db_dependency)):
    rows = (
        session.query(Place.category, func.count(Place.id))
        .group_by(Place.category)
        .order_by(func.count(Place.id).desc())
        .all()
    )
    return {"categories": [{"name": category, "count": count} for category, count in rows]}


@router.post("/api/places/reclassify")
def reclassify_places(session: Session = Depends(db_dependency)):
    """Re-derives category for every "Other places" row against the current
    CATEGORY_RULES, using each place's own already-stored raw_json rather
    than re-querying Nominatim - a one-off catch-up whenever CATEGORY_RULES
    gains new buckets, run manually rather than on a schedule since it only
    ever needs to do anything the moment rules actually change. Never
    touches a place the user has manually corrected (manually_corrected),
    and can't do anything for a place with no raw_json at all (most of the
    Google Timeline import - it never had OSM tags to categorise from in
    the first place, so there's nothing here to re-derive)."""
    candidates = (
        session.query(Place)
        .filter(Place.category == "Other places", Place.raw_json.isnot(None), Place.manually_corrected.is_(False))
        .all()
    )
    reclassified = 0
    for place in candidates:
        try:
            data = json.loads(place.raw_json)
        except ValueError:
            continue
        new_category = categorise_from_raw_json(data)
        if new_category != "Other places":
            place.category = new_category
            reclassified += 1
    session.commit()
    return {"checked": len(candidates), "reclassified": reclassified}


@router.get("/api/places/detail/{place_id}/nearby")
def get_nearby_alternatives(place_id: int, session: Session = Depends(db_dependency)):
    place = session.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    return {"alternatives": find_nearby_places(place.lat_round, place.lon_round)}


@router.get("/api/places/detail/{place_id}/visits")
def get_place_visits(place_id: int, session: Session = Depends(db_dependency)):
    place = session.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    visits = session.query(Visit).filter(Visit.place_id == place_id).order_by(Visit.start_ts.desc()).all()
    return {
        "place_id": place_id,
        "name": place.name,
        "city": place.city,
        "category": place.category,
        "visits": [{"id": v.id, "start_ts": v.start_ts, "end_ts": v.end_ts} for v in visits],
    }


@router.get("/api/places/detail/{place_id}/similar")
def get_similar_places(place_id: int, session: Session = Depends(db_dependency)):
    """Other Place rows that look like the same real place as this one
    (same current name+city, nearby) - candidates to fold into this one
    when correcting it, e.g. two visits to the same office that ended up
    with separate Place rows."""
    place = session.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    return {"similar": find_similar_places(session, place)}


@router.get("/api/places/search")
def search_place_by_name(
    q: str,
    place_id: int | None = None,
    lat: float | None = None,
    lon: float | None = None,
    session: Session = Depends(db_dependency),
):
    """Free-text search for when the right place isn't among the nearby
    OSM-tagged alternatives at all (e.g. Overpass's radius/tagging missed
    it). Biased toward place_id's coordinates when given, else lat/lon
    directly (e.g. converting a travel segment into a visit, where there's
    no existing Place yet to bias from)."""
    near_lat, near_lon = lat, lon
    if place_id is not None:
        place = session.get(Place, place_id)
        if place is not None:
            near_lat, near_lon = place.lat_round, place.lon_round
    return {"results": search_places(q, near_lat, near_lon)}


class PlaceCorrection(BaseModel):
    name: str
    category: str
    city: str | None = None
    country: str | None = None
    country_code: str | None = None
    merge_place_ids: list[int] = []


@router.put("/api/places/detail/{place_id}")
def correct_place(place_id: int, correction: PlaceCorrection, session: Session = Depends(db_dependency)):
    place = session.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    place.name = correction.name
    place.category = correction.category
    place.city = correction.city
    place.country = correction.country
    place.country_code = correction.country_code.upper() if correction.country_code else None
    place.manually_corrected = True
    merged_visits = merge_places_into(session, place_id, correction.merge_place_ids)
    session.commit()
    return {
        "id": place.id,
        "name": place.name,
        "category": place.category,
        "city": place.city,
        "country": place.country,
        "country_code": place.country_code,
        "manually_corrected": place.manually_corrected,
        "merged_visits": merged_visits,
    }


@router.get("/api/places/{category}")
def get_places_in_category(category: str, session: Session = Depends(db_dependency)):
    rows = (
        session.query(
            Place.id,
            Place.name,
            Place.city,
            func.count(Visit.id),
            func.max(Visit.end_ts),
        )
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.category == category)
        .group_by(Place.id)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    return {
        "category": category,
        "places": [
            {"id": place_id, "name": name, "city": city, "visit_count": visit_count, "last_visit_ts": last_ts}
            for place_id, name, city, visit_count, last_ts in rows
        ],
    }
