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

# A real GPS fix reports single-digit-to-tens-of-metres accuracy; confirmed
# live that OwnTracks occasionally falls back to cell/WiFi triangulation
# (hundreds of metres of error) when it can't get a proper GPS lock, and
# that a single one of these bad points next to a real one is enough to
# imply a multi-hundred-km/h "jump" between them - misclassified as flying,
# and drawn on the map as a wild spike off the real route. acc is nullable
# (some sources never report it) - only an *explicit* value over this
# threshold is treated as untrustworthy, not the absence of one.
MAX_TRUSTED_ACCURACY_M = 100.0

# Segments shorter than this are noise (GPS drift between two visits at
# effectively the same spot) and are dropped rather than recorded.
MIN_SEGMENT_DISTANCE_M = 20.0

# A tracking dropout inside a leg (phone killed in the background, then
# resumed - a confirmed recurring issue, see the Diagnostics tab) otherwise
# gets silently blended into the leg's average speed: the whole elapsed time
# from before the dropout to after it is divided into the crow-flies
# distance, which is small enough relative to the huge duration that a
# genuine ~1h drive spanning a ~9h blackout classifies as several hours of
# "walking" at ~1 km/h - confirmed live, 5 August 2026. Any points-to-points
# gap this long inside a leg splits it into separate sub-legs instead, each
# classified on its own; the blank stretch between them is left unrepresented
# rather than bridged, same as everywhere else "no data" already means "no
# segment" rather than a fabricated one.
MAX_INTRA_LEG_GAP_S = 30 * 60

# A stretch away from home shorter than this doesn't count as a "trip" - see
# _flush_trip_run for why (routine errands outside the home radius otherwise
# flood the Trips view).
TRIP_MIN_DURATION_S = 6 * 3600

# A gap this long between two consecutive away-classified visits, with both
# visits still close to home (see HOME_LOCALITY_RADIUS_M), is treated as an
# implicit overnight return home even with no explicit Home visit logged in
# between - see _rebuild_trips' away-run loop for why: confirmed live, three
# separate days of ordinary Bedford errands (school run, supermarket, a
# shopping centre) got strung into one fabricated three-day "trip" purely
# because Google's own Timeline export didn't emit an explicit Home visit on
# two of those three nights, even though the person plainly slept at home
# each night - nothing else in the data ever told the algorithm they'd gone
# home in between. Deliberately shorter than a real overnight stay away
# (hotel checkout the next morning) would need to be treated as a break.
MAX_AWAY_GAP_S = 8 * 3600

# How far from home still counts as "the same local area" for the overnight-
# gap rule above - wide enough to cover routine errands a few km out (the
# Bedford example above topped out around 5.2km) without also covering a
# genuine day trip or short break to a real, distinct destination. Distinct
# from a HomePeriod's own (much tighter) radius_m, which exists to separate
# "at home" from "anywhere else at all", not to define the whole local area.
HOME_LOCALITY_RADIUS_M = 15_000.0

# A gap this long with *zero* recorded activity at all - no visit, no
# TripSegment, nothing - is treated as a likely return home even when
# neither side of the gap is itself near home. Confirmed live: a Cotswold
# hotel stay and a separate flight to Italy got fused into one fabricated
# 22-day "trip", because the gap between them (a genuine ~46.5h silence)
# sits between two segments that are themselves nowhere near home (a
# Cotswold hotel, Stansted Airport) - the HOME_LOCALITY_RADIUS_M rule above
# never triggers there, since it needs at least one *endpoint* near home,
# and a real trip's own quiet middle usually isn't. But a driving segment
# ending right at the start of that gap (140km, a plausible drive home from
# the Cotswolds) and another starting right at its end (89km, a plausible
# drive from home to Stansted) show the person was moving right up to both
# edges of an otherwise completely blank stretch - strong circumstantial
# evidence they went home in between, even with no Visit proving it.
# Deliberately much longer than MAX_AWAY_GAP_S (that rule already covers
# the near-home case with a lower bar) - a full day-plus of total silence
# is rare enough within a genuine single trip that treating it as a
# probable break is the safer default, matching the same "no insight beats
# a wrong-looking one" reasoning used throughout this module.
TOTAL_BLANK_GAP_S = 24 * 3600


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
        .filter(
            LocationPoint.source == "owntracks",
            (LocationPoint.acc.is_(None)) | (LocationPoint.acc <= MAX_TRUSTED_ACCURACY_M),
        )
        .order_by(LocationPoint.tst)
        .all()
    ]
    if not points:
        return

    _rebuild_visits(session, points)
    session.flush()
    _rebuild_trip_segments(session)
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


