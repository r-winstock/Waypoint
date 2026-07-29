from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class LocationPoint(Base):
    """Raw OwnTracks pings, one row per HTTP location report - plus, since
    source was added, backfilled historical points recovered from a Google
    Timeline export's rawSignals (see scripts/import_raw_signals.py). The
    stay-point/trip-segment rebuild only ever clusters source="owntracks"
    rows (see processing.py) - a backfilled point exists purely so the Day
    map has a real GPS trace to draw for imported history, never to be
    reclassified into a competing set of Visits/TripSegments alongside the
    ones Google's own semanticSegments already produced for that same time
    range."""

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
    source: Mapped[str] = mapped_column(String(16), default="owntracks", index=True)


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
    # The place/city's own name in its local language, when Nominatim's
    # English (Accept-Language: en) response differs from it - e.g. name is
    # "Milan", name_local is "Milano". Only ever populated when it's a real,
    # different string worth showing as a subtitle - never just a duplicate
    # of name/city.
    name_local: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city_local: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    # Human-given trip name (e.g. "New Zealand 2004"), sourced from a
    # Travellerspoint KML export's own folder name - only ever set for
    # source="kml_import" trips (see scripts/import_travellerspoint_kml.py).
    # Preferred as the display label over primary_city/primary_country when
    # present: those are a geometric best-guess at the "real" destination
    # among a trip's waypoints, which turned out unreliable in practice (a
    # connecting airport can measure marginally farther from home than the
    # actual destination) - a name the trip's own creator chose doesn't have
    # that failure mode. Still kept alongside primary_city/primary_country
    # rather than replacing them, since a country/city pair remains a much
    # better Wikipedia search term for card photos than "New Zealand 2004".
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
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


class CachedImage(Base):
    """A representative photo for a card background (Trips/Places/Cities),
    fetched once from Wikipedia and kept locally rather than re-fetched on
    every page load. Keyed by a slugified search term (city/place/country
    name), not by a foreign key to Place/Trip/City - the same city name is
    shared by many places/trips, so one row covers all of them.

    found=False with image_path=None is itself a cached result (Wikipedia
    had nothing for this query) - without it, every card for a place with
    no findable photo would re-query Wikipedia on every single page load.
    Only an explicit refresh re-attempts it.
    """

    __tablename__ = "cached_images"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    query: Mapped[str] = mapped_column(String(255))
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    found: Mapped[bool] = mapped_column(default=False)
    fetched_at: Mapped[int] = mapped_column(Integer)


class CachedRoute(Base):
    """A snapped rail path for a single train/subway/tram TripSegment,
    computed via Overpass + a small self-built graph search - see
    app/routing.py for why: OSRM (used for driving/walking/cycling) has no
    free public equivalent for rail routing. Keyed by segment_id, not a
    slugified query like CachedImage, since a segment's endpoints are unique
    to it - there's no shared-across-many-rows case the way one city name
    covers many places. found=False (points_json=None) is itself a cached
    result (no rail network found nearby, or the two visits don't snap
    close enough to one) so a segment that genuinely has no rail path
    doesn't re-query Overpass on every day view."""

    __tablename__ = "cached_routes"

    segment_id: Mapped[int] = mapped_column(ForeignKey("trip_segments.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    points_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    found: Mapped[bool] = mapped_column(default=False)
    fetched_at: Mapped[int] = mapped_column(Integer)
