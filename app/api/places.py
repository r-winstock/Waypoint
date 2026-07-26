from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.geocoding import find_nearby_places
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


@router.get("/api/places/detail/{place_id}/nearby")
def get_nearby_alternatives(place_id: int, session: Session = Depends(db_dependency)):
    place = session.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    return {"alternatives": find_nearby_places(place.lat_round, place.lon_round)}


class PlaceCorrection(BaseModel):
    name: str
    category: str
    city: str | None = None
    country: str | None = None
    country_code: str | None = None


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
    session.commit()
    return {
        "id": place.id,
        "name": place.name,
        "category": place.category,
        "city": place.city,
        "country": place.country,
        "country_code": place.country_code,
        "manually_corrected": place.manually_corrected,
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
