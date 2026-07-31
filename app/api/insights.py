from __future__ import annotations

import calendar
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.country_names import country_name_en
from app.db import db_dependency, get_setting
from app.models import LocationPoint, Place, Trip, TripSegment, Visit

router = APIRouter()

TREND_MONTHS = 6
# Display order when present - not an allow-list. A mode only appears in the
# response if it actually has data; this just keeps the order consistent
# (walking first, flying last) rather than whatever order the query returns.
TRAVEL_MODE_ORDER = [
    "walking", "cycling", "driving", "taxi", "bus", "train", "subway", "tram", "ferry", "boating", "flying",
]
# Same convention as TRAVEL_MODE_ORDER above - display order when present,
# not an allow-list. Categories not listed here (e.g. a user-typed custom
# category from a place correction) still appear, just after these.
VISIT_CATEGORIES = ["Home", "Work", "Food and drink", "Shopping", "Hotels", "Culture", "Sports", "Airports", "Other places"]

# Thresholds matched to what the frontend actually displays (formatMiles
# rounds anything under 0.1mi to "0 mi", formatDuration rounds under 60s to
# "0 min") - a card is only worth showing for the month currently being
# viewed if it would show something other than that zero. Trend history is
# a different question (a category can fade to nothing over 6 months and
# that's worth seeing), so this only gates the *current* month's own card.
TRAVEL_MIN_DISPLAY_M = 160.9344  # 0.1 mile
VISIT_MIN_DISPLAY_S = 60


def _month_bounds(year: int, month: int) -> tuple[int, int]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    days = calendar.monthrange(year, month)[1]
    end = datetime(year, month, days, 23, 59, 59, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp()) + 1


def _step_back(year: int, month: int, n: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) - n
    return total // 12, total % 12 + 1


def _travel_totals(session: Session, start_ts: int, end_ts: int) -> dict[str, dict[str, float]]:
    rows = (
        session.query(TripSegment.mode, TripSegment.distance_m, TripSegment.duration_s)
        .filter(TripSegment.start_ts >= start_ts, TripSegment.start_ts < end_ts)
        .all()
    )
    totals: dict[str, dict[str, float]] = defaultdict(lambda: {"distance_m": 0.0, "duration_s": 0.0})
    for mode, distance_m, duration_s in rows:
        totals[mode]["distance_m"] += distance_m
        totals[mode]["duration_s"] += duration_s
    return totals


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * earth_radius_m * atan2(sqrt(a), sqrt(1 - a))


def _visit_totals(session: Session, start_ts: int, end_ts: int) -> dict[str, float]:
    rows = (
        session.query(Place.category, Visit.start_ts, Visit.end_ts)
        .join(Place, Visit.place_id == Place.id)
        .filter(Visit.start_ts >= start_ts, Visit.start_ts < end_ts)
        .all()
    )
    totals: dict[str, float] = defaultdict(float)
    for category, v_start, v_end in rows:
        totals[category] += max(v_end - v_start, 0)
    return totals


