from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.models import Place, TripSegment, Visit

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
