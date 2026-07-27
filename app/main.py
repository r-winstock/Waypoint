from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import cities, day, events, images, insights, places, routing, settings, trips, world
from app.db import get_session, init_db
from app.ingest import router as ingest_router
from app.processing import process_all
from app.scheduler import start_scheduler, stop_scheduler
from app.version import __version__

STATIC_DIR = Path(__file__).parent / "static"

# Tied to process start time rather than __version__ - every restart (any
# deploy, any dev reload) busts cached static assets this way, not just a
# version bump specifically. See the no_cache_static middleware below for
# why a cache-buster is needed at all.
_CACHE_BUST = str(int(time.time()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Waypoint", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Browsers will heuristically cache a static file for a while even with
    no explicit Cache-Control header at all, confirmed live: editing app.js/
    index.html had no visible effect in an already-open tab, and a plain
    reload wasn't enough to fetch the new version either - only a hard,
    cache-bypassing reload was. no-cache (not no-store) still lets the
    browser revalidate cheaply via the ETag/Last-Modified StaticFiles/
    FileResponse already send, so this doesn't turn every load into a full
    re-download - it just stops a stale copy being used without even asking."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(ingest_router)
app.include_router(events.router)
app.include_router(images.router)
app.include_router(routing.router)
app.include_router(day.router)
app.include_router(trips.router)
app.include_router(insights.router)
app.include_router(places.router)
app.include_router(cities.router)
app.include_router(world.router)
app.include_router(settings.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": __version__}


@app.post("/api/process")
def trigger_processing():
    """Runs the visit/trip-segment/trip rebuild immediately, instead of
    waiting for the next scheduler tick. Handy after a bulk ingest, and for
    an eventual manual 'refresh' button in the UI."""
    with get_session() as session:
        process_all(session)
    return {"status": "ok"}


@app.get("/")
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__CACHE_BUST__", _CACHE_BUST)
    return HTMLResponse(html)
