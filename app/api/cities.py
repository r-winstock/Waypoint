from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.country_names import country_name_en
from app.db import db_dependency
from app.models import Place, Visit

router = APIRouter()


@router.get("/api/cities")
def get_cities(page: int = 1, page_size: int = 24, session: Session = Depends(db_dependency)):
    """Paginated the same way /api/trips is - a full list of every visited
    city (hundreds, for a long-running history) rendering all its photo
    cards at once was slow to load, the same problem already solved there."""
    rows = (
        session.query(
            Place.city,
            func.count(func.distinct(Place.id)),
            func.max(Visit.end_ts),
            # Any one real country for this city is enough as a photo-search
            # disambiguator (see images.py's hint param) - doesn't need to be
            # the strict majority, just a real value to distinguish e.g.
            # "Windsor, United Kingdom" from Windsor, Ontario. Run through
            # country_name_en below - Wikipedia's own descriptions are
            # always in English ("Second-largest city in Italy"), so a
            # hint in the local language ("Italia") never matches anything,
            # confirmed live: this silently broke Milan's photo even after
            # the city name itself was corrected to English.
            func.max(Place.country),
            func.max(Place.country_code),
            # Same reasoning - any one place's city_local is representative
            # of this city (they'd all resolve to the same local name).
            func.max(Place.city_local),
        )
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.city.isnot(None))
        .group_by(Place.city)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    total_cities = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    return {
        "cities": [
            {
                "name": city,
                "place_count": place_count,
                "last_visit_ts": last_ts,
                "country": country_name_en(country_code, country),
                "local_name": local_name,
            }
            for city, place_count, last_ts, country, country_code, local_name in page_rows
        ],
        "page": page,
        "page_size": page_size,
        "total_cities": total_cities,
        "total_pages": max(1, (total_cities + page_size - 1) // page_size),
    }


@router.get("/api/cities/{city_name}")
def get_city_detail(city_name: str, session: Session = Depends(db_dependency)):
    rows = (
        session.query(
            Place.id,
            Place.name,
            Place.name_local,
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

    country_row = (
        session.query(Place.country, Place.country_code)
        .filter(Place.city == city_name, Place.country.isnot(None))
        .limit(1)
        .first()
    )
    country = country_name_en(country_row[1], country_row[0]) if country_row else None
    city_local = (
        session.query(Place.city_local)
        .filter(Place.city == city_name, Place.city_local.isnot(None))
        .limit(1)
        .scalar()
    )

    return {
        "city_name": city_name,
        "city_local": city_local,
        "country": country,
        "places": [
            {
                "id": place_id,
                "name": name,
                "name_local": name_local,
                "category": category,
                "lat": lat,
                "lon": lon,
                "visit_count": visit_count,
                "last_visit_ts": last_ts,
            }
            for place_id, name, name_local, category, lat, lon, visit_count, last_ts in rows
        ],
    }
