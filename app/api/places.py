from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import db_dependency
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


@router.get("/api/places/{category}")
def get_places_in_category(category: str, session: Session = Depends(db_dependency)):
    rows = (
        session.query(
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
            {"name": name, "city": city, "visit_count": visit_count, "last_visit_ts": last_ts}
            for name, city, visit_count, last_ts in rows
        ],
    }
