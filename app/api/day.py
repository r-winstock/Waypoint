from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.models import LocationPoint, TripSegment, Visit
from app.photoprism import attach_photos_to_visits, nearby_photos
from app.processing import MAX_TRUSTED_ACCURACY_M, VISIT_MERGE_MAX_GAP_S

router = APIRouter()


def _day_bounds(day_str: str) -> tuple[int, int]:
    try:
        d = date.fromisoformat(day_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


@router.get("/api/day/overview")
def get_day_overview(session: Session = Depends(db_dependency)):
    """Monthly visit density across all recorded history, for the Day view's
    timeline chart (replaces an earlier, cramped year-strip navigator -
    clicking through one day at a time to reach an old date is painful with
    11+ years of history). min_ts/max_ts let the frontend jump to a real
    date with data in that month rather than always landing on the 1st,
    which may have none.

    Months with zero visits are filled in as explicit zero-count entries
    between the first and last month that has any data, rather than simply
    omitted - a density chart's x-axis needs to be an evenly-spaced
    timeline, and skipping empty months would silently compress a real gap
    (e.g. several quiet years) into looking adjacent to the months either
    side of it."""
    month_expr = func.strftime("%Y-%m", Visit.start_ts, "unixepoch")
    rows = (
        session.query(month_expr.label("month"), func.count(Visit.id), func.min(Visit.start_ts), func.max(Visit.start_ts))
        .group_by("month")
        .order_by("month")
        .all()
    )
    if not rows:
        return {"months": []}

    by_month = {month: (count, min_ts, max_ts) for month, count, min_ts, max_ts in rows}
    first_year, first_month = (int(x) for x in rows[0][0].split("-"))
    last_year, last_month = (int(x) for x in rows[-1][0].split("-"))

    months = []
    y, m = first_year, first_month
    while (y, m) <= (last_year, last_month):
        key = f"{y:04d}-{m:02d}"
        count, min_ts, max_ts = by_month.get(key, (0, None, None))
        months.append({"month": key, "visit_count": count, "min_ts": min_ts, "max_ts": max_ts})
        m += 1
        if m > 12:
            m = 1
            y += 1
    return {"months": months}


@router.get("/api/day/{day_str}")
def get_day(day_str: str, session: Session = Depends(db_dependency)):
    start_ts, end_ts = _day_bounds(day_str)

    points = (
        session.query(LocationPoint)
        .filter(
            LocationPoint.tst >= start_ts,
            LocationPoint.tst < end_ts,
            # Same accuracy floor as app/processing.py's own segment
            # classification (see MAX_TRUSTED_ACCURACY_M there) - a
            # cell/WiFi-triangulated fallback fix (confirmed live: several
            # hundred metres of error, vs a real GPS fix's tens) doesn't
            # just corrupt the mode classifier, it draws as a wild spike
            # off the real route on this exact map.
            (LocationPoint.acc.is_(None)) | (LocationPoint.acc <= MAX_TRUSTED_ACCURACY_M),
        )
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

    # A segment that starts today but ends tomorrow (an overnight flight, a
    # late drive home) has its arrival Visit entirely outside today's own
    # window - confirmed live this meant the map had no coordinate to draw
    # that segment's line to at all (the "nearest visit either side" lookup
    # found nothing after it) even though the segment itself correctly shows
    # up in today's timeline/stats above. The single nearest visit just
    # before start_ts and just after end_ts, wherever they actually fall, is
    # enough for the map to draw right up to (not into) the adjacent day -
    # these are never added to the timeline/stats, only used as map
    # endpoints.
    before_visit = (
        session.query(Visit).filter(Visit.end_ts <= start_ts).order_by(Visit.end_ts.desc()).first()
    )
    after_visit = (
        session.query(Visit).filter(Visit.start_ts >= end_ts).order_by(Visit.start_ts).first()
    )

    # Coalesce consecutive visits to the same resolved place with only a
    # short gap between them into one displayed entry. Each pipeline's own
    # merge logic only ever considers rows of its own source (Visit.source -
    # see processing.py), so a continuous stay that happens to straddle the
    # boundary between imported history and live OwnTracks tracking (or any
    # other same-source micro-gap that survived clustering) still showed up
    # as separate cards even though they resolved to the identical place.
    # Display-only: builds fresh dicts rather than mutating the ORM objects,
    # so nothing here is ever written back to the database.
    timeline = []
    coalesced_visits: list[dict] = []
    for v in visits:
        prev = coalesced_visits[-1] if coalesced_visits else None
        if (
            prev is not None
            and v.place_id is not None
            and v.place_id == prev["place_id"]
            and v.start_ts - prev["end_ts"] <= VISIT_MERGE_MAX_GAP_S
        ):
            prev["end_ts"] = max(prev["end_ts"], v.end_ts)
            prev["visit_ids"].append(v.id)
        else:
            coalesced_visits.append(
                {
                    "id": v.id,
                    "visit_ids": [v.id],
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
            )

    for entry in coalesced_visits:
        timeline.append({"type": "visit", **entry})
    for s in segments:
        timeline.append(
            {
                "type": "segment",
                "id": s.id,
                "start_ts": s.start_ts,
                "end_ts": s.end_ts,
                "mode": s.mode,
                "distance_m": s.distance_m,
                "duration_s": s.duration_s,
                "render_mode": s.render_mode,
            }
        )
    timeline.sort(key=lambda e: e["start_ts"])

    # A visit that started before this day - an ongoing multi-day stay that
    # never technically broke (confirmed live: OwnTracks' own resolution
    # missed a same-day round trip that only Google's richer imported data
    # caught, so the underlying Home visit never registered a real
    # departure) - already appears once above, sorted by its own earlier
    # start_ts. That puts it at the very *top* of today's timeline, not the
    # bottom, even though it's the same stay that covers "arrived back
    # home" after today's last segment too. Every day that visibly "ends
    # with a visit" only does so because that day's own return created a
    # genuinely new Visit row with a start_ts falling inside that specific
    # day; when the stay never broke at all, there's no second row to show
    # at the end. Appends a clipped, second appearance of that same
    # ongoing visit - starting right where the day's last segment ends -
    # whenever today's timeline would otherwise trail off on a segment
    # with nothing after it.
    if timeline and timeline[-1]["type"] == "segment":
        last_end = timeline[-1]["end_ts"]
        closing_visit = next(
            (v for v in visits if v.place_id is not None and v.start_ts <= last_end <= v.end_ts),
            None,
        )
        if closing_visit is not None:
            timeline.append(
                {
                    "type": "visit",
                    # Distinct from the id its own earlier appearance in this
                    # same timeline already used (see the comment above) -
                    # the frontend's :key is "type-id", and two entries with
                    # the same type+id would collide there even though this
                    # one is deliberately a second, clipped appearance of the
                    # same underlying visit, not a duplicate to be merged.
                    "id": f"{closing_visit.id}-return",
                    "visit_ids": [closing_visit.id],
                    "start_ts": last_end,
                    "end_ts": max(closing_visit.end_ts, last_end),
                    "lat": closing_visit.lat,
                    "lon": closing_visit.lon,
                    "place_id": closing_visit.place_id,
                    "place_name": closing_visit.place.name if closing_visit.place else None,
                    "place_name_local": closing_visit.place.name_local if closing_visit.place else None,
                    "category": closing_visit.place.category if closing_visit.place else None,
                    "city": closing_visit.place.city if closing_visit.place else None,
                }
            )

    # Dynamic by whatever modes actually occur today - was hardcoded to just
    # driving/flying/walking, which silently dropped cycling/taxi/bus/train/
    # subway/tram/ferry distance from the stat tiles once those modes existed.
    stats: dict[str, float] = {}
    for s in segments:
        stats[f"{s.mode}_m"] = stats.get(f"{s.mode}_m", 0.0) + s.distance_m
        stats[f"{s.mode}_s"] = stats.get(f"{s.mode}_s", 0.0) + s.duration_s

    # end_ts - 1, not end_ts: see nearby_photos' own docstring for why -
    # _day_bounds returns the exclusive start of the *next* day, already one
    # day ahead of the day actually being viewed, and nearby_photos pads
    # whatever end_ts it's given by another full day itself.
    day_photos = nearby_photos(start_ts=start_ts, end_ts=end_ts - 1, limit=60)
    # Attaches each photo to whichever visit it was taken during, so a
    # photo-heavy day shows "what you saw at each stop" rather than one
    # long horizontally-scrolling strip at the top - the leftover return
    # value (photos matching no visit's own time window) is still shown,
    # just as a separate, clearly-labelled "other photos" fallback.
    unassigned_photos = attach_photos_to_visits(timeline, day_photos)

    return {
        "date": day_str,
        "stats": {**stats, "visits": len(coalesced_visits)},
        "points": [{"lat": p.lat, "lon": p.lon, "tst": p.tst} for p in points],
        "timeline": timeline,
        "context_visits": {
            "before": {"lat": before_visit.lat, "lon": before_visit.lon} if before_visit else None,
            "after": {"lat": after_visit.lat, "lon": after_visit.lon} if after_visit else None,
        },
        "photos": unassigned_photos,
    }
