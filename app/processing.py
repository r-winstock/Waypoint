from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.geocoding import resolve_place
from app.models import HomePeriod, LocationPoint, Trip, TripSegment, Visit

STAY_RADIUS_M = 150.0
STAY_MIN_SECONDS = 8 * 60

# Safety-net merge for visits the clustering above still split apart - two
# consecutive visits this close in both space and time get stitched back
# into one. Larger than STAY_RADIUS_M since it's specifically catching drift
# that crossed that boundary.
VISIT_MERGE_RADIUS_M = 250.0
VISIT_MERGE_MAX_GAP_S = 3 * 3600

# Mode thresholds, average km/h over a trip segment. Only distinguishes what
# GPS speed alone can tell apart - a taxi and a car look identical from
# speed, so those finer distinctions only exist where Google's own Timeline
# import already classified them (see scripts/import_google_timeline.py).
WALK_MAX_KMH = 7.0
CYCLE_MAX_KMH = 25.0
FLY_MIN_KMH = 140.0

# Segments shorter than this are noise (GPS drift between two visits at
# effectively the same spot) and are dropped rather than recorded.
MIN_SEGMENT_DISTANCE_M = 20.0

# A stretch away from home shorter than this doesn't count as a "trip" - see
# _flush_trip_run for why (routine errands outside the home radius otherwise
# flood the Trips view).
TRIP_MIN_DURATION_S = 6 * 3600


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def classify_mode_by_speed(avg_kmh: float) -> str:
    """Speed-only mode classification, shared by the OwnTracks pipeline and
    the Google Timeline import's timelinePath segments (movement Google
    itself didn't confidently classify)."""
    if avg_kmh > FLY_MIN_KMH:
        return "flying"
    if avg_kmh > CYCLE_MAX_KMH:
        return "driving"
    if avg_kmh > WALK_MAX_KMH:
        return "cycling"
    return "walking"


def distance_and_duration(points: list[RawPoint]) -> tuple[float, float]:
    """Total haversine distance and elapsed time over an ordered list of
    points - shared by the OwnTracks pipeline's raw-point legs and the
    Google Timeline import's timelinePath segments."""
    distance_m = sum(haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(points, points[1:]))
    duration_s = points[-1].tst - points[0].tst
    return distance_m, duration_s


@dataclass
class RawPoint:
    lat: float
    lon: float
    tst: int


@dataclass
class VisitCandidate:
    start_ts: int
    end_ts: int
    lat: float
    lon: float
    point_count: int


def process_all(session: Session) -> None:
    """Full recompute of visits / trip segments / trips from raw location
    points. Cheap at personal-device scale (thousands of points), and far
    simpler than incremental streaming updates - safe to re-run on every
    scheduler tick."""

    # source="owntracks" only - a backfilled historical point (see
    # LocationPoint's docstring) must never be clustered into a competing
    # Visit/TripSegment alongside the ones Google's own semanticSegments
    # import already produced for that same time range. Those points exist
    # purely for the Day map's raw-GPS-trace rendering, which reads
    # LocationPoint directly (see app/api/day.py) with no source filter -
    # the filter only needs to happen here, at classification time.
    points = [
        RawPoint(p.lat, p.lon, p.tst)
        for p in session.query(LocationPoint)
        .filter(LocationPoint.source == "owntracks")
        .order_by(LocationPoint.tst)
        .all()
    ]
    if not points:
        return

    _rebuild_visits(session, points)
    session.flush()
    _rebuild_trip_segments(session, points)
    _geocode_visits(session)
    _rebuild_trips(session)
    session.commit()


def _cluster_stay_points(points: list[RawPoint]) -> list[VisitCandidate]:
    candidates: list[VisitCandidate] = []
    n = len(points)
    i = 0
    while i < n:
        j = i
        sum_lat, sum_lon, count = points[i].lat, points[i].lon, 1
        while j + 1 < n:
            # Compared against the cluster's running centroid, not a fixed
            # first-point anchor - a fixed anchor falsely "closes" a long
            # stay the moment normal GPS/WiFi positioning drifts past the
            # radius from that one original sample, even though the phone
            # never actually moved (this is exactly what produced phantom
            # multi-hour "walking" segments between visits that were both
            # really just "at home all day").
            centroid_lat, centroid_lon = sum_lat / count, sum_lon / count
            if haversine_m(centroid_lat, centroid_lon, points[j + 1].lat, points[j + 1].lon) > STAY_RADIUS_M:
                break
            j += 1
            sum_lat += points[j].lat
            sum_lon += points[j].lon
            count += 1

        duration = points[j].tst - points[i].tst
        if duration >= STAY_MIN_SECONDS:
            candidates.append(VisitCandidate(points[i].tst, points[j].tst, sum_lat / count, sum_lon / count, count))
            i = j + 1
        else:
            i += 1
    return candidates


