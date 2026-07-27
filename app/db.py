from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

# Default resolves next to the app package (repo_root/data) regardless of the
# process's working directory; production sets WAYPOINT_DATA_DIR explicitly
# to the container's bind-mounted /app/data.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR = Path(os.environ.get("WAYPOINT_DATA_DIR", str(_DEFAULT_DATA_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "waypoint.db"

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

DEFAULT_SETTINGS = {
    # Home location used to decide what counts as "away" for trip grouping.
    # Unset (None) until the user configures it via /api/settings.
    "home_lat": "",
    "home_lon": "",
    "home_radius_m": "500",
    "owntracks_username": "waypoint",
    "owntracks_password": "waypoint",
}


# create_all only ever creates missing tables, never adds columns to a
# table that already exists - there's no migration framework here (a single
# personal-use SQLite file doesn't warrant one), so a column added to a
# model after the table already exists on disk needs an explicit ALTER
# TABLE, run once here at startup and skipped thereafter via PRAGMA
# table_info. Kept minimal/inline rather than a generic migration system -
# this is the second time it's been needed (google_place_id was added
# before the first real deploy, so never needed this path).
_PLACE_COLUMN_MIGRATIONS = {
    "name_local": "VARCHAR(255)",
    "city_local": "VARCHAR(128)",
}


def _migrate_place_columns() -> None:
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(places)").fetchall()}
        for column, col_type in _PLACE_COLUMN_MIGRATIONS.items():
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE places ADD COLUMN {column} {col_type}")
        conn.commit()


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_place_columns()
    with SessionLocal() as session:
        seed_default_settings(session)
        session.commit()


def seed_default_settings(session: Session) -> None:
    from app.models import Settings

    existing = {row.key for row in session.query(Settings.key).all()}
    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing:
            session.add(Settings(key=key, value=value))


def get_session() -> Session:
    return SessionLocal()


def db_dependency():
    """FastAPI dependency: yields a session, closes it after the request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_setting(session: Session, key: str, default: str = "") -> str:
    from app.models import Settings

    row = session.get(Settings, key)
    return row.value if row is not None else default


def set_setting(session: Session, key: str, value: str) -> None:
    from app.models import Settings

    row = session.get(Settings, key)
    if row is None:
        session.add(Settings(key=key, value=value))
    else:
        row.value = value
