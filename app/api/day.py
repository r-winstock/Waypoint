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
                "place_id": v.place_id,
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

    # Dynamic by whatever modes actually occur today - was hardcoded to just
    # driving/flying/walking, which silently dropped cycling/taxi/bus/train/
    # subway/tram/ferry distance from the stat tiles once those modes existed.
    stats: dict[str, float] = {}
    for s in segments:
        stats[f"{s.mode}_m"] = stats.get(f"{s.mode}_m", 0.0) + s.distance_m
        stats[f"{s.mode}_s"] = stats.get(f"{s.mode}_s", 0.0) + s.duration_s

    return {
        "date": day_str,
        "stats": {**stats, "visits": len(visits)},
        "points": [{"lat": p.lat, "lon": p.lon, "tst": p.tst} for p in points],
        "timeline": timeline,
    }
