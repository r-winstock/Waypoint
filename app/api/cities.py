from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.models import Place, Visit

router = APIRouter()


@router.get("/api/cities")
def get_cities(session: Session = Depends(db_dependency)):
    rows = (
        session.query(
            Place.city,
            func.count(func.distinct(Place.id)),
            func.max(Visit.end_ts),
            # Any one real country for this city is enough as a photo-search
            # disambiguator (see images.py's hint param) - doesn't need to be
            # the strict majority, just a real value to distinguish e.g.
            # "Windsor, United Kingdom" from Windsor, Ontario.
            func.max(Place.country),
        )
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.city.isnot(None))
        .group_by(Place.city)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    return {
        "cities": [
            {"name": city, "place_count": place_count, "last_visit_ts": last_ts, "country": country}
            for city, place_count, last_ts, country in rows
        ]
    }


@router.get("/api/cities/{city_name}")
def get_city_detail(city_name: str, session: Session = Depends(db_dependency)):
    rows = (
        session.query(
            Place.id,
            Place.name,
            Place.category,
            Place.lat_round,
            Place.lon_round,
            func.count(Visit.id),
            func.max(Visit.end_ts),
        )
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.city == city_name)
        .group_by(Place.id)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No places found for this city")

    country = (
        session.query(Place.country)
        .filter(Place.city == city_name, Place.country.isnot(None))
        .limit(1)
        .scalar()
    )

    return {
        "city_name": city_name,
        "country": country,
        "places": [
            {
                "id": place_id,
                "name": name,
                "category": category,
                "lat": lat,
                "lon": lon,
                "visit_count": visit_count,
                "last_visit_ts": last_ts,
            }
            for place_id, name, category, lat, lon, visit_count, last_ts in rows
        ],
    }
