from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.models import LocationPoint, TripSegment, Visit

router = APIRouter()


def _day_bounds(day_str: str) -> tuple[int, int]:
    try:
        d = date.fromisoformat(day_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


@router.get("/api/day/{day_str}")
def get_day(day_str: str, session: Session = Depends(db_dependency)):
    start_ts, end_ts = _day_bounds(day_str)

    points = (
        session.query(LocationPoint)
        .filter(LocationPoint.tst >= start_ts, LocationPoint.tst < end_ts)
        .order_by(LocationPoint.tst)
        .all()
    )

    visits = (
        session.query(Visit)
        .filter(Visit.start_ts < end_ts, Visit.end_ts >= start_ts)
        .order_by(Visit.start_ts)
        .all()
    )
    segments = (
        session.query(TripSegment)
        .filter(TripSegment.start_ts < end_ts, TripSegment.end_ts >= start_ts)
        .order_by(TripSegment.start_ts)
        .all()
    )

    timeline = []
    for v in visits:
        timeline.append(
            {
                "type": "visit",
                "start_ts": v.start_ts,
                "end_ts": v.end_ts,
                "lat": v.lat,
                "lon": v.lon,
                "place_name": v.place.name if v.place else None,
                "category": v.place.category if v.place else None,
                "city": v.place.city if v.place else None,
            }
        )
    for s in segments:
        timeline.append(
            {
                "type": "segment",
                "start_ts": s.start_ts,
                "end_ts": s.end_ts,
                "mode": s.mode,
                "distance_m": s.distance_m,
                "duration_s": s.duration_s,
            }
        )
    timeline.sort(key=lambda e: e["start_ts"])

    stats = {"driving_m": 0.0, "driving_s": 0.0, "flying_m": 0.0, "flying_s": 0.0, "walking_m": 0.0, "walking_s": 0.0}
    mode_key = {"driving": "driving", "flying": "flying", "walking": "walking"}
    for s in segments:
        key = mode_key.get(s.mode)
        if key:
            stats[f"{key}_m"] += s.distance_m
            stats[f"{key}_s"] += s.duration_s

    return {
        "date": day_str,
        "stats": {**stats, "visits": len(visits)},
        "points": [{"lat": p.lat, "lon": p.lon, "tst": p.tst} for p in points],
        "timeline": timeline,
    }
