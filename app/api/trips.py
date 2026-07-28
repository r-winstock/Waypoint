from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.country_names import country_name_en
from app.db import db_dependency
from app.models import Trip, TripSegment
from app.photoprism import attach_photos_to_visits, nearby_photos

router = APIRouter()


@router.get("/api/trips")
def get_trips(page: int = 1, page_size: int = 24, session: Session = Depends(db_dependency)):
    """Grouped by destination (primary_city + primary_country), not one card
    per Trip row - 800 individual trips on one page was unusable, and a
    handful of destinations (e.g. Reading, visited on three separate
    unrelated occasions) were showing as several near-identical cards next
    to each other. Each group carries its own individual trips (still
    openable via GET /api/trips/{id} same as before) for the frontend to
    expand inline rather than needing a second endpoint.

    Grouping first, then paginating the resulting destination list (not the
    raw trip list) - a single destination's trips can span multiple years,
    so paginating years directly would sometimes split one destination's
    history across pages."""
    trips = session.query(Trip).order_by(Trip.start_ts.desc()).all()

    total_days = 0
    groups: dict[tuple[str | None, str | None], dict] = {}
    order: list[tuple[str | None, str | None]] = []
    for t in trips:
        days = max(1, (t.end_ts - t.start_ts) // 86400 + 1)
        total_days += days
        key = (t.primary_city, t.primary_country)
        if key not in groups:
            groups[key] = {
                "primary_city": t.primary_city,
                # English, not the raw stored (sometimes local-language)
                # value - used as the photo-search hint on Trip cards, and
                # Wikipedia's own descriptions are always in English
                # ("Second-largest city in Italy") - a hint of "Italia"
                # never matches anything, confirmed live this silently
                # broke Milan's photo even after the city name itself was
                # corrected to English.
                "primary_country": country_name_en(t.primary_country_code, t.primary_country),
                "primary_country_code": t.primary_country_code,
                "trip_count": 0,
                "total_days": 0,
                "last_visit_ts": t.end_ts,
                "trips": [],
            }
            order.append(key)  # trips are pre-sorted desc, so first-seen = most recent for this destination
        group = groups[key]
        group["trip_count"] += 1
        group["total_days"] += days
        group["last_visit_ts"] = max(group["last_visit_ts"], t.end_ts)
        group["trips"].append(
            {
                "id": t.id,
                "start_ts": t.start_ts,
                "end_ts": t.end_ts,
                "days": days,
                "visits": [
                    {
                        "lat": v.lat,
                        "lon": v.lon,
                        "place_name": v.place.name if v.place else None,
                        "place_name_local": v.place.name_local if v.place else None,
                        "category": v.place.category if v.place else None,
                        "city": v.place.city if v.place else None,
                    }
                    for v in t.visits
                ],
            }
        )

    destinations = [groups[k] for k in order]
    total_destinations = len(destinations)
    start = (page - 1) * page_size
    page_items = destinations[start : start + page_size]

    return {
        "destinations": page_items,
        "page": page,
        "page_size": page_size,
        "total_destinations": total_destinations,
        "total_pages": max(1, (total_destinations + page_size - 1) // page_size),
        "totals": {"trip_count": len(trips), "day_count": total_days},
    }


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
            "place_name_local": v.place.name_local if v.place else None,
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

    # limit=100, not nearby_photos' own smaller default - a multi-day trip
    # can have far more distinct stops than a single Day view, each wanting
    # its own handful of photos once attach_photos_to_visits splits them up
    # below, so the same cap that's fine for one day is too tight here.
    trip_photos = nearby_photos(start_ts=trip.start_ts, end_ts=trip.end_ts, limit=100)
    unassigned_photos = attach_photos_to_visits(timeline, trip_photos)

    return {
        "id": trip.id,
        "start_ts": trip.start_ts,
        "end_ts": trip.end_ts,
        "days": max(1, (trip.end_ts - trip.start_ts) // 86400 + 1),
        "primary_city": trip.primary_city,
        "primary_country": country_name_en(trip.primary_country_code, trip.primary_country),
        "primary_country_code": trip.primary_country_code,
        "timeline": timeline,
        "photos": unassigned_photos,
    }
