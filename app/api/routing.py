from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.models import TripSegment
from app.routing import get_or_fetch_rail_route

router = APIRouter()

RAIL_MODES = {"train", "subway", "tram"}


@router.get("/api/routing/segment/{segment_id}")
def get_segment_route(
    segment_id: int, from_lat: float, from_lon: float, to_lat: float, to_lon: float,
    session: Session = Depends(db_dependency),
):
    """Snapped rail path for one train/subway/tram TripSegment - see
    app/routing.py. Only meaningful for rail modes; driving/walking/cycling
    routing happens client-side straight against the public OSRM demo
    server (map.js's wpFetchRoute), which needs no caching of its own.

    Endpoints are passed in by the caller rather than looked up here via
    TripSegment.start_visit_id/end_visit_id - those FKs are frequently null
    on imported history (confirmed live: Google Timeline's timelinePath-
    derived segments never set them), whereas the frontend already knows
    both endpoints positionally (the nearest visit either side of this
    segment in the day's timeline), the exact same way it already does for
    the OSRM driving/walking/cycling call this mirrors."""
    segment = session.get(TripSegment, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    if segment.mode not in RAIL_MODES:
        raise HTTPException(status_code=400, detail="Only train/subway/tram segments have a rail route")

    cached = get_or_fetch_rail_route(session, segment_id, segment.mode, from_lat, from_lon, to_lat, to_lon)
    session.commit()
    if not cached.found or not cached.points_json:
        raise HTTPException(status_code=404, detail="No rail route found nearby")
    return {"points": json.loads(cached.points_json)}
