from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.geocoding import create_or_reuse_place, resolve_place
from app.models import TripSegment, Visit
from app.processing import _rebuild_trip_segments, _rebuild_trips

router = APIRouter()

# Mirrors TripSegment.mode's docstring in models.py - kept as an explicit
# allowlist rather than trusting arbitrary client input straight into the
# column, since this is the only write path for mode that isn't derived by
# the processing/import pipelines themselves.
VALID_MODES = {
    "walking", "cycling", "driving", "taxi", "bus", "train", "subway", "tram", "ferry", "boating", "flying",
}

# Mirrors TripSegment.render_mode's docstring - "auto" is today's automatic
# mode-based snapping, the other three are a per-segment manual override for
# when that guessed wrong (snapped to the wrong network, or shouldn't have
# snapped at all).
VALID_RENDER_MODES = {"auto", "raw", "snap_road", "snap_rail"}


class UpdateSegmentMode(BaseModel):
    mode: str | None = None
    render_mode: str | None = None


@router.patch("/api/events/segments/{segment_id}")
def update_segment_mode(segment_id: int, body: UpdateSegmentMode, session: Session = Depends(db_dependency)):
    """Lets a segment be reclassified (e.g. speed-classified "driving" was
    actually a taxi) - GPS speed alone can't tell these apart, so this is
    the only way to correct it once the visit either side is confirmed.
    Also doubles as the write path for render_mode (the Day map's per-
    segment snap-to-road/rail/raw override) - both are single-field
    corrections to the same row, no reason for two endpoints."""
    if body.mode is None and body.render_mode is None:
        raise HTTPException(status_code=400, detail="mode or render_mode required")
    if body.mode is not None and body.mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(VALID_MODES)}")
    if body.render_mode is not None and body.render_mode not in VALID_RENDER_MODES:
        raise HTTPException(status_code=400, detail=f"render_mode must be one of {sorted(VALID_RENDER_MODES)}")
    segment = session.get(TripSegment, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    if body.mode is not None:
        segment.mode = body.mode
    if body.render_mode is not None:
        segment.render_mode = body.render_mode
    session.commit()
    return {"id": segment.id, "mode": segment.mode, "render_mode": segment.render_mode}


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


class CreateVisit(BaseModel):
    lat: float
    lon: float
    start_ts: int
    end_ts: int
    name: str | None = None
    category: str | None = None
    city: str | None = None
    country: str | None = None
    country_code: str | None = None


@router.post("/api/events/visits")
def create_visit(body: CreateVisit, session: Session = Depends(db_dependency)):
    """Manually inserts a visit for a stay tracking missed entirely (phone
    left at home, dead battery, a genuine background-kill dropout) - a real
    gap in the record, not a mis-detected one, so there's nothing to
    correct/convert/merge; a fresh row is the only way to fill it.

    source="manual" so it's never touched by _rebuild_visits' full
    OwnTracks regeneration (that would just delete it back out again on the
    next scheduler tick), but IS picked up as a boundary by
    _rebuild_trip_segments, which re-derives the raw-GPS legs either side of
    it from scratch - exactly the "GPS plots flow around the new visit"
    behaviour, reusing the same pairwise leg-building already used for
    every other visit rather than needing any bespoke splitting logic here.
    """
    if body.end_ts <= body.start_ts:
        raise HTTPException(status_code=400, detail="end_ts must be after start_ts")

    overlapping = (
        session.query(Visit)
        .filter(Visit.start_ts < body.end_ts, Visit.end_ts > body.start_ts)
        .all()
    )
    # OwnTracks/manual visits in the way are safe to clear - both are fully
    # regenerable (owntracks by the next full reprocess, manual by this same
    # endpoint). Imported history (google_import/kml_import/photo_import)
    # is not - reject rather than silently delete real recorded data; the
    # user needs to remove/adjust that entry first if it's genuinely wrong.
    if any(v.source not in ("owntracks", "manual") for v in overlapping):
        raise HTTPException(
            status_code=409,
            detail="This time range overlaps imported history - delete or correct that entry first.",
        )

    if body.name:
        place = create_or_reuse_place(
            session, body.lat, body.lon, body.name, body.category or "Other places",
            body.city, body.country, body.country_code,
        )
    else:
        place = resolve_place(session, body.lat, body.lon)

    for v in overlapping:
        session.delete(v)
    session.execute(
        delete(TripSegment).where(
            TripSegment.source == "owntracks",
            TripSegment.start_ts < body.end_ts,
            TripSegment.end_ts > body.start_ts,
        )
    )

    visit = Visit(
        start_ts=body.start_ts,
        end_ts=body.end_ts,
        lat=body.lat,
        lon=body.lon,
        point_count=0,
        source="manual",
        place_id=place.id if place is not None else None,
    )
    session.add(visit)
    session.flush()

    _rebuild_trip_segments(session)
    _rebuild_trips(session)

    session.commit()
    session.refresh(visit)
    return {"visit_id": visit.id, "place_id": place.id if place is not None else None}
