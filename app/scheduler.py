from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import get_session
from app.processing import process_all

logger = logging.getLogger("waypoint.scheduler")

PROCESS_INTERVAL_SECONDS = 120

_scheduler = BackgroundScheduler()


def _run_processing() -> None:
    try:
        with get_session() as session:
            process_all(session)
    except Exception:
        logger.exception("Processing job failed")


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
