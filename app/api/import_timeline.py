from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from sqlalchemy import func

from app.db import DATA_DIR, SessionLocal
from app.google_timeline_import import run as run_import
from app.models import TripSegment, Visit

router = APIRouter()

_IMPORT_TMP_DIR = DATA_DIR / "import_tmp"
_IMPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)

# A single global job, not a queue - this is a one-user app and only one
# Timeline export ever needs importing at a time. Guarded by a lock since the
# upload endpoint (request thread) and the import worker (background thread)
# both touch it.
_lock = threading.Lock()
_state: dict = {"status": "idle", "filename": None, "since_ts": None, "started_ts": None, "progress": None, "result": None, "error": None}


def _compute_since_ts() -> int | None:
    """Auto-detects the safe import cutoff - the same "find the latest
    covered timestamp" reasoning previously done by hand before every manual
    import, now automatic so a fresh export can just be dropped in without
    figuring out a precise cutoff first. None (import everything) only for a
    genuinely empty database."""
    session = SessionLocal()
    try:
        max_visit = session.query(func.max(Visit.end_ts)).scalar()
        max_segment = session.query(func.max(TripSegment.end_ts)).scalar()
        candidates = [t for t in (max_visit, max_segment) if t is not None]
        return max(candidates) if candidates else None
    finally:
        session.close()


def _run_in_background(path: Path) -> None:
    def on_progress(visits: int, segments: int, skipped: int) -> None:
        with _lock:
            _state["progress"] = {"visits": visits, "segments": segments, "skipped": skipped}

    try:
        result = run_import(path, since_ts=_state["since_ts"], progress_callback=on_progress)
        with _lock:
            _state["status"] = "done"
            _state["result"] = result
    except Exception as e:
        with _lock:
            _state["status"] = "error"
            _state["error"] = str(e)
    finally:
        path.unlink(missing_ok=True)


@router.post("/api/import/google-timeline")
def start_import(file: UploadFile):
    """Accepts a raw Google Timeline export upload (the on-device export,
    Settings -> Location -> Timeline -> Export Timeline data - not a Takeout
    archive) and imports whatever's genuinely new since the last import, with
    no manual cutoff-finding required. Replaces the previous workflow of
    scp-ing a 150-200MB file to the server and running a script by hand.

    Runs in a background thread rather than blocking this request: a fresh
    export can take several minutes to stream through (ijson walks every
    semanticSegments entry even though since_ts skips most of them - only
    the huge rawSignals array is skipped outright) plus Nominatim/Google
    Places geocoding calls for any newly-seen places, well past what's safe
    to hold a single HTTP request open for. Poll /api/import/google-
    timeline/status for progress and the final result."""
    with _lock:
        if _state["status"] == "running":
            raise HTTPException(status_code=409, detail="An import is already running")
        since_ts = _compute_since_ts()
        _state.update(
            {
                "status": "running",
                "filename": file.filename,
                "since_ts": since_ts,
                "started_ts": time.time(),
                "progress": None,
                "result": None,
                "error": None,
            }
        )

    dest = _IMPORT_TMP_DIR / f"upload-{int(time.time())}.json"
    with open(dest, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            out.write(chunk)

    thread = threading.Thread(target=_run_in_background, args=(dest,), daemon=True)
    thread.start()
    return {"status": "started", "since_ts": since_ts}


@router.get("/api/import/google-timeline/status")
def get_import_status():
    with _lock:
        return dict(_state)
