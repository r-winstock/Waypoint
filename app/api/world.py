from __future__ import annotations

from fastapi import APIRouter, Depends
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
