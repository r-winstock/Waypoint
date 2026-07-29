from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.country_names import country_name_en
from app.db import db_dependency, get_setting
from app.models import Place, Trip, TripSegment, Visit

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
        session.query(Trip.id, Trip.primary_city, Trip.primary_country, Trip.start_ts, Trip.end_ts)
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