def _rebuild_trip_segments(session: Session) -> None:
    # Same source-scoping as _rebuild_visits - never touches imported segments.
    session.execute(delete(TripSegment).where(TripSegment.source == "owntracks"))

    # Loads its own points rather than taking them as a parameter - lets a
    # manual-visit insert (see app/api/events.py) call this standalone to
    # re-derive the segments either side of the new visit, without having to
    # duplicate process_all's own point-loading query.
    points = [
        RawPoint(p.lat, p.lon, p.tst)
        for p in session.query(LocationPoint)
        .filter(
            LocationPoint.source == "owntracks",
            (LocationPoint.acc.is_(None)) | (LocationPoint.acc <= MAX_TRUSTED_ACCURACY_M),
        )
        .order_by(LocationPoint.tst)
        .all()
    ]
    if not points:
        return

    # Pairs OwnTracks-derived visits AND manually-inserted ones (source=
    # "manual" - see app/api/events.py's create_visit) - a manual visit is a
    # real boundary the raw GPS legs need to be re-split around exactly like
    # any other stay, it just didn't come from the automatic clustering pass.
    # Imported visits are excluded: they already have their own Google-
    # classified segments and there are no raw points to build legs from
    # between them anyway (location_points only ever holds live pings).
    visits = (
        session.query(Visit)
        .filter(Visit.source.in_(("owntracks", "manual")))
        .order_by(Visit.start_ts)
        .all()
    )
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

        sub_legs = _split_leg_on_gaps(leg)
        for i, sub_leg in enumerate(sub_legs):
            if len(sub_leg) < 2:
                continue

            distance_m, duration_s = distance_and_duration(sub_leg)
            if distance_m < MIN_SEGMENT_DISTANCE_M or duration_s <= 0:
                continue

            avg_kmh = (distance_m / 1000.0) / (duration_s / 3600.0)
            mode = classify_mode_by_speed(avg_kmh)

            # A gap-split sub-leg in the middle isn't actually adjacent to
            # either real visit any more - only the first/last sub-leg still
            # is, so those FKs stay null for the others (already handled as
            # routinely-null elsewhere, see app/api/routing.py).
            session.add(
                TripSegment(
                    start_ts=sub_leg[0].tst,
                    end_ts=sub_leg[-1].tst,
                    mode=mode,
                    distance_m=distance_m,
                    duration_s=duration_s,
                    start_visit_id=prev.id if i == 0 else None,
                    end_visit_id=nxt.id if i == len(sub_legs) - 1 else None,
                )
            )


def _split_leg_on_gaps(leg: list[RawPoint]) -> list[list[RawPoint]]:
    """Breaks a leg into contiguous sub-legs wherever consecutive points are
    more than MAX_INTRA_LEG_GAP_S apart - see that constant's own comment."""
    sub_legs: list[list[RawPoint]] = [[leg[0]]]
    for point in leg[1:]:
        if point.tst - sub_legs[-1][-1].tst > MAX_INTRA_LEG_GAP_S:
            sub_legs.append([])
        sub_legs[-1].append(point)
    return sub_legs


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


