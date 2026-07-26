from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.models import Trip

router = APIRouter()


@router.get("/api/trips")
def get_trips(session: Session = Depends(db_dependency)):
    trips = session.query(Trip).order_by(Trip.start_ts.desc()).all()

    total_days = 0
    out = []
    for t in trips:
        days = max(1, (t.end_ts - t.start_ts) // 86400 + 1)
        total_days += days
        out.append(
            {
                "id": t.id,
                "start_ts": t.start_ts,
                "end_ts": t.end_ts,
                "days": days,
                "primary_city": t.primary_city,
                "primary_country": t.primary_country,
                "primary_country_code": t.primary_country_code,
                "visits": [
                    {
                        "lat": v.lat,
                        "lon": v.lon,
                        "place_name": v.place.name if v.place else None,
                        "city": v.place.city if v.place else None,
                    }
                    for v in t.visits
                ],
            }
        )

    return {"trips": out, "totals": {"trip_count": len(out), "day_count": total_days}}
