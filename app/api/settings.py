from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import db_dependency, get_setting, set_setting
from app.models import HomePeriod
from app.processing import _rebuild_trips

router = APIRouter()


class SettingsUpdate(BaseModel):
    owntracks_username: str | None = None
    owntracks_password: str | None = None
    # ISO date (YYYY-MM-DD), optional - powers Insights' "% of life spent
    # travelling" stat. No UI to set this yet (this app has no Settings page
    # at all - home periods are equally curl-only, see /api/settings/home-
    # periods), so Insights itself shows the exact curl command to set it
    # when this is empty.
    birth_date: str | None = None


@router.get("/api/settings")
def get_settings(session: Session = Depends(db_dependency)):
    return {
        "owntracks_username": get_setting(session, "owntracks_username", "waypoint"),
        "birth_date": get_setting(session, "birth_date", "") or None,
    }


@router.put("/api/settings")
def update_settings(update: SettingsUpdate, session: Session = Depends(db_dependency)):
    if update.owntracks_username is not None:
        set_setting(session, "owntracks_username", update.owntracks_username)
    if update.owntracks_password is not None:
        set_setting(session, "owntracks_password", update.owntracks_password)
    if update.birth_date is not None:
        set_setting(session, "birth_date", update.birth_date)
    session.commit()
    return get_settings(session)


def _parse_date(s: str | None) -> int | None:
    if not s:
        return None
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _format_date(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _serialize(period: HomePeriod) -> dict:
    return {
        "id": period.id,
        "start_date": _format_date(period.start_ts),
        "end_date": _format_date(period.end_ts),
        "lat": period.lat,
        "lon": period.lon,
        "radius_m": period.radius_m,
        "label": period.label,
    }


class HomePeriodCreate(BaseModel):
    lat: float
    lon: float
    radius_m: float = 500.0
    # ISO date (YYYY-MM-DD) - None means "no known bound that direction"
    # (the earliest home on record, or the current one).
    start_date: str | None = None
    end_date: str | None = None
    label: str | None = None


@router.get("/api/settings/home-periods")
def list_home_periods(session: Session = Depends(db_dependency)):
    periods = session.query(HomePeriod).order_by(HomePeriod.start_ts).all()
    return {"home_periods": [_serialize(p) for p in periods]}


@router.post("/api/settings/home-periods")
def create_home_period(body: HomePeriodCreate, session: Session = Depends(db_dependency)):
    period = HomePeriod(
        start_ts=_parse_date(body.start_date),
        end_ts=_parse_date(body.end_date),
        lat=body.lat,
        lon=body.lon,
        radius_m=body.radius_m,
        label=body.label,
    )
    session.add(period)
    session.flush()
    # Every existing visit's "at home"/"away" classification can shift once
    # a new period is added (e.g. filling in a previously-uncovered era) -
    # rebuild immediately rather than leaving trips stale until the next
    # unrelated OwnTracks ping happens to trigger it.
    _rebuild_trips(session)
    session.commit()
    return _serialize(period)


@router.delete("/api/settings/home-periods/{period_id}")
def delete_home_period(period_id: int, session: Session = Depends(db_dependency)):
    period = session.get(HomePeriod, period_id)
    if period is None:
        raise HTTPException(status_code=404, detail="Home period not found")
    session.delete(period)
    session.flush()
    _rebuild_trips(session)
    session.commit()
    return {"deleted": True}
