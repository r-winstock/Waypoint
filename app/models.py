from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class LocationPoint(Base):
    """Raw OwnTracks pings, one row per HTTP location report."""

    __tablename__ = "location_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tid: Mapped[str] = mapped_column(String(8))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    alt: Mapped[float | None] = mapped_column(Float, nullable=True)
    acc: Mapped[float | None] = mapped_column(Float, nullable=True)
    vel: Mapped[float | None] = mapped_column(Float, nullable=True)
    batt: Mapped[float | None] = mapped_column(Float, nullable=True)
    tst: Mapped[int] = mapped_column(Integer, index=True)  # unix seconds


class Place(Base):
    """Reverse-geocode cache, keyed by rounded coordinates."""

    __tablename__ = "places"
    __table_args__ = (UniqueConstraint("lat_round", "lon_round", name="uq_place_coord"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lat_round: Mapped[float] = mapped_column(Float, index=True)
    lon_round: Mapped[float] = mapped_column(Float, index=True)
    # Google Timeline import's placeId - a precise, stable identifier, so it's
    # checked before falling back to coordinate rounding (which could in
    # theory collide two distinct nearby places into one cache entry).
    google_place_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(String(64), default="Other places")
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True once a user has corrected this place via /api/places/{id} - purely
    # informational (the UI badges it), since normal cache lookups never
    # overwrite an existing Place's fields regardless of this flag.
    manually_corrected: Mapped[bool] = mapped_column(default=False)

    visits: Mapped[list["Visit"]] = relationship(back_populates="place")


class Visit(Base):
    """A stay-point: a cluster of raw pings that stayed within a small radius."""

    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_ts: Mapped[int] = mapped_column(Integer, index=True)
    end_ts: Mapped[int] = mapped_column(Integer, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    point_count: Mapped[int] = mapped_column(Integer, default=0)
    # "owntracks" (rebuilt from location_points on every scheduler tick) or
    # "google_import" (written once, never touched by the rebuild - see
    # processing.py's _rebuild_visits, which only deletes source="owntracks").
    source: Mapped[str] = mapped_column(String(16), default="owntracks", index=True)

    place_id: Mapped[int | None] = mapped_column(ForeignKey("places.id"), nullable=True)
    place: Mapped[Place | None] = relationship(back_populates="visits")

    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), nullable=True, index=True)
    trip: Mapped["Trip | None"] = relationship(back_populates="visits")


class TripSegment(Base):
    """The travel leg between two consecutive visits."""

    __tablename__ = "trip_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_ts: Mapped[int] = mapped_column(Integer, index=True)
    end_ts: Mapped[int] = mapped_column(Integer, index=True)
    # walking | cycling | driving | taxi | bus | train | subway | tram | ferry | flying
    mode: Mapped[str] = mapped_column(String(16))
    distance_m: Mapped[float] = mapped_column(Float)
    duration_s: Mapped[float] = mapped_column(Float)
    # see Visit.source - same reasoning, same rebuild-safety requirement.
    source: Mapped[str] = mapped_column(String(16), default="owntracks", index=True)

    start_visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True)
    end_visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True)


class Trip(Base):
    """A multi-day (or single away-stretch) grouping of visits away from home."""

    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_ts: Mapped[int] = mapped_column(Integer, index=True)
    end_ts: Mapped[int] = mapped_column(Integer, index=True)
    primary_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_country_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # "computed" (the gap/radius heuristic in _rebuild_trips, rebuilt every
    # time it runs) or "kml_import" (trip boundaries taken directly from the
    # source file's own folder structure, which is more reliable than any
    # heuristic - see scripts/import_travellerspoint_kml.py). _rebuild_trips
    # only ever deletes/recomputes source="computed" rows.
    source: Mapped[str] = mapped_column(String(16), default="computed")

    visits: Mapped[list[Visit]] = relationship(back_populates="trip", order_by="Visit.start_ts")


class Settings(Base):
    """Simple key/value store: home location, OwnTracks ingest credentials."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