def _merge_nearby_visits(candidates: list[VisitCandidate]) -> list[VisitCandidate]:
    """Safety-net pass: stitches adjacent visits back together when close in
    both space and time - catches drift that crossed STAY_RADIUS_M right at
    a cluster boundary and still split what should be one continuous stay."""
    if not candidates:
        return []
    merged = [candidates[0]]
    for cand in candidates[1:]:
        prev = merged[-1]
        gap_s = cand.start_ts - prev.end_ts
        distance_m = haversine_m(prev.lat, prev.lon, cand.lat, cand.lon)
        if gap_s <= VISIT_MERGE_MAX_GAP_S and distance_m <= VISIT_MERGE_RADIUS_M:
            total = prev.point_count + cand.point_count
            merged[-1] = VisitCandidate(
                start_ts=prev.start_ts,
                end_ts=cand.end_ts,
                lat=(prev.lat * prev.point_count + cand.lat * cand.point_count) / total,
                lon=(prev.lon * prev.point_count + cand.lon * cand.point_count) / total,
                point_count=total,
            )
        else:
            merged.append(cand)
    return merged


def _rebuild_visits(session: Session, points: list[RawPoint]) -> None:
    # Only ever touches OwnTracks-derived visits - imported history
    # (source="google_import") is written once and never rebuilt.
    session.execute(delete(Visit).where(Visit.source == "owntracks"))

    candidates = _merge_nearby_visits(_cluster_stay_points(points))
    for v in candidates:
        session.add(Visit(start_ts=v.start_ts, end_ts=v.end_ts, lat=v.lat, lon=v.lon, point_count=v.point_count))


def _rebuild_trip_segments(session: Session, points: list[RawPoint]) -> None:
    # Same source-scoping as _rebuild_visits - never touches imported segments.
    session.execute(delete(TripSegment).where(TripSegment.source == "owntracks"))

    # Only pairs OwnTracks-derived visits - imported visits already have their
    # own Google-classified segments and don't need raw-point legs synthesised
    # between them (there are none: location_points only holds live pings).
    visits = session.query(Visit).filter(Visit.source == "owntracks").order_by(Visit.start_ts).all()
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

        distance_m, duration_s = distance_and_duration(leg)
        if distance_m < MIN_SEGMENT_DISTANCE_M or duration_s <= 0:
            continue

        avg_kmh = (distance_m / 1000.0) / (duration_s / 3600.0)
        mode = classify_mode_by_speed(avg_kmh)

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
    # A manually-corrected trip (see PUT /api/trips/{id}) is about to be
    # deleted along with every other source="computed" row below - snapshot
    # it first, keyed by (start_ts, end_ts), so _flush_trip_run can restore
    # the correction onto whichever freshly-created trip covers the same
    # date range. Confirmed live: a trip renamed via the picker reverted
    # within minutes, because nothing preserved the correction across the
    # very next rebuild (triggered by every incoming batch of OwnTracks
    # points - i.e. constantly, for a live-tracked account). Boundaries are
    # a safe match key here specifically because these are all *historical*
    # trips; live tracking only ever appends new points at "now", it never
    # retroactively rewrites a past trip's own start/end.
    corrections = {
        (t.start_ts, t.end_ts): (t.name, t.primary_city, t.primary_country, t.primary_country_code)
        for t in session.query(Trip).filter(Trip.source == "computed", Trip.manually_corrected.is_(True)).all()
    }

    # Only ever touches source="computed" trips - kml_import trips get their
    # boundaries directly from the source file's own folder structure (see
    # scripts/import_travellerspoint_kml.py) and are never rebuilt here.
    session.execute(delete(Trip).where(Trip.source == "computed"))

    # A visit that doesn't end up part of a new qualifying trip in this pass
    # (now classified as "at home" under updated settings, or its away-run
    # too short to clear TRIP_MIN_DURATION_S - see _flush_trip_run, which
    # only ever sets trip_id on visits it actually assigns to a new Trip)
    # must not keep whatever trip_id it had from a previous rebuild. SQLite
    # reuses a deleted row's rowid for the next inserted row, so a stale FK
    # left behind here can silently alias onto an entirely unrelated
    # freshly-created trip - confirmed live: a real 14-day Manchester trip
    # absorbed several unrelated one-off Bedford errands from three weeks
    # later this way, because their stale trip_id happened to match
    # Manchester's newly (re)assigned id.
    session.execute(update(Visit).where(Visit.source != "kml_import").values(trip_id=None))

    home_periods = session.query(HomePeriod).all()
    if not home_periods:
        return  # no home location(s) configured yet - no trips without one

    # kml_import and photo_import visits are excluded from the gap/radius
    # heuristic below: they already stand alone (or, for kml_import, get
    # their trip boundaries directly from the source file's own folder
    # structure - see scripts/import_travellerspoint_kml.py), and mixing
    # sparse standalone visits into this heuristic is exactly what merged
    # two separate real trips into one nonsensical run before - confirmed
    # live for photo_import specifically: a Longleat visit in 2002 and a
    # Coniston one in 2003 merged into a single fabricated "trip" spanning
    # 2002-2006, because there's no continuous "at home" visit data between
    # them (unlike live tracking) to ever break the run. photo_import visits
    # get their own trip each further down instead, one visit = one trip.
    visits = (
        session.query(Visit)
        .filter(Visit.source.notin_(["kml_import", "photo_import"]))
        .order_by(Visit.start_ts)
        .all()
    )

    def _is_away(visit: Visit) -> bool:
        # Unlike _tag_home (any period, ever), this needs the *one* period
        # that actually covers this visit's own timestamp - the whole point
        # of HomePeriod is that a visit near an old address should count as
        # "at home" only while that was genuinely still home, not forever.
        # A visit with no covering period at all (before the earliest known
        # home, or in a gap between two) is treated as "not away" rather
        # than guessed at - silently fabricating a trip out of a genuinely
        # unknown-era visit is worse than just leaving it ungrouped.
        for period in home_periods:
            if (period.start_ts is None or visit.start_ts >= period.start_ts) and (
                period.end_ts is None or visit.start_ts < period.end_ts
            ):
                return haversine_m(visit.lat, visit.lon, period.lat, period.lon) > period.radius_m
        return False

    run: list[Visit] = []
    for visit in visits:
        if _is_away(visit):
            run.append(visit)
        else:
            _flush_trip_run(session, run, corrections)
            run = []
    _flush_trip_run(session, run, corrections)

    # photo_import visits: one visit = one trip, each flushed on its own
    # rather than gap-grouped with any other visit (see the exclusion
    # comment above) - still goes through the exact same _flush_trip_run
    # (same TRIP_MIN_DURATION_S filter, same city/country/correction
    # logic), just called once per visit instead of once per away-run.
    for visit in session.query(Visit).filter(Visit.source == "photo_import").order_by(Visit.start_ts).all():
        _flush_trip_run(session, [visit], corrections)


