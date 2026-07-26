from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import cities, day, insights, places, settings, trips, world
from app.db import get_session, init_db
from app.ingest import router as ingest_router
from app.processing import process_all
from app.scheduler import start_scheduler, stop_scheduler
from app.version import __version__

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Waypoint", version=__version__, lifespan=lifespan)

app.include_router(ingest_router)
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
    return FileResponse(STATIC_DIR / "index.html")
