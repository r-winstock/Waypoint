from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.models import Place, Visit

router = APIRouter()


@router.get("/api/world")
def get_world(session: Session = Depends(db_dependency)):
    rows = (
        session.query(
            Place.country,
            Place.country_code,
            func.count(func.distinct(Place.city)),
            func.max(Visit.end_ts),
        )
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.country.isnot(None))
        .group_by(Place.country)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    return {
        "countries": [
            {
                "name": country,
                "country_code": country_code,
                "city_count": city_count,
                "last_visit_ts": last_ts,
            }
            for country, country_code, city_count, last_ts in rows
        ]
    }


@router.get("/api/world/{country_code}")
def get_country_detail(country_code: str, session: Session = Depends(db_dependency)):
    country_code = country_code.upper()
    name_row = (
        session.query(Place.country).filter(Place.country_code == country_code, Place.country.isnot(None)).first()
    )
    if name_row is None:
        raise HTTPException(status_code=404, detail="No visited places found for this country")

    city_rows = (
        session.query(Place.city, func.count(func.distinct(Place.id)), func.max(Visit.end_ts))
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.country_code == country_code, Place.city.isnot(None))
        .group_by(Place.city)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    places = session.query(Place).filter(Place.country_code == country_code).all()

    return {
        "country_code": country_code,
        "country_name": name_row[0],
        "cities": [
            {"name": city, "place_count": place_count, "last_visit_ts": last_ts}
            for city, place_count, last_ts in city_rows
        ],
        "pins": [
            {"lat": p.lat_round, "lon": p.lon_round, "name": p.name, "category": p.category, "city": p.city}
            for p in places
        ],
    }
