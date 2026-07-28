from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.country_names import country_name_en
from app.db import db_dependency
from app.models import Place, Visit

router = APIRouter()


@router.get("/api/world")
def get_world(session: Session = Depends(db_dependency)):
    # Grouped by country_code (falling back to the raw country text only
    # when no code is set), not by Place.country directly - two Places can
    # share the same real country but carry slightly different raw country
    # text (different geocoding passes, a manual correction, an import
    # source) and previously formed two separate groups here that both
    # happened to resolve to the same English display name via
    # country_name_en (confirmed live: two "Ireland" rows) - identical
    # .name values on the frontend's own x-for :key, which broke Alpine's
    # rendering for the entire World list, not just the duplicate entry.
    group_key = func.coalesce(Place.country_code, Place.country)
    rows = (
        session.query(
            func.max(Place.country),
            func.max(Place.country_code),
            func.count(func.distinct(Place.city)),
            func.max(Visit.end_ts),
        )
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.country.isnot(None))
        .group_by(group_key)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    return {
        "countries": [
            {
                "name": country_name_en(country_code, country),
                "local_name": country if country_name_en(country_code, country) != country else None,
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
    display_name = country_name_en(country_code, name_row[0])
    local_name = name_row[0] if display_name != name_row[0] else None

    city_rows = (
        session.query(
            Place.city, func.count(func.distinct(Place.id)), func.max(Visit.end_ts), func.max(Place.city_local)
        )
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.country_code == country_code, Place.city.isnot(None))
        .group_by(Place.city)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    places = session.query(Place).filter(Place.country_code == country_code).all()

    return {
        "country_code": country_code,
        "country_name": display_name,
        "country_local_name": local_name,
        "cities": [
            {"name": city, "place_count": place_count, "last_visit_ts": last_ts, "local_name": local_name}
            for city, place_count, last_ts, local_name in city_rows
        ],
        "pins": [
            {"lat": p.lat_round, "lon": p.lon_round, "name": p.name, "category": p.category, "city": p.city}
            for p in places
        ],
    }
