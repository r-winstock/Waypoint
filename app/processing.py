from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import get_setting
from app.geocoding import resolve_place
from app.models import LocationPoint, Trip, TripSegment, Visit

STAY_RADIUS_M = 150.0
STAY_MIN_SECONDS = 8 * 60

# Mode thresholds, average km/h over a trip segment.
WALK_MAX_KMH = 7.0
FLY_MIN_KMH = 140.0

# Segments shorter than this are noise (GPS drift between two visits at
# effectively the same spot) and are dropped rather than recorded.
MIN_SEGMENT_DISTANCE_M = 20.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class RawPoint:
    lat: float
    lon: float
    tst: int


def process_all(session: Session) -> None:
    """Full recompute of visits / trip segments / trips from raw location
    points. Cheap at personal-device scale (thousands of points), and far
    simpler than incremental streaming updates - safe to re-run on every
    scheduler tick."""

    points = [
        RawPoint(p.lat, p.lon, p.tst)
        for p in session.query(LocationPoint).order_by(LocationPoint.tst).all()
    ]
    if not points:
        return

    _rebuild_visits(session, points)
    session.flush()
    _rebuild_trip_segments(session, points)
    _geocode_visits(session)
    _rebuild_trips(session)
    session.commit()


def _rebuild_visits(session: Session, points: list[RawPoint]) -> None:
    session.execute(delete(Visit))

    n = len(points)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and haversine_m(points[i].lat, points[i].lon, points[j + 1].lat, points[j + 1].lon) <= STAY_RADIUS_M:
            j += 1

        duration = points[j].tst - points[i].tst
        if duration >= STAY_MIN_SECONDS:
            cluster = points[i : j + 1]
            centroid_lat = sum(p.lat for p in cluster) / len(cluster)
            centroid_lon = sum(p.lon for p in cluster) / len(cluster)
            session.add(
                Visit(
                    start_ts=points[i].tst,
                    end_ts=points[j].tst,
                    lat=centroid_lat,
                    lon=centroid_lon,
                    point_count=len(cluster),
                )
            )
            i = j + 1
        else:
            i += 1


def _rebuild_trip_segments(session: Session, points: list[RawPoint]) -> None:
    session.execute(delete(TripSegment))

    visits = session.query(Visit).order_by(Visit.start_ts).all()
    if len(visits) < 2:
        return

    # Index raw points by timestamp so we can pull the sub-sequence travelled
    # between the end of one visit and the start of the next.
    by_tst = {p.tst: p for p in points}
    sorted_tst = [p.tst for p in points]

    for prev, nxt in zip(visits, visits[1:]):
        start_idx = _bisect_left(sorted_tst, prev.end_ts)
        end_idx = _bisect_right(sorted_tst, nxt.start_ts)
        leg = [by_tst[t] for t in sorted_tst[start_idx:end_idx]]
        if len(leg) < 2:
            continue

        distance_m = sum(
            haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(leg, leg[1:])
        )
        duration_s = leg[-1].tst - leg[0].tst
        if distance_m < MIN_SEGMENT_DISTANCE_M or duration_s <= 0:
            continue

        avg_kmh = (distance_m / 1000.0) / (duration_s / 3600.0)
        if avg_kmh > FLY_MIN_KMH:
            mode = "flying"
        elif avg_kmh > WALK_MAX_KMH:
            mode = "driving"
        else:
            mode = "walking"

        session.add(
            TripSegment(
                start_ts=leg[0].tst,
                end_ts=leg[-1].tst,
                mode=mode,
                distance_m=distance_m,
                duration_s=duration_s,
                start_visit_id=prev.id,
                end_visit_id=nxt.id,
            )
        )


def _bisect_left(a: list[int], x: int) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(a: list[int], x: int) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _geocode_visits(session: Session) -> None:
    for visit in session.query(Visit).filter(Visit.place_id.is_(None)).all():
        place = resolve_place(session, visit.lat, visit.lon)
        if place is not None:
            visit.place_id = place.id


def _rebuild_trips(session: Session) -> None:
    session.execute(delete(Trip))

    home_lat = get_setting(session, "home_lat", "")
    home_lon = get_setting(session, "home_lon", "")
    if not home_lat or not home_lon:
        return  # home location not configured yet - no trips without it

    home_lat_f, home_lon_f = float(home_lat), float(home_lon)
    home_radius_m = float(get_setting(session, "home_radius_m", "500"))

    visits = session.query(Visit).order_by(Visit.start_ts).all()

    run: list[Visit] = []
    for visit in visits:
        is_away = haversine_m(visit.lat, visit.lon, home_lat_f, home_lon_f) > home_radius_m
        if is_away:
            run.append(visit)
        else:
            _flush_trip_run(session, run)
            run = []
    _flush_trip_run(session, run)


def _flush_trip_run(session: Session, run: list[Visit]) -> None:
    if not run:
        return

    trip = Trip(start_ts=run[0].start_ts, end_ts=run[-1].end_ts)

    city_duration: Counter[str] = Counter()
    country: str | None = None
    country_code: str | None = None
    for visit in run:
        duration = max(visit.end_ts - visit.start_ts, 60)
        if visit.place and visit.place.city:
            city_duration[visit.place.city] += duration
        if visit.place and visit.place.country:
            country = visit.place.country
            country_code = visit.place.country_code

    if city_duration:
        trip.primary_city = city_duration.most_common(1)[0][0]
    trip.primary_country = country
    trip.primary_country_code = country_code

    session.add(trip)
    session.flush()
    for visit in run:
        visit.trip_id = trip.id