PHANTOM_VISIT_DURATION_S = 7200  # exactly 2 hours


def _remove_contained_google_visits(session: Session) -> int:
    """Deletes google_import visits matching Google Timeline's own
    low-confidence fallback signature: start_ts AND end_ts both land exactly
    on the hour, and the duration is exactly PHANTOM_VISIT_DURATION_S. No
    genuine GPS-clustered visit in this entire dataset lands on an exact
    hour boundary on both ends by chance - out of 15000+ real visits, every
    single one with this shape turned out to be spurious once checked
    against the surrounding data.

    Originally this only deleted visits fully time-contained within another,
    longer visit (the Penzance/Manchester/Benidoleig case: a fabricated
    "Home" visit sitting entirely inside a real, longer, concurrent hotel
    stay elsewhere - you can't genuinely be at Home for two hours in the
    middle of a twenty-hour Cornwall hotel stay). That containment
    requirement turned out to be too strict: a fabricated visit that
    straddles the boundary between two adjacent *real* visits - contained
    fully by neither - produces the exact same trip-splitting bug (confirmed
    live: a Reading business trip split in two by a fabricated 18:00-20:00
    "Home" visit that overlapped the tail of one real visit and the head of
    the next, without being enclosed by either). The signature alone is
    sufficient and catches both shapes; containment is no longer checked.

    Left in the data, these also inflate the affected place's visit count
    (Home alone gained roughly 5000 of these) and clutter the Day view on
    the affected date with a visit that never happened.
    """
    result = session.execute(
        delete(Visit).where(
            Visit.source == "google_import",
            (Visit.end_ts - Visit.start_ts) == PHANTOM_VISIT_DURATION_S,
            Visit.start_ts % 3600 == 0,
            Visit.end_ts % 3600 == 0,
        )
    )
    return result.rowcount


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

    _remove_contained_google_visits(session)

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

    def _home_period_for(visit: Visit) -> HomePeriod | None:
        # Unlike _tag_home (any period, ever), this needs the *one* period
        # that actually covers this visit's own timestamp - the whole point
        # of HomePeriod is that a visit near an old address should count as
        # "at home" only while that was genuinely still home, not forever.
        for period in home_periods:
            if (period.start_ts is None or visit.start_ts >= period.start_ts) and (
                period.end_ts is None or visit.start_ts < period.end_ts
            ):
                return period
        return None

    def _is_away(visit: Visit) -> bool:
        # Work-category visits are never even passed to this function - see
        # the main loop below, which skips them outright rather than
        # asking whether they're "away". Kept out of this function
        # entirely rather than just returning False here, because a visit
        # right before this one already flushed the run (a plain day
        # commute) shouldn't fabricate one from Work alone - but a Work
        # visit sitting *inside* an already-active away-run (visited your
        # own office while staying overnight on a business trip) shouldn't
        # end that trip either, and returning False from here would do
        # exactly that (see _flush_trip_run's own caller for what "not
        # away" does to a run in progress).
        period = _home_period_for(visit)
        # A visit with no covering period at all (before the earliest known
        # home, or in a gap between two) is treated as "not away" rather
        # than guessed at - silently fabricating a trip out of a genuinely
        # unknown-era visit is worse than just leaving it ungrouped.
        if period is None:
            return False
        return haversine_m(visit.lat, visit.lon, period.lat, period.lon) > period.radius_m

    def _near_home(visit: Visit) -> bool:
        # Wider than a HomePeriod's own radius_m on purpose - see
        # HOME_LOCALITY_RADIUS_M for why - and no covering period at all
        # counts as "not near", same conservative default as _is_away above.
        period = _home_period_for(visit)
        return period is not None and haversine_m(visit.lat, visit.lon, period.lat, period.lon) <= HOME_LOCALITY_RADIUS_M

    # See TOTAL_BLANK_GAP_S for why this exists. Loaded once here rather
    # than queried per-gap (thousands of visits would mean thousands of
    # repeated queries otherwise) - only ever scanned when a gap has
    # already cleared the (rare) 24h threshold below, so an O(segments)
    # scan per qualifying gap is cheap in aggregate even without an index.
    segment_rows = session.query(TripSegment.start_ts, TripSegment.end_ts).all()

    def _gap_max_blank_s(gap_start: int, gap_end: int) -> int:
        # The largest contiguous stretch of [gap_start, gap_end) not
        # covered by any segment - deliberately not just "is any segment
        # anywhere in the gap": a real segment can legitimately touch one
        # or both *edges* of an otherwise-blank gap (a drive away from home
        # right before it starts, a drive to the airport right before it
        # ends) without that meaning the long blank stretch in the *middle*
        # was actually covered by anything.
        overlapping = sorted(
            (s for s in segment_rows if s.start_ts < gap_end and s.end_ts > gap_start),
            key=lambda s: s.start_ts,
        )
        cursor = gap_start
        max_blank = 0
        for s in overlapping:
            seg_start = max(s.start_ts, gap_start)
            if seg_start > cursor:
                max_blank = max(max_blank, seg_start - cursor)
            cursor = max(cursor, min(s.end_ts, gap_end))
        return max(max_blank, gap_end - cursor)

    run: list[Visit] = []
    for visit in visits:
        if visit.place and visit.place.category == "Work":
            # Neither extends a run nor flushes one already in progress -
            # confirmed live both failure modes are real. A daily commute
            # (Home -> Work -> Home) fabricated 583 separate one-day
            # "trips" out of nothing but going to work when Work counted
            # as "away" in its own right; a Work visit sitting inside an
            # already-active away-run (visited your own office while
            # staying overnight on a genuine business trip) wrongly ended
            # that trip when Work instead counted as an explicit "not
            # away" break. Simply skipping it avoids both: a run that's
            # empty stays empty (no trip fabricated from Work alone,
            # matching the Home-Work-Home case), and a run already in
            # progress carries on exactly as it was (the trip continues
            # straight through the office visit, matching the business-
            # trip case).
            continue
        if not _is_away(visit):
            _flush_trip_run(session, run, corrections)
            run = []
            continue
        if run:
            gap_s = visit.start_ts - run[-1].end_ts
            # See MAX_AWAY_GAP_S: a long quiet stretch with no visit at all
            # is ambiguous on its own (asleep at home with nothing logged,
            # or asleep at a hotel three hundred miles away). Originally
            # required *both* the visit before and the visit after the gap
            # to be near home before treating it as an implicit return - but
            # confirmed live that's too strict: a mundane local errand (an
            # ALDI run in Bedford) sitting right next to a genuine multi-day
            # trip (a Cotswold hotel stay, a flight out of Stansted) merged
            # the two together into one fabricated 22-day "trip", because
            # the *other* side of each gap was always genuinely remote by
            # definition - a real trip's own departure/return is exactly the
            # case where only one side of the gap is ever near home. Either
            # side being near home is enough: if you were home-ish right
            # before or right after a long gap, you almost certainly went
            # properly home in between, regardless of how far the trip on
            # the other side of that gap actually reaches.
            near_home_break = gap_s > MAX_AWAY_GAP_S and (_near_home(run[-1]) or _near_home(visit))
            # See TOTAL_BLANK_GAP_S: covers the case above's own blind spot -
            # neither side near home at all (a Cotswold hotel, an airport),
            # but a genuinely long stretch with *no* activity of any kind
            # bridging them.
            total_blank_break = (
                gap_s > TOTAL_BLANK_GAP_S and _gap_max_blank_s(run[-1].end_ts, visit.start_ts) > TOTAL_BLANK_GAP_S
            )
            if near_home_break or total_blank_break:
                _flush_trip_run(session, run, corrections)
                run = []
        run.append(visit)
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
