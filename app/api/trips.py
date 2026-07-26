from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.models import Trip, TripSegment

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
                        "category": v.place.category if v.place else None,
                        "city": v.place.city if v.place else None,
                    }
                    for v in t.visits
                ],
            }
        )

    return {"trips": out, "totals": {"trip_count": len(out), "day_count": total_days}}


@router.get("/api/trips/{trip_id}")
def get_trip_detail(trip_id: int, session: Session = Depends(db_dependency)):
    trip = session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    visits = sorted(trip.visits, key=lambda v: v.start_ts)
    # Segments aren't linked to a trip directly (only visits are, via
    # Trip.visits) - a segment within the trip's own date range is one of
    # its travel legs, same convention the Day view already uses.
    segments = (
        session.query(TripSegment)
        .filter(TripSegment.start_ts >= trip.start_ts, TripSegment.end_ts <= trip.end_ts)
        .order_by(TripSegment.start_ts)
        .all()
    )

    timeline = [
        {
            "type": "visit",
            "id": v.id,
            "start_ts": v.start_ts,
            "end_ts": v.end_ts,
            "lat": v.lat,
            "lon": v.lon,
            "place_id": v.place_id,
            "place_name": v.place.name if v.place else None,
            "category": v.place.category if v.place else None,
            "city": v.place.city if v.place else None,
        }
        for v in visits
    ] + [
        {
            "type": "segment",
            "id": s.id,
            "start_ts": s.start_ts,
            "end_ts": s.end_ts,
            "mode": s.mode,
            "distance_m": s.distance_m,
            "duration_s": s.duration_s,
        }
        for s in segments
    ]
    timeline.sort(key=lambda e: e["start_ts"])

    return {
        "id": trip.id,
        "start_ts": trip.start_ts,
        "end_ts": trip.end_ts,
        "days": max(1, (trip.end_ts - trip.start_ts) // 86400 + 1),
        "primary_city": trip.primary_city,
        "primary_country": trip.primary_country,
        "primary_country_code": trip.primary_country_code,
        "timeline": timeline,
    }
