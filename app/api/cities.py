from __future__ import annotations

from fastapi import APIRouter, Depends
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
        )
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.city.isnot(None))
        .group_by(Place.city)
        .order_by(func.max(Visit.end_ts).desc())
        .all()
    )
    return {
        "cities": [
            {"name": city, "place_count": place_count, "last_visit_ts": last_ts}
            for city, place_count, last_ts in rows
        ]
    }
