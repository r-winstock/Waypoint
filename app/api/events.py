from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.geocoding import create_or_reuse_place
from app.models import TripSegment, Visit

router = APIRouter()


@router.delete("/api/events/visits/{visit_id}")
def delete_visit(visit_id: int, session: Session = Depends(db_dependency)):
    visit = session.get(Visit, visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="Visit not found")
    session.delete(visit)
    session.commit()
    return {"deleted": True}


@router.delete("/api/events/segments/{segment_id}")
def delete_segment(segment_id: int, session: Session = Depends(db_dependency)):
    segment = session.get(TripSegment, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    session.delete(segment)
    session.commit()
    return {"deleted": True}


class ConvertToVisit(BaseModel):
    lat: float
    lon: float
    name: str
    category: str
    city: str | None = None
    country: str | None = None
    country_code: str | None = None


@router.post("/api/events/segments/{segment_id}/convert-to-visit")
def convert_segment_to_visit(segment_id: int, body: ConvertToVisit, session: Session = Depends(db_dependency)):
    """A travel segment that was actually a stop (e.g. speed-classified as
    "walking" but really a supermarket visit) becomes a Visit at the given
    place, and the segment it replaces is removed. Coordinates are supplied
    by the caller (e.g. the search result the user picked) rather than
    derived from the segment - TripSegment doesn't carry its own lat/lon,
    only distance/duration, and re-deriving a point from the flanking visits
    would be an approximation when the real place is already known."""
    segment = session.get(TripSegment, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment not found")

    place = create_or_reuse_place(
        session, body.lat, body.lon, body.name, body.category, body.city, body.country, body.country_code
    )
    visit = Visit(
        start_ts=segment.start_ts,
        end_ts=segment.end_ts,
        lat=body.lat,
        lon=body.lon,
        point_count=0,
        source=segment.source,
        place_id=place.id,
    )
    session.add(visit)
    session.delete(segment)
    session.commit()
    session.refresh(visit)
    return {"visit_id": visit.id, "place_id": place.id}


@router.post("/api/events/visits/{visit_id}/merge-with-previous")
def merge_visit_with_previous(visit_id: int, session: Session = Depends(db_dependency)):
    """Merges this visit into whichever visit immediately precedes it (by
    start_ts, regardless of source) - the general case being two adjacent
    cards for what was really one continuous stay, e.g. split by a spurious
    segment, or by the seam between imported history and live tracking."""
    visit = session.get(Visit, visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="Visit not found")

    previous = (
        session.query(Visit)
        .filter(Visit.start_ts < visit.start_ts)
        .order_by(Visit.start_ts.desc())
        .first()
    )
    if previous is None:
        raise HTTPException(status_code=400, detail="No preceding visit to merge with")

    previous.end_ts = max(previous.end_ts, visit.end_ts)
    session.delete(visit)
    # Any segment now fully inside the merged visit's span was the gap being
    # merged away (a short drift/misclassification between the same place),
    # not a real journey - drop it along with the visit it used to separate.
    session.execute(
        delete(TripSegment).where(TripSegment.start_ts >= previous.start_ts, TripSegment.end_ts <= previous.end_ts)
    )
    session.commit()
    session.refresh(previous)
    return {"merged_into_visit_id": previous.id}
