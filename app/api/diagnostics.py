from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import DB_PATH, db_dependency, get_setting
from app.models import LocationPoint, Place, Trip, TripSegment, Visit
from app.version import __version__

router = APIRouter()

# Tied to process start, same convention main.py's own _CACHE_BUST already
# uses - every restart (deploy, crash, manual recreate) resets this, which is
# exactly the point: uptime here means "how long has *this* process been
# running", the same question you'd ask first when tracking data goes stale.
PROCESS_START_TS = time.time()

# In-memory ring buffer of recent WARNING-and-up log records from anywhere in
# the app (the scheduler's own logger.exception included) - the container's
# stdout logs aren't readable from inside the process itself, and a proper
# log file (rotation, a bind mount) is more plumbing than a personal-use
# diagnostics page needs. Resets on restart, same as PROCESS_START_TS - this
# answers "what's going wrong right now", not "show me a permanent audit
# trail", so losing history across a restart is an acceptable trade.
_LOG_BUFFER_SIZE = 50
_recent_logs: deque[dict] = deque(maxlen=_LOG_BUFFER_SIZE)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _recent_logs.appendleft(
            {
                "ts": int(record.created),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
        )


def install_log_buffer() -> None:
    """Attached to the root logger once at app startup (see main.py's
    lifespan) - catches WARNING+ records from every module, not just this
    one, so a scheduler failure or an unhandled exception anywhere shows up
    here without each module needing its own wiring."""
    handler = _BufferHandler(level=logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)


@router.get("/api/diagnostics")
def get_diagnostics(session: Session = Depends(db_dependency)):
    """Everything needed to answer "is tracking actually working right now,
    and if not, since when" without needing SSH access to the host - built
    directly out of a real live incident (a silent server-side ingest
    failure that went unnoticed for almost 12 hours overnight, only found by
    manually comparing timestamps the next morning)."""
    now_ts = int(datetime.now(timezone.utc).timestamp())

    latest_point = session.query(LocationPoint).order_by(LocationPoint.tst.desc()).first()
    hour_ago, day_ago = now_ts - 3600, now_ts - 86400
    points_last_hour = session.query(func.count(LocationPoint.id)).filter(LocationPoint.tst >= hour_ago).scalar() or 0
    points_last_day = session.query(func.count(LocationPoint.id)).filter(LocationPoint.tst >= day_ago).scalar() or 0

    last_run_str = get_setting(session, "last_scheduler_run_ts", "")
    last_ok_str = get_setting(session, "last_scheduler_ok", "")
    last_error = get_setting(session, "last_scheduler_error", "")

    db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else None

    return {
        "version": __version__,
        "now_ts": now_ts,
        "process_started_ts": int(PROCESS_START_TS),
        "uptime_s": int(time.time() - PROCESS_START_TS),
        "last_point": (
            {
                "tst": latest_point.tst,
                "lat": latest_point.lat,
                "lon": latest_point.lon,
                "acc": latest_point.acc,
                "batt": latest_point.batt,
                "source": latest_point.source,
                "age_s": now_ts - latest_point.tst,
            }
            if latest_point
            else None
        ),
        "points_last_hour": points_last_hour,
        "points_last_day": points_last_day,
        "scheduler": {
            "last_run_ts": int(last_run_str) if last_run_str else None,
            "last_run_age_s": (now_ts - int(last_run_str)) if last_run_str else None,
            "last_ok": last_ok_str == "1" if last_ok_str else None,
            "last_error": last_error or None,
        },
        "db_size_bytes": db_size_bytes,
        "counts": {
            "location_points": session.query(func.count(LocationPoint.id)).scalar() or 0,
            "visits": session.query(func.count(Visit.id)).scalar() or 0,
            "trip_segments": session.query(func.count(TripSegment.id)).scalar() or 0,
            "trips": session.query(func.count(Trip.id)).scalar() or 0,
            "places": session.query(func.count(Place.id)).scalar() or 0,
        },
        "recent_logs": list(_recent_logs),
    }
