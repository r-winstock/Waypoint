from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.db import get_session, get_setting
from app.models import LocationPoint

router = APIRouter()
security = HTTPBasic()


def check_owntracks_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    with get_session() as session:
        expected_user = get_setting(session, "owntracks_username", "waypoint")
        expected_pass = get_setting(session, "owntracks_password", "waypoint")

    user_ok = secrets.compare_digest(credentials.username, expected_user)
    pass_ok = secrets.compare_digest(credentials.password, expected_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OwnTracks credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.post("/api/owntracks")
async def owntracks_ingest(payload: dict, _auth: None = Depends(check_owntracks_auth)):
    """Receives OwnTracks HTTP-mode reports. Only '_type': 'location' pings are
    stored; other message types (transition, waypoints, lwt) are accepted and
    ignored so OwnTracks doesn't treat them as delivery failures."""

    if payload.get("_type") != "location":
        return []

    with get_session() as session:
        session.add(
            LocationPoint(
                tid=str(payload.get("tid", "??"))[:8],
                lat=float(payload["lat"]),
                lon=float(payload["lon"]),
                alt=payload.get("alt"),
                acc=payload.get("acc"),
                vel=payload.get("vel"),
                batt=payload.get("batt"),
                tst=int(payload["tst"]),
            )
        )
        session.commit()

    # OwnTracks expects a JSON array in response (normally other waypoints to
    # sync back to the device) - empty is a valid, well-formed reply.
    return []
