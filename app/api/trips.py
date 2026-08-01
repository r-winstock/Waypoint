from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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
    groups: dict[tuple[str | None, str | None, str | None], dict] = {}
    order: list[tuple[str | None, str | None, str | None]] = []
    for t in trips:
        days = max(1, (t.end_ts - t.start_ts) // 86400 + 1)
        total_days += days
        # Named (kml_import) trips group by their own name first - without
        # this, two distinct named trips that happen to share a
        # primary_city/primary_country (a geometric best-guess, not always
        # the same place two different trips actually centred on) would
        # wrongly merge into one destination card. Unnamed (computed) trips
        # are unaffected - name is always None for them, so the key reduces
        # to exactly the grouping already in place before Trip.name existed.
        #
        # Grouped on primary_country_code, not the raw primary_country
        # string - confirmed live that the same country can be geocoded as
        # a bilingual/localised string at one point ("Éire / Ireland",
        # "España") and a plain English one at another ("Ireland", "Spain"),
        # splitting the same real destination into two backend groups that
        # both then render as the identical English name via
        # country_name_en() below - a genuine duplicate card, and a
        # duplicate Alpine :key crash since the frontend key is built from
        # that same (identical, post-conversion) displayed value. The code
        # is stable regardless of which raw string a given trip happened to
        # be geocoded with.
        key = (t.name, t.primary_city, t.primary_country_code)
        # A trip that resolved no name/city/country at all is unknown, not
        # "the same unknown place" as some other unrelated trip that also
        # failed to resolve - confirmed live, three genuinely separate trips
        # (Feb 2019 near Rugby, Aug 2019 near Oxford, Aug 2021 near
        # Northampton) silently merged into one incoherent "Trip" card
        # because they shared the same (None, None, None) key. Falling back
        # to the trip's own id keeps every such trip in its own card instead.
        if key == (None, None, None):
            key = ("_unresolved", t.id, None)
        if key not in groups:
            groups[key] = {
                "name": t.name,
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
                "name": t.name,
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
            "render_mode": s.render_mode,
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
        "name": trip.name,
        "start_ts": trip.start_ts,
        "end_ts": trip.end_ts,
        "days": max(1, (trip.end_ts - trip.start_ts) // 86400 + 1),
        "primary_city": trip.primary_city,
        "primary_country": country_name_en(trip.primary_country_code, trip.primary_country),
        "primary_country_code": trip.primary_country_code,
        "timeline": timeline,
        "photos": unassigned_photos,
    }


class TripCorrection(BaseModel):
    name: str | None = None
    primary_city: str | None = None
    primary_country: str | None = None
    primary_country_code: str | None = None


@router.put("/api/trips/{trip_id}")
def correct_trip(trip_id: int, correction: TripCorrection, session: Session = Depends(db_dependency)):
    """Manually override a trip's own destination, same "Fix this place"
    pattern as PUT /api/places/detail/{place_id} - primary_city/
    primary_country/primary_country_code are a geometric best-guess (see
    _farthest_city_country/_primary_city_country) that's sometimes wrong
    about what someone actually considers the "real" destination of a trip
    (a connecting airport outweighing the city itself), and name has no
    heuristic at all for "computed" trips (only ever set from a KML
    folder's own name on import - see scripts/import_travellerspoint_kml.py).
    Sets all three together (rather than name alone) since primary_city/
    primary_country double as the photo-search query/hint on trip cards -
    leaving them pointed at the old, wrong place after a correction would
    keep showing the wrong photo under the new name."""
    trip = session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip.name = correction.name
    trip.primary_city = correction.primary_city
    trip.primary_country = correction.primary_country
    trip.primary_country_code = correction.primary_country_code.upper() if correction.primary_country_code else None
    # A source="computed" trip is entirely deleted and recreated by every
    # _rebuild_trips() pass - this flag is how that rebuild knows to carry
    # this correction over onto the freshly-created replacement row (see
    # its own comment) instead of silently discarding it. No-op for
    # source="kml_import" trips, which _rebuild_trips never touches at all.
    trip.manually_corrected = True
    session.commit()
    return {
        "id": trip.id,
        "name": trip.name,
        "primary_city": trip.primary_city,
        "primary_country": country_name_en(trip.primary_country_code, trip.primary_country),
        "primary_country_code": trip.primary_country_code,
    }
