from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import db_dependency, get_setting, set_setting

router = APIRouter()


class SettingsUpdate(BaseModel):
    home_lat: float | None = None
    home_lon: float | None = None
    home_radius_m: float | None = None
    owntracks_username: str | None = None
    owntracks_password: str | None = None


@router.get("/api/settings")
def get_settings(session: Session = Depends(db_dependency)):
    home_lat = get_setting(session, "home_lat", "")
    home_lon = get_setting(session, "home_lon", "")
    return {
        "home_lat": float(home_lat) if home_lat else None,
        "home_lon": float(home_lon) if home_lon else None,
        "home_radius_m": float(get_setting(session, "home_radius_m", "500")),
        "owntracks_username": get_setting(session, "owntracks_username", "waypoint"),
    }


@router.put("/api/settings")
def update_settings(update: SettingsUpdate, session: Session = Depends(db_dependency)):
    if update.home_lat is not None:
        set_setting(session, "home_lat", str(update.home_lat))
    if update.home_lon is not None:
        set_setting(session, "home_lon", str(update.home_lon))
    if update.home_radius_m is not None:
        set_setting(session, "home_radius_m", str(update.home_radius_m))
    if update.owntracks_username is not None:
        set_setting(session, "owntracks_username", update.owntracks_username)
    if update.owntracks_password is not None:
        set_setting(session, "owntracks_password", update.owntracks_password)
    session.commit()
    return get_settings(session)