def _primary_city_country(visits: list[Visit]) -> tuple[str | None, str | None, str | None]:
    """Time-weighted mode city (and the country tied to that specific city,
    not just whichever visit happens to be last - a trip can include a
    UK-based visit, e.g. the airport, alongside the actual foreign
    destination). Shared by the computed gap/radius grouping and the KML
    importer's direct per-folder trips."""
    city_duration: Counter[str] = Counter()
    city_country: dict[str, tuple[str | None, str | None]] = {}
    country_duration: Counter[str] = Counter()
    country_code_for: dict[str, str | None] = {}
    for visit in visits:
        duration = max(visit.end_ts - visit.start_ts, 60)
        if visit.place and visit.place.city:
            city_duration[visit.place.city] += duration
            city_country[visit.place.city] = (visit.place.country, visit.place.country_code)
        elif visit.place and visit.place.country:
            # A place resolved at country level (e.g. a broad "Canada" or
            # "New Zealand" geocode, rather than a specific city/town) has
            # no city to key on at all - falls back to a time-weighted mode
            # *country* instead of leaving the trip with no name whatsoever.
            country_duration[visit.place.country] += duration
            country_code_for[visit.place.country] = visit.place.country_code

    if city_duration:
        primary_city = city_duration.most_common(1)[0][0]
        country, country_code = city_country[primary_city]
        return primary_city, country, country_code
    if country_duration:
        country = country_duration.most_common(1)[0][0]
        return None, country, country_code_for[country]
    return None, None, None


def _flush_trip_run(
    session: Session, run: list[Visit], corrections: dict[tuple[int, int], tuple] | None = None
) -> None:
    if not run:
        return
    # Below this, it's a routine local errand outside the home radius (a
    # supermarket run a mile out), not a trip - real data testing found the
    # radius check alone let through hundreds of these. Chosen as "away long
    # enough that it's not a same-afternoon errand"; adjust if it over- or
    # under-shoots what actually reads as a trip once you look at it.
    if run[-1].end_ts - run[0].start_ts < TRIP_MIN_DURATION_S:
        return

    start_ts, end_ts = run[0].start_ts, run[-1].end_ts
    trip = Trip(start_ts=start_ts, end_ts=end_ts, source="computed")

    # A manually-corrected trip covering this exact date range (see
    # _rebuild_trips' own snapshot) wins outright over the geometric
    # best-guess below - that's the entire point of the correction.
    saved = (corrections or {}).get((start_ts, end_ts))
    if saved is not None:
        trip.name, trip.primary_city, trip.primary_country, trip.primary_country_code = saved
        trip.manually_corrected = True
    else:
        trip.primary_city, trip.primary_country, trip.primary_country_code = _primary_city_country(run)

    session.add(trip)
    session.flush()
    for visit in run:
        visit.trip_id = trip.id
