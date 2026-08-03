from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import get_session, set_setting
from app.processing import process_all

logger = logging.getLogger("waypoint.scheduler")

PROCESS_INTERVAL_SECONDS = 120

_scheduler = BackgroundScheduler()


def _run_processing() -> None:
    # Recorded in Settings (not just left as an in-memory value) so the
    # diagnostics page can show "last successful run" across a restart too -
    # a scheduler that silently stopped ticking hours before a container was
    # even restarted is exactly the kind of gap that's otherwise invisible
    # until someone notices missing data the next morning.
    now_ts = int(datetime.now(timezone.utc).timestamp())
    try:
        with get_session() as session:
            process_all(session)
            set_setting(session, "last_scheduler_run_ts", str(now_ts))
            set_setting(session, "last_scheduler_ok", "1")
            set_setting(session, "last_scheduler_error", "")
            session.commit()
    except Exception as e:
        logger.exception("Processing job failed")
        try:
            with get_session() as session:
                set_setting(session, "last_scheduler_run_ts", str(now_ts))
                set_setting(session, "last_scheduler_ok", "0")
                set_setting(session, "last_scheduler_error", f"{type(e).__name__}: {e}"[:500])
                session.commit()
        except Exception:
            logger.exception("Failed to record scheduler failure in Settings")


def start_scheduler() -> BackgroundScheduler:
    _scheduler.add_job(
        _run_processing,
        "interval",
        seconds=PROCESS_INTERVAL_SECONDS,
        id="process_points",
        next_run_time=None,  # first run is triggered explicitly below
    )
    _scheduler.start()
    _scheduler.modify_job("process_points", next_run_time=datetime.now())
    return _scheduler


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