@router.get("/api/insights/highlights")
def get_insights_highlights(session: Session = Depends(db_dependency)):
    """All-time records, not scoped to any particular month - the tab's
    monthly tiles were the whole of Insights before this, which read as
    thin next to the rest of the app's now much richer views. A handful of
    plain aggregate queries is plenty at a personal-device data scale (tens
    of thousands of rows, not millions) - no need for anything fancier."""
    total_visits = session.query(func.count(Visit.id)).scalar() or 0
    total_distance_m = session.query(func.sum(TripSegment.distance_m)).scalar() or 0.0
    total_countries = (
        session.query(func.count(func.distinct(Place.country_code))).filter(Place.country_code.isnot(None)).scalar() or 0
    )
    total_cities = session.query(func.count(func.distinct(Place.city))).filter(Place.city.isnot(None)).scalar() or 0

    # Trip.start_ts/end_ts already define "away from home" stretches (see
    # Trip's own model docstring), so summing their spans directly is the
    # honest measure of "days spent travelling" - no need to re-derive it
    # from Visit rows. +86399 (not +86400) before the day-floor division:
    # a trip's own end_ts is a real moment mid-day, not a midnight boundary,
    # so this rounds a 36-hour trip to 2 days rather than 1 or 3.
    trip_rows = session.query(Trip.start_ts, Trip.end_ts).order_by(Trip.start_ts).all()
    total_trip_days = sum((end - start + 86399) // 86400 for start, end in trip_rows)
    avg_trip_days = round(total_trip_days / len(trip_rows), 1) if trip_rows else None
    longest_gap_days = (
        max((b_start - a_end) // 86400 for (_, a_end), (b_start, _) in zip(trip_rows, trip_rows[1:]))
        if len(trip_rows) > 1
        else None
    )
    days_since_last_trip = (int(datetime.now(timezone.utc).timestamp()) - trip_rows[-1][1]) // 86400 if trip_rows else None

    birth_date_str = get_setting(session, "birth_date", "")
    life_percent = None
    if birth_date_str:
        try:
            birth_ts = int(datetime.strptime(birth_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            days_alive = (int(datetime.now(timezone.utc).timestamp()) - birth_ts) / 86400
            if days_alive > 0:
                life_percent = round(total_trip_days / days_alive * 100, 2)
        except ValueError:
            pass  # malformed birth_date - degrades to no stat, same as unset

    day_expr = func.strftime("%Y-%m-%d", Visit.start_ts, "unixepoch")
    busiest_day_row = (
        session.query(day_expr.label("day"), func.count(Visit.id).label("cnt"))
        .group_by("day")
        .order_by(func.count(Visit.id).desc())
        .first()
    )

    longest_trip_row = (
        session.query(Trip.id, Trip.name, Trip.primary_city, Trip.primary_country, Trip.start_ts, Trip.end_ts)
        .order_by((Trip.end_ts - Trip.start_ts).desc())
        .first()
    )

    most_visited_row = (
        session.query(Place.id, Place.name, Place.city, Place.category, func.count(Visit.id).label("cnt"))
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.name.isnot(None))
        .group_by(Place.id)
        .order_by(func.count(Visit.id).desc())
        .first()
    )

    # Widening Records beyond the original 6 (longest trip / most-visited
    # place / busiest day / avg trip length / longest gap / days since last
    # trip) - those were all "trip-shape" stats; these add a place-shape one
    # (country/city/farthest), a calendar one (most trips in a year, first
    # trip on record) and a single-leg one (longest journey), so the tab
    # reads as a real spread of record types rather than six variations on
    # "how long was a trip".
    country_row = (
        session.query(Place.country_code, Place.country, func.count(Visit.id).label("cnt"))
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.country_code.isnot(None))
        .group_by(Place.country_code)
        .order_by(func.count(Visit.id).desc())
        .first()
    )

    city_row = (
        session.query(Place.city, func.count(Visit.id).label("cnt"))
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.city.isnot(None))
        .group_by(Place.city)
        .order_by(func.count(Visit.id).desc())
        .first()
    )

    # Trip *count* per calendar year, not distance - the yearly distance
    # trend already has its own home in Trends (and in the Overview
    # "peak_year" story), so this deliberately measures something else:
    # how many separate trips were packed into one year.
    trip_year_expr = func.strftime("%Y", Trip.start_ts, "unixepoch")
    busiest_trip_year_row = (
        session.query(trip_year_expr.label("year"), func.count(Trip.id).label("cnt"))
        .group_by("year")
        .order_by(func.count(Trip.id).desc())
        .first()
    )

    first_trip_row = (
        session.query(Trip.id, Trip.name, Trip.primary_city, Trip.primary_country, Trip.start_ts)
        .order_by(Trip.start_ts.asc())
        .first()
    )

    # Farthest place ever visited, as the crow flies from home - only
    # possible once home_lat/home_lon are set (see settings.py's own
    # curl-only bootstrapping note); at personal-device scale (hundreds, not
    # millions, of distinct places) a plain Python max() over every place is
    # simpler and plenty fast, no need for a spatial index.
    farthest_place = None
    home_lat_str = get_setting(session, "home_lat", "")
    home_lon_str = get_setting(session, "home_lon", "")
    if home_lat_str and home_lon_str:
        home_lat, home_lon = float(home_lat_str), float(home_lon_str)
        place_rows = (
            session.query(
                Place.id, Place.name, Place.city, Place.country, Place.category, Place.lat_round, Place.lon_round
            )
            .join(Visit, Visit.place_id == Place.id)
            .filter(Place.name.isnot(None))
            .distinct()
            .all()
        )
        best_row, best_dist = None, -1.0
        for p in place_rows:
            d = _haversine_m(home_lat, home_lon, p.lat_round, p.lon_round)
            if d > best_dist:
                best_row, best_dist = p, d
        if best_row:
            farthest_place = {
                "place_id": best_row.id,
                "name": best_row.name,
                "city": best_row.city,
                "country": best_row.country,
                "category": best_row.category,
                "distance_m": best_dist,
            }

    # Longest single travel leg ever taken (one TripSegment, not a whole
    # trip). start_visit_id/end_visit_id are only ever populated for
    # source="owntracks" segments (see TripSegment/Visit's own docstrings) -
    # every imported segment (google_import, kml_import - i.e. almost all
    # real history) leaves them null, so the FK can't be used to name the
    # endpoints. Instead, find the nearest Visit ending before this segment
    # started and the nearest one starting after it ended - the same
    # "whichever visit sits either side of this gap in time" relationship
    # the segment already has in every timeline view, just derived by time
    # instead of a join.
    #
    # The segment's own distance_m is trusted almost everywhere else in this
    # app (imported verbatim from Google's own topCandidate.distanceMeters -
    # see import_google_timeline.py's own reasoning for not re-deriving it),
    # and spot-checking the 20 longest flights confirmed that trust is
    # earned in the overwhelming majority of cases (stored value within a
    # few % of the real endpoint-to-endpoint distance). But exactly one real
    # segment in this dataset was a genuine outlier - Google's own figure
    # ~3x the true straight-line distance between its resolved endpoints,
    # which the "X -> Y, N mi" phrasing on this card would otherwise repeat
    # as a confidently wrong headline number. Since this card already has to
    # resolve real coordinates for its endpoint labels anyway, checking the
    # top handful of candidates against their own true distance and ranking
    # by *that* costs little and can't be fooled by a single bad upstream
    # number the way "just trust the biggest stored value" can.
    LONGEST_JOURNEY_CANDIDATES = 25
    candidate_rows = (
        session.query(TripSegment.mode, TripSegment.distance_m, TripSegment.start_ts, TripSegment.end_ts)
        .order_by(TripSegment.distance_m.desc())
        .limit(LONGEST_JOURNEY_CANDIDATES)
        .all()
    )

    def _nearest_place(filter_expr, order_expr):
        return (
            session.query(Place.name, Place.city, Place.country, Place.lat_round, Place.lon_round)
            .join(Visit, Visit.place_id == Place.id)
            .filter(filter_expr)
            .order_by(order_expr)
            .first()
        )

    best_candidate = None  # (real_distance_m, mode, start_ts, before_row, after_row)
    for cand in candidate_rows:
        # Deliberately not filtering to Place.name.isnot(None) here - doing so
        # skips straight past a genuinely-nearest but never-geocoded stub
        # Place (e.g. a brief airport connection Nominatim was never asked
        # about) to whatever *named* visit happens to come next, which for a
        # long-haul flight can be days later and back at the other end of the
        # trip - a confidently wrong "X to X" beats no answer, which this
        # exists specifically to avoid.
        before_row = _nearest_place(Visit.end_ts <= cand.start_ts, Visit.end_ts.desc())
        after_row = _nearest_place(Visit.start_ts >= cand.end_ts, Visit.start_ts.asc())
        if not before_row or not after_row:
            continue
        real_distance_m = _haversine_m(
            before_row.lat_round, before_row.lon_round, after_row.lat_round, after_row.lon_round
        )
        if best_candidate is None or real_distance_m > best_candidate[0]:
            best_candidate = (real_distance_m, cand.mode, cand.start_ts, before_row, after_row)

    longest_journey = None
    if best_candidate:
        real_distance_m, longest_mode, longest_start_ts, before_row, after_row = best_candidate
        longest_journey = {
            "mode": longest_mode,
            "distance_m": real_distance_m,
            "start_name": before_row.name or before_row.city or before_row.country,
            "end_name": after_row.name or after_row.city or after_row.country,
            "day": datetime.fromtimestamp(longest_start_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
        }

    # Longest journey *per mode* - "longest walk", "longest train journey"
    # etc, not just one overall headline. Only "flying" gets the headline's
    # own real-haversine re-ranking treatment (walk every top candidate,
    # keep whichever resolvable one has the largest *real* distance) -
    # that exists specifically to catch one documented failure mode, a
    # long-haul flight whose stored distance_m was ~3x its true endpoint-
    # to-endpoint distance (see the headline computation's own comment).
    # Every other mode trusts its single top segment's stored distance_m
    # outright instead of re-ranking - confirmed live this matters, not
    # just theoretical: applying the flying-style re-ranking to "walking"
    # picked out a nonsensical 5,497km "walk", because for a short, frequent
    # mode the *largest real distance among resolvable candidates* is far
    # more likely to be a case where the nearest-visit-in-time genuinely
    # sits nowhere near the actual walk (a data gap either side of it) than
    # a real long walk - exactly the kind of confidently-wrong number this
    # whole approach exists to avoid, just from the opposite direction.
    # Short/local segment distances come from straightforward GPS haversine
    # in the OwnTracks pipeline (or well-bounded short Google segments) and
    # don't carry flying's own known bad-data risk, so the plain stored
    # figure is the trustworthy one for them. Only ever includes a mode
    # that actually has at least one segment - same "dynamic, not a
    # hardcoded list" convention as TRAVEL_MODE_ORDER above.
    FLYING_CANDIDATES = 20
    longest_by_mode: dict[str, dict] = {}
    modes_present = [row[0] for row in session.query(TripSegment.mode).distinct().all()]
    for mode in modes_present:
        if mode == "flying":
            # Deterministic tie-break (id, not just distance_m) - several
            # segments can share the exact same stored distance (duplicate/
            # near-duplicate rows from import), and SQLite doesn't
            # guarantee a stable order among ties on a bare ORDER BY
            # distance_m otherwise.
            candidates = (
                session.query(TripSegment.distance_m, TripSegment.start_ts, TripSegment.end_ts)
                .filter(TripSegment.mode == mode)
                .order_by(TripSegment.distance_m.desc(), TripSegment.id)
                .limit(FLYING_CANDIDATES)
                .all()
            )
            resolved = None  # (real_distance_m, start_ts, before_row, after_row)
            for cand in candidates:
                before_row = _nearest_place(Visit.end_ts <= cand.start_ts, Visit.end_ts.desc())
                after_row = _nearest_place(Visit.start_ts >= cand.end_ts, Visit.start_ts.asc())
                if not before_row or not after_row:
                    continue
                real_distance = _haversine_m(before_row.lat_round, before_row.lon_round, after_row.lat_round, after_row.lon_round)
                # A resolvable endpoint can still be an unnamed geocoded stub
                # (a row exists, but name/city/country are all null) - not a
                # sign of a bad match, so this still has to check every
                # candidate rather than stopping at the first one that merely
                # *resolves*, or it can settle for a smaller real distance
                # than a later, properly-named candidate actually has.
                if resolved is None or real_distance > resolved[0]:
                    resolved = (real_distance, cand.start_ts, before_row, after_row)
            if resolved is None:
                continue
            distance_m, start_ts, before_row, after_row = resolved
        else:
            top = (
                session.query(TripSegment.distance_m, TripSegment.start_ts, TripSegment.end_ts)
                .filter(TripSegment.mode == mode)
                .order_by(TripSegment.distance_m.desc())
                .first()
            )
            if top is None:
                continue
            before_row = _nearest_place(Visit.end_ts <= top.start_ts, Visit.end_ts.desc())
            after_row = _nearest_place(Visit.start_ts >= top.end_ts, Visit.start_ts.asc())
            distance_m, start_ts = top.distance_m, top.start_ts
        longest_by_mode[mode] = {
            "distance_m": distance_m,
            "start_name": (before_row.name or before_row.city or before_row.country) if before_row else None,
            "end_name": (after_row.name or after_row.city or after_row.country) if after_row else None,
            "day": datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
        }

    # Shortest trip - the other end of "longest trip" (min duration, not min
    # distance - a trip has no single "distance" of its own). >0 days only:
    # a same-day blip that still cleared TRIP_MIN_DURATION_S is a real trip,
    # but "0 days" would read as a bug, not a record.
    shortest_trip_row = (
        session.query(Trip.id, Trip.name, Trip.primary_city, Trip.primary_country, Trip.start_ts, Trip.end_ts)
        .filter(Trip.end_ts - Trip.start_ts >= 86400)
        .order_by((Trip.end_ts - Trip.start_ts).asc())
        .first()
    )

    # Most countries visited within one single trip - a genuine multi-
    # country tour reads differently from "went to France for a week", and
    # nothing else in Records surfaces that. Computed in Python over each
    # trip's own visits (personal-device scale: hundreds of trips, not
    # thousands) rather than a grouped SQL query, since it needs a per-trip
    # distinct-country count, not a global one.
    most_countries_trip = None
    best_country_count = 1  # only worth a record at 2+ countries
    for trip in session.query(Trip).all():
        codes = {v.place.country_code for v in trip.visits if v.place and v.place.country_code}
        if len(codes) > best_country_count:
            best_country_count = len(codes)
            most_countries_trip = {
                "trip_id": trip.id,
                "name": trip.name,
                "primary_city": trip.primary_city,
                "primary_country": trip.primary_country,
                "country_count": len(codes),
            }

    # "Favourite" category - most total *time* spent, not most visits (a
    # dozen 5-minute shop visits shouldn't outrank fewer but much longer
    # restaurant/hotel stays) - same measure _visit_totals already uses for
    # the monthly Breakdown bars, just summed across all history instead of
    # one month.
    category_duration: Counter[str] = Counter()
    for category, v_start, v_end in session.query(Place.category, Visit.start_ts, Visit.end_ts).join(
        Place, Visit.place_id == Place.id
    ):
        category_duration[category] += max(v_end - v_start, 0)
    favourite_category = None
    if category_duration:
        top_category, top_seconds = category_duration.most_common(1)[0]
        favourite_category = {"category": top_category, "duration_s": top_seconds}

    # Most-visited place *within* one specific category - "most-visited
    # train station" and "most-visited airport" are the two that read as
    # genuine records rather than trivia (everyone has a "usual" station or
    # airport; "most-visited shop" is just whichever supermarket is
    # nearest home, less interesting). Shares openPlaceRecord's own
    # category+place_id navigation on the frontend, so no new click-through
    # plumbing needed.
    def _most_visited_in_category(category: str) -> dict | None:
        row = (
            session.query(Place.id, Place.name, Place.city, func.count(Visit.id).label("cnt"))
            .join(Visit, Visit.place_id == Place.id)
            .filter(Place.category == category, Place.name.isnot(None))
            .group_by(Place.id)
            .order_by(func.count(Visit.id).desc())
            .first()
        )
        if row is None:
            return None
        return {"place_id": row.id, "name": row.name, "city": row.city, "category": category, "visit_count": row.cnt}

    most_visited_station = _most_visited_in_category("Transport")
    most_visited_airport = _most_visited_in_category("Airports")

    # The place most recently added to the map for the very first time -
    # "what's the newest place you've discovered", not "what have you seen
    # most recently" (that's just whatever's on today's Day view). Grouped
    # by place, taking each place's own *first* visit, then picking whichever
    # of those first-visit dates is itself the most recent.
    newest_place_row = (
        session.query(Place.id, Place.name, Place.city, Place.category, func.min(Visit.start_ts).label("first_seen"))
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.name.isnot(None))
        .group_by(Place.id)
        .order_by(func.min(Visit.start_ts).desc())
        .first()
    )
    newest_place = (
        {
            "place_id": newest_place_row.id,
            "name": newest_place_row.name,
            "city": newest_place_row.city,
            "category": newest_place_row.category,
            "day": datetime.fromtimestamp(newest_place_row.first_seen, tz=timezone.utc).strftime("%Y-%m-%d"),
        }
        if newest_place_row
        else None
    )

    # UK county coverage - "county" only exists inside a UK place's own
    # cached Nominatim response (raw_json), never as an indexed column (see
    # Place's own fields), so this reads it back out of that JSON blob
    # rather than needing a schema change just for two Records cards.
    # Places resolved via Google Places alone (no Nominatim address block -
    # see app/google_places.py's Essentials-tier field mask) have no
    # raw_json at all and are silently skipped, same as anywhere else in
    # this app that treats missing address data as "unknown", not "none".
    county_visits: Counter[str] = Counter()
    for place, visit_count in (
        session.query(Place, func.count(Visit.id))
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.country_code == "GB", Place.raw_json.isnot(None))
        .group_by(Place.id)
        .all()
    ):
        try:
            county = json.loads(place.raw_json).get("address", {}).get("county")
        except (ValueError, AttributeError):
            county = None
        if county:
            county_visits[county] += visit_count
    total_counties = len(county_visits)
    least_visited_county = None
    if county_visits:
        county, cnt = min(county_visits.items(), key=lambda kv: kv[1])
        least_visited_county = {"county": county, "visit_count": cnt}

    # Busiest calendar month ever, by visit count - the day-level version
    # ("busiest day ever") already exists; this is the same idea one level
    # zoomed out, and (unlike that one) isn't already covered by the Trends
    # subtab's own yearly/seasonal charts.
    month_expr = func.strftime("%Y-%m", Visit.start_ts, "unixepoch")
    busiest_month_row = (
        session.query(month_expr.label("month"), func.count(Visit.id).label("cnt"))
        .group_by("month")
        .order_by(func.count(Visit.id).desc())
        .first()
    )

    # ---- The "fun ones" - genuinely playful, still grounded in real data ----

    # Highest point reached - real recorded GPS altitude, not derived from
    # anything else. LocationPoint.alt is only ever populated for
    # source="owntracks" pings with a device that actually reported one
    # (not every phone/app session does), so this silently has less
    # coverage than most other records here - still worth surfacing when
    # it exists rather than skipping it for being incomplete, same as
    # every other "best available data" record in this endpoint.
    highest_point_row = (
        session.query(LocationPoint.alt, LocationPoint.tst)
        .filter(LocationPoint.alt.isnot(None))
        .order_by(LocationPoint.alt.desc())
        .first()
    )
    highest_point = None
    if highest_point_row:
        near = _nearest_place(Visit.start_ts <= highest_point_row.tst, Visit.start_ts.desc()) or _nearest_place(
            Visit.start_ts >= highest_point_row.tst, Visit.start_ts.asc()
        )
        highest_point = {
            "altitude_m": highest_point_row.alt,
            "place_name": near.name if near else None,
            "city": near.city if near else None,
            "day": datetime.fromtimestamp(highest_point_row.tst, tz=timezone.utc).strftime("%Y-%m-%d"),
        }

    # Fastest average speed across any single journey leg - distance/
    # duration computed in SQL and sorted directly, not LocationPoint.vel
    # (OwnTracks' own reported instantaneous speed): live inspection found
    # vel capped at exactly 40.0 across the whole dataset, a suspiciously
    # round ceiling that reads as a device/import artifact rather than a
    # real top speed, so it's not trustworthy for a headline number the way
    # distance_m/duration_s is. But a bare MAX() over that ratio isn't
    # trustworthy either - confirmed live it surfaced a "2005 km/h flight"
    # (stored distance_m carrying flying's own documented bad-distance
    # risk - see longest_by_mode's flying branch) and a "734 km/h train"
    # (a 138s segment with an implausibly large stored distance, i.e. a bad
    # segment, not a fast train). Being a MAX() over a *ratio* makes this
    # record uniquely exposed to any single distance/duration artifact
    # anywhere in the dataset, unlike longest_by_mode where distance alone
    # doesn't blow up the same way. Two guards: a real-world plausible top
    # speed per mode (generous ceilings - elite sustained pace, fastest
    # scheduled rail, realistic jet-stream-boosted cruise ground speed for
    # flying - not physical limits, just "further than this is bad data,
    # not a record"), and for flying specifically, the same real-haversine-
    # endpoint recomputation longest_by_mode already relies on rather than
    # trusting stored distance_m at all.
    MODE_SPEED_CEILING_KMH = {
        "walking": 25, "cycling": 60, "driving": 180, "taxi": 150, "bus": 130,
        "train": 350, "subway": 100, "tram": 80, "ferry": 90, "boating": 80,
        "flying": 1200,
    }
    FASTEST_MIN_DURATION_S = 60
    FASTEST_FLYING_CANDIDATES = 30
    fastest_journey = None
    best_kmh = 0.0
    for mode, ceiling_kmh in MODE_SPEED_CEILING_KMH.items():
        if mode == "flying":
            candidates = (
                session.query(TripSegment.duration_s, TripSegment.start_ts, TripSegment.end_ts)
                .filter(TripSegment.mode == mode, TripSegment.duration_s >= FASTEST_MIN_DURATION_S)
                .order_by((TripSegment.distance_m / TripSegment.duration_s).desc())
                .limit(FASTEST_FLYING_CANDIDATES)
                .all()
            )
            for cand in candidates:
                before_row = _nearest_place(Visit.end_ts <= cand.start_ts, Visit.end_ts.desc())
                after_row = _nearest_place(Visit.start_ts >= cand.end_ts, Visit.start_ts.asc())
                if not before_row or not after_row:
                    continue
                real_distance = _haversine_m(before_row.lat_round, before_row.lon_round, after_row.lat_round, after_row.lon_round)
                kmh = (real_distance / 1000) / (cand.duration_s / 3600)
                if kmh <= ceiling_kmh and kmh > best_kmh:
                    best_kmh = kmh
                    fastest_journey = {
                        "mode": mode,
                        "kmh": round(kmh),
                        "day": datetime.fromtimestamp(cand.start_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                    }
        else:
            row = (
                session.query(TripSegment.distance_m, TripSegment.duration_s, TripSegment.start_ts)
                .filter(
                    TripSegment.mode == mode,
                    TripSegment.duration_s >= FASTEST_MIN_DURATION_S,
                    TripSegment.distance_m / TripSegment.duration_s <= ceiling_kmh / 3.6,
                )
                .order_by((TripSegment.distance_m / TripSegment.duration_s).desc())
                .first()
            )
            if row is None:
                continue
            kmh = (row.distance_m / 1000) / (row.duration_s / 3600)
            if kmh > best_kmh:
                best_kmh = kmh
                fastest_journey = {
                    "mode": mode,
                    "kmh": round(kmh),
                    "day": datetime.fromtimestamp(row.start_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                }

    # McDonald's world tour - confirmed live this dataset has 34 distinct
    # branches across 231 visits, easily the most-represented chain by a
    # wide margin (Costa/KFC/Greggs/Starbucks/Subway all in single figures)
    # - genuinely the one "wild" record worth a dedicated card rather than
    # a generic "favourite chain" abstraction that would need guessing at
    # which chains matter without knowing the real distribution first.
    mcdonalds_count = session.query(func.count(func.distinct(Place.id))).filter(Place.name.ilike("%mcdonald%")).scalar() or 0
    mcdonalds_visits = (
        session.query(func.count(Visit.id)).join(Place, Visit.place_id == Place.id).filter(Place.name.ilike("%mcdonald%")).scalar()
        or 0
    )
    mcdonalds_tour = {"count": mcdonalds_count, "visit_count": mcdonalds_visits} if mcdonalds_count else None

    # Total time spent at airports - reuses category_duration (already
    # computed above for favourite_category) rather than a fresh query,
    # "dead time waiting to fly" framing rather than a neutral one.
    airport_time_s = category_duration.get("Airports", 0)
    airport_time = {"duration_s": airport_time_s} if airport_time_s else None

    # Longest single stay away from home - Home/Work excluded outright,
    # or this is trivially just "asleep in bed" every time (the single
    # longest Visit in a personal dataset is overwhelmingly likely to be
    # an overnight-plus stretch at home otherwise).
    longest_stay_row = (
        session.query(Visit.start_ts, Visit.end_ts, Place.name, Place.city)
        .join(Place, Visit.place_id == Place.id)
        .filter(Place.category.notin_(["Home", "Work"]))
        .order_by((Visit.end_ts - Visit.start_ts).desc())
        .first()
    )
    longest_stay = (
        {
            "place_name": longest_stay_row.name,
            "city": longest_stay_row.city,
            "duration_s": longest_stay_row.end_ts - longest_stay_row.start_ts,
            "day": datetime.fromtimestamp(longest_stay_row.start_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
        }
        if longest_stay_row
        else None
    )

    # Latest night-owl visit - rotates clock minutes so anything after
    # midnight (00:00-11:59) ranks *later* than the evening before it, or a
    # 01:00 start would lose to an 11pm one despite obviously being the
    # later night. Same UTC-clock-time convention every other date/time
    # grouping in this endpoint already uses (busiest_day, busiest_month
    # etc. all group by strftime(...,'unixepoch'), not a per-user local
    # timezone - there's no stored timezone to convert against). Loads
    # just the one start_ts column across every visit (personal-device
    # scale, same reasoning already used for farthest_place's plain
    # Python max()) rather than trying to express the rotation in SQL.
    all_visit_starts = [row[0] for row in session.query(Visit.start_ts).all()]

    def _night_rank(ts: int) -> int:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        minutes = dt.hour * 60 + dt.minute
        return minutes if minutes >= 12 * 60 else minutes + 24 * 60

    night_owl = None
    if all_visit_starts:
        night_owl_ts = max(all_visit_starts, key=_night_rank)
        v = (
            session.query(Place.name, Place.city)
            .join(Visit, Visit.place_id == Place.id)
            .filter(Visit.start_ts == night_owl_ts)
            .first()
        )
        dt = datetime.fromtimestamp(night_owl_ts, tz=timezone.utc)
        night_owl = {
            "place_name": v.name if v else None,
            "city": v.city if v else None,
            "time": dt.strftime("%H:%M"),
            "day": dt.strftime("%Y-%m-%d"),
        }

    # Longest day out - biggest span between a day's first and last visit
    # *start* time (not end time - a visit spanning past midnight would
    # otherwise inflate this with time that's really the next calendar
    # day's, the same class of bug already fixed elsewhere for midnight-
    # crossing display). >1 visit only, or every single-visit day
    # trivially "spans" zero.
    day_span_expr = func.strftime("%Y-%m-%d", Visit.start_ts, "unixepoch")
    longest_day_row = (
        session.query(day_span_expr.label("day"), func.min(Visit.start_ts).label("first_ts"), func.max(Visit.start_ts).label("last_ts"))
        .group_by("day")
        .having(func.count(Visit.id) > 1)
        .order_by((func.max(Visit.start_ts) - func.min(Visit.start_ts)).desc())
        .first()
    )
    longest_day_out = (
        {"day": longest_day_row.day, "duration_s": longest_day_row.last_ts - longest_day_row.first_ts}
        if longest_day_row
        else None
    )

    return {
        "total_visits": total_visits,
        "total_distance_m": total_distance_m,
        "total_countries": total_countries,
        "total_cities": total_cities,
        "total_trip_days": total_trip_days,
        "avg_trip_days": avg_trip_days,
        "longest_gap_days": longest_gap_days,
        "days_since_last_trip": days_since_last_trip,
        "life_percent": life_percent,
        "busiest_day": (
            {"day": busiest_day_row.day, "visit_count": busiest_day_row.cnt} if busiest_day_row else None
        ),
        "longest_trip": (
            {
                "trip_id": longest_trip_row.id,
                "name": longest_trip_row.name,
                "primary_city": longest_trip_row.primary_city,
                "primary_country": longest_trip_row.primary_country,
                "start_ts": longest_trip_row.start_ts,
                "end_ts": longest_trip_row.end_ts,
                "days": round((longest_trip_row.end_ts - longest_trip_row.start_ts) / 86400),
            }
            if longest_trip_row
            else None
        ),
        "most_visited_place": (
            {
                "place_id": most_visited_row.id,
                "name": most_visited_row.name,
                "city": most_visited_row.city,
                "category": most_visited_row.category,
                "visit_count": most_visited_row.cnt,
            }
            if most_visited_row
            else None
        ),
        "most_visited_country": (
            {
                "country": country_name_en(country_row.country_code, country_row.country),
                "country_code": country_row.country_code,
                "visit_count": country_row.cnt,
            }
            if country_row
            else None
        ),
        "most_visited_city": (
            {"city": city_row.city, "visit_count": city_row.cnt} if city_row else None
        ),
        "busiest_trip_year": (
            {"year": busiest_trip_year_row.year, "trip_count": busiest_trip_year_row.cnt}
            if busiest_trip_year_row
            else None
        ),
        "first_trip": (
            {
                "trip_id": first_trip_row.id,
                "name": first_trip_row.name,
                "primary_city": first_trip_row.primary_city,
                "primary_country": first_trip_row.primary_country,
                "day": datetime.fromtimestamp(first_trip_row.start_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                "years_ago": round((int(datetime.now(timezone.utc).timestamp()) - first_trip_row.start_ts) / 86400 / 365.25, 1),
            }
            if first_trip_row
            else None
        ),
        "farthest_place": farthest_place,
        "longest_journey": longest_journey,
        "longest_by_mode": longest_by_mode,
        "shortest_trip": (
            {
                "trip_id": shortest_trip_row.id,
                "name": shortest_trip_row.name,
                "primary_city": shortest_trip_row.primary_city,
                "primary_country": shortest_trip_row.primary_country,
                "days": round((shortest_trip_row.end_ts - shortest_trip_row.start_ts) / 86400),
            }
            if shortest_trip_row
            else None
        ),
        "most_countries_trip": most_countries_trip,
        "favourite_category": favourite_category,
        "most_visited_station": most_visited_station,
        "most_visited_airport": most_visited_airport,
        "newest_place": newest_place,
        "total_counties": total_counties,
        "least_visited_county": least_visited_county,
        "busiest_month": (
            {"month": busiest_month_row.month, "visit_count": busiest_month_row.cnt} if busiest_month_row else None
        ),
        "highest_point": highest_point,
        "fastest_journey": fastest_journey,
        "mcdonalds_tour": mcdonalds_tour,
        "airport_time": airport_time,
        "longest_stay": longest_stay,
        "night_owl": night_owl,
        "longest_day_out": longest_day_out,
    }


@router.get("/api/insights/yearly")
def get_insights_yearly(session: Session = Depends(db_dependency)):
    """Per-calendar-year distance/visit totals across all recorded history -
    the Trends subtab's year-over-year chart. Zero-filled between the first
    and last year with any data, same reasoning as the Day view's own
    monthly density chart: an evenly-spaced timeline needs explicit zeros,
    not gaps silently skipped."""
    year_expr = func.strftime("%Y", TripSegment.start_ts, "unixepoch")
    distance_rows = dict(
        session.query(year_expr.label("year"), func.sum(TripSegment.distance_m)).group_by("year").all()
    )
    visit_year_expr = func.strftime("%Y", Visit.start_ts, "unixepoch")
    visit_rows = dict(session.query(visit_year_expr.label("year"), func.count(Visit.id)).group_by("year").all())

    if not distance_rows and not visit_rows:
        return {"years": []}
    all_years = {int(y) for y in distance_rows} | {int(y) for y in visit_rows}
    first_year, last_year = min(all_years), max(all_years)

    return {
        "years": [
            {
                "year": y,
                "distance_m": distance_rows.get(str(y), 0.0),
                "visit_count": visit_rows.get(str(y), 0),
            }
            for y in range(first_year, last_year + 1)
        ]
    }


@router.get("/api/insights/seasonality")
def get_insights_seasonality(session: Session = Depends(db_dependency)):
    """Per-calendar-month (Jan-Dec) totals aggregated across every year of
    history - "which month do you actually travel most", as distinct from
    the yearly chart's "which year". Always 12 entries regardless of how
    much history exists, since a calendar month is a fixed, complete scale
    (there's no equivalent of "zero-filling a gap" here - month 1-12 always
    all exist)."""
    month_expr = func.strftime("%m", TripSegment.start_ts, "unixepoch")
    distance_rows = dict(
        session.query(month_expr.label("month"), func.sum(TripSegment.distance_m)).group_by("month").all()
    )
    visit_month_expr = func.strftime("%m", Visit.start_ts, "unixepoch")
    visit_rows = dict(session.query(visit_month_expr.label("month"), func.count(Visit.id)).group_by("month").all())

    return {
        "months": [
            {
                "month": m,
                "distance_m": distance_rows.get(f"{m:02d}", 0.0),
                "visit_count": visit_rows.get(f"{m:02d}", 0),
            }
            for m in range(1, 13)
        ]
    }


@router.get("/api/insights/breakdown")
def get_insights_breakdown(session: Session = Depends(db_dependency)):
    """All-time (not month-scoped) travel-by-mode and visits-by-category
    totals, for Breakdown's ranked bar lists - reuses the exact same
    aggregation helpers the month view already uses, just called across the
    whole of history (start_ts=0) instead of one month's bounds."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    travel = _travel_totals(session, 0, now_ts)
    visits = _visit_totals(session, 0, now_ts)
    return {
        "travel": {mode: {"distance_m": t["distance_m"], "duration_s": t["duration_s"]} for mode, t in travel.items()},
        "visits": {category: {"duration_s": duration_s} for category, duration_s in visits.items()},
    }


# Excluded from "top category away from home" - Home trivially dominates
# every real dataset (it's wherever the user sleeps most nights), Streets
# and roads is a geocoding artefact (a resolved point with no real venue),
# and Other places is definitionally the bucket for "couldn't tell you
# anything more specific" - none of these make an interesting "did you know"
# sentence, unlike a genuine venue category (Food and drink, Culture, etc).
STORY_CATEGORY_EXCLUDE = {"Home", "Streets and roads", "Other places"}
# Commonly cited figure (193 UN member states + 2 permanent observers) - a
# defensible, widely-used denominator for "% of the world's countries",
# not a precise legal count (that number is itself disputed depending on
# whether e.g. Taiwan/Kosovo/Vatican are counted).
WORLD_COUNTRY_COUNT = 195
MOON_DISTANCE_M = 384_400_000.0


@router.get("/api/insights/stories")
def get_insights_stories(session: Session = Depends(db_dependency)):
    """"Did you know" narrative insights, computed fresh from real numbers -
    the Overview subtab's rotating hero card. Deliberately returns
    structured data per story (a type + whatever raw numbers that type
    needs), not a pre-composed sentence - the frontend already owns every
    number-formatting convention this app uses (formatMiles, formatDuration,
    monthName), and duplicating that formatting logic in Python here would
    be a second, driftable source of truth for the exact same numbers
    already shown elsewhere on the same page.

    Every story is only included if its own underlying data is real and
    non-trivial (never a "0 mi" or "None" story) - the same "no insight
    beats a wrong-looking one" principle already applied throughout this
    app (see e.g. images.py's own docstring)."""
    highlights = get_insights_highlights(session)
    yearly = get_insights_yearly(session)
    seasonality = get_insights_seasonality(session)
    breakdown = get_insights_breakdown(session)

    stories: list[dict] = []

    if highlights["total_distance_m"] > 0:
        stories.append({"type": "circumference", "total_distance_m": highlights["total_distance_m"]})

    if highlights["most_visited_place"]:
        stories.append({"type": "most_visited", **highlights["most_visited_place"]})

    if highlights["longest_trip"]:
        stories.append({"type": "longest_trip", **highlights["longest_trip"]})

    if highlights["busiest_day"]:
        stories.append({"type": "busiest_day", **highlights["busiest_day"]})

    if yearly["years"]:
        peak_year = max(yearly["years"], key=lambda y: y["distance_m"])
        if peak_year["distance_m"] > 0:
            stories.append({"type": "peak_year", "year": peak_year["year"], "distance_m": peak_year["distance_m"]})

    peak_month = max(seasonality["months"], key=lambda m: m["distance_m"])
    if peak_month["distance_m"] > 0:
        stories.append({"type": "peak_month", "month": peak_month["month"]})

    if highlights["longest_gap_days"] is not None and highlights["longest_gap_days"] > 0:
        stories.append({"type": "longest_gap", "days": highlights["longest_gap_days"]})

    if highlights["total_countries"] > 0:
        stories.append({
            "type": "country_count",
            "count": highlights["total_countries"],
            "percent_of_world": round(highlights["total_countries"] / WORLD_COUNTRY_COUNT * 100),
        })

    first_visit_per_country = (
        session.query(Place.country_code, Place.country, func.min(Visit.start_ts).label("first_ts"))
        .join(Visit, Visit.place_id == Place.id)
        .filter(Place.country_code.isnot(None))
        .group_by(Place.country_code)
        .all()
    )
    if first_visit_per_country:
        newest = max(first_visit_per_country, key=lambda r: r.first_ts)
        stories.append({
            "type": "newest_country",
            "country": country_name_en(newest.country_code, newest.country),
            "country_code": newest.country_code,
            "year": datetime.fromtimestamp(newest.first_ts, tz=timezone.utc).year,
        })

    top_category = max(
        ((c, v["duration_s"]) for c, v in breakdown["visits"].items() if c not in STORY_CATEGORY_EXCLUDE),
        key=lambda x: x[1],
        default=None,
    )
    if top_category and top_category[1] > 0:
        stories.append({"type": "top_category", "category": top_category[0], "duration_s": top_category[1]})

    flying = breakdown["travel"].get("flying")
    if flying and flying["distance_m"] > MOON_DISTANCE_M:
        stories.append({"type": "moon_trips", "flying_m": flying["distance_m"]})

    trip_count = session.query(func.count(Trip.id)).scalar() or 0
    if trip_count > 0 and highlights["avg_trip_days"]:
        stories.append({"type": "trip_frequency", "trip_count": trip_count, "avg_trip_days": highlights["avg_trip_days"]})

    return {"stories": stories}


@router.get("/api/insights/heatmap/{year}")
def get_insights_heatmap(year: int, session: Session = Depends(db_dependency)):
    """Daily visit counts for a calendar-heatmap (GitHub-contributions
    style) of one year - every day of the year is returned, zero-filled,
    same reasoning as the Day view's history chart: an evenly-spaced
    timeline needs explicit zeros, not gaps silently skipped."""
    start_ts = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
    day_expr = func.strftime("%Y-%m-%d", Visit.start_ts, "unixepoch")
    rows = (
        session.query(day_expr.label("day"), func.count(Visit.id))
        .filter(Visit.start_ts >= start_ts, Visit.start_ts < end_ts)
        .group_by("day")
        .all()
    )
    counts = dict(rows)

    days = []
    d = date(year, 1, 1)
    one_day = timedelta(days=1)
    last_day = date(year, 12, 31)
    while d <= last_day:
        key = d.isoformat()
        days.append({"date": key, "visit_count": counts.get(key, 0)})
        d += one_day
    return {"year": year, "days": days}


@router.get("/api/insights/{year}/{month}")
def get_insights(year: int, month: int, session: Session = Depends(db_dependency)):
    start_ts, end_ts = _month_bounds(year, month)

    travel = _travel_totals(session, start_ts, end_ts)
    visits = _visit_totals(session, start_ts, end_ts)

    travel_trend: dict[str, list[float]] = defaultdict(lambda: [0.0] * TREND_MONTHS)
    visit_trend: dict[str, list[float]] = defaultdict(lambda: [0.0] * TREND_MONTHS)
    for i in range(TREND_MONTHS):
        y, m = _step_back(year, month, TREND_MONTHS - 1 - i)
        m_start, m_end = _month_bounds(y, m)
        for mode, t in _travel_totals(session, m_start, m_end).items():
            travel_trend[mode][i] = t["distance_m"]
        for category, duration_s in _visit_totals(session, m_start, m_end).items():
            visit_trend[category][i] = duration_s

    # A mode/category only gets a card this month if its *own* current-month
    # total would show as something other than "0 mi"/"0 min" - having shown
    # up somewhere in the 6-month trend isn't enough on its own (that's what
    # produced e.g. a "Bus: 0 mi" card most months for someone who took one
    # bus trip 4 months ago).
    modes_with_data = sorted(
        {m for m, t in travel.items() if t["distance_m"] >= TRAVEL_MIN_DISPLAY_M},
        key=lambda m: TRAVEL_MODE_ORDER.index(m) if m in TRAVEL_MODE_ORDER else len(TRAVEL_MODE_ORDER),
    )
    categories_with_data = sorted(
        {c for c, duration_s in visits.items() if duration_s >= VISIT_MIN_DISPLAY_S},
        key=lambda c: VISIT_CATEGORIES.index(c) if c in VISIT_CATEGORIES else len(VISIT_CATEGORIES),
    )

    return {
        "year": year,
        "month": month,
        "travel": {
            mode: {
                "distance_m": travel[mode]["distance_m"],
                "duration_s": travel[mode]["duration_s"],
                "trend": travel_trend[mode],
            }
            for mode in modes_with_data
        },
        "visits": {
            category: {"duration_s": visits[category], "trend": visit_trend[category]}
            for category in categories_with_data
        },
    }
