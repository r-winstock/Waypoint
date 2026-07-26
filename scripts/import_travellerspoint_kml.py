"""Imports trip waypoints from a Travellerspoint (travellerspoint.com) KML
export - much coarser than the Google Timeline import (one waypoint per stop,
not real GPS tracking) but with far greater historical coverage (Richard's
export goes back to 2000; Google Timeline only exists from ~2015).

Skips any trip whose date range already has Visit coverage from another
source (Google Timeline import or live OwnTracks) - the point of this
importer is filling gaps in older history, not duplicating what's already
there in better detail.

Each imported folder becomes its own Trip directly (source="kml_import"),
rather than letting processing.py's gap/radius heuristic rediscover trip
boundaries - that heuristic assumes continuous tracking where an "at home"
visit breaks up separate excursions, which sparse waypoint-only data doesn't
have, and two real trips just a couple of weeks apart merged into one
nonsensical multi-week "trip" before this was made explicit.

Each KML <Folder> is one trip; each <Placemark> with a <Point> is a waypoint
(Placemarks with only a <LineString> are the visual great-circle flight path
and are skipped entirely - not real travel data). Dates are only present on
some waypoints (typically not the first/last, which are just "home") - a
missing date is filled in from the nearest dated neighbour.

Mode between consecutive waypoints is a best-effort heuristic (KML gives no
speed/track data to classify from): "flying" if either place name mentions
an airport, or the two waypoints are further apart than would plausibly be
driven in a single day; "driving" otherwise. Nowhere near as precise as the
Google Timeline import's real classification - this is coarse trip-planning
data, not tracking data, and is treated accordingly.

Usage:
    python scripts/import_travellerspoint_kml.py "/path/to/trips.kml"
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.geocoding import resolve_place  # noqa: E402
from app.models import Trip, TripSegment, Visit  # noqa: E402
from app.processing import _rebuild_trips, haversine_m  # noqa: E402

KML_NS = "{http://www.opengis.net/kml/2.2}"
DATE_RE = re.compile(r"(\d{2}[A-Za-z]{3}\d{4})")
AIRPORT_RE = re.compile(r"airport|aeroporto|\([A-Z]{3}\)", re.IGNORECASE)

# Above this straight-line distance between two same-day waypoints, assume
# it was flown rather than driven - KML gives no way to know for sure.
FLYING_DISTANCE_KM = 300.0


def parse_folder(folder: ET.Element) -> tuple[str, list[tuple[str, float, float, datetime | None]]]:
    name_el = folder.find(f"{KML_NS}name")
    trip_name = (name_el.text or "").strip() if name_el is not None else "Unnamed trip"

    waypoints: list[tuple[str, float, float, datetime | None]] = []
    for pm in folder.findall(f"{KML_NS}Placemark"):
        point = pm.find(f"{KML_NS}Point")
        if point is None:
            continue  # the LineString-only placemark - visual flight arc, not data
        coords_el = point.find(f"{KML_NS}coordinates")
        if coords_el is None or not coords_el.text:
            continue
        lon, lat, *_ = (float(x) for x in coords_el.text.strip().split(","))

        pm_name_el = pm.find(f"{KML_NS}name")
        pm_name = " ".join((pm_name_el.text or "").split()) if pm_name_el is not None else ""

        desc_el = pm.find(f"{KML_NS}description")
        date = None
        if desc_el is not None and desc_el.text:
            m = DATE_RE.search(desc_el.text)
            if m:
                date = datetime.strptime(m.group(1), "%d%b%Y").replace(tzinfo=timezone.utc)

        waypoints.append((pm_name, lat, lon, date))

    # Fill in missing dates (typically the first/last "home" waypoints) from
    # the nearest dated neighbour - forward-fill then back-fill.
    last_known = None
    for i, (n, lat, lon, d) in enumerate(waypoints):
        if d is not None:
            last_known = d
        elif last_known is not None:
            waypoints[i] = (n, lat, lon, last_known)
    next_known = None
    for i in range(len(waypoints) - 1, -1, -1):
        n, lat, lon, d = waypoints[i]
        if d is not None:
            next_known = d
        elif next_known is not None:
            waypoints[i] = (n, lat, lon, next_known)

    return trip_name, waypoints


def infer_mode(name_a: str, name_b: str, distance_km: float) -> str:
    if AIRPORT_RE.search(name_a) or AIRPORT_RE.search(name_b) or distance_km > FLYING_DISTANCE_KM:
        return "flying"
    return "driving"


def _farthest_city_country(visits: list[Visit]) -> tuple[str | None, str | None, str | None]:
    """Picks the city of whichever visit is geographically farthest from the
    trip's first waypoint, rather than the duration-weighted mode used by
    _rebuild_trips' continuous-tracking trips. KML waypoints all get an equal
    placeholder duration (no real dwell data), and a Travellerspoint folder is
    structurally an out-and-back list - the near-home departure/transit point
    (e.g. "Daventry") appears on both the outbound and return leg, so it ties
    or beats the real destination under a count/duration-based pick. The
    actual destination is reliably the farthest point reached from the start."""
    if not visits:
        return None, None, None
    origin = visits[0]
    best = None
    best_distance = -1.0
    for visit in visits:
        if not (visit.place and visit.place.city):
            continue
        distance = haversine_m(origin.lat, origin.lon, visit.lat, visit.lon)
        if distance > best_distance:
            best_distance = distance
            best = visit
    if best is None:
        return None, None, None
    return best.place.city, best.place.country, best.place.country_code


def has_existing_coverage(session, start_ts: int, end_ts: int) -> bool:
    return (
        session.query(Visit)
        .filter(Visit.start_ts < end_ts, Visit.end_ts > start_ts)
        .first()
        is not None
    )


def run(kml_path: Path) -> None:
    init_db()
    tree = ET.parse(kml_path)
    root = tree.getroot()

    imported_trips = skipped_trips = imported_visits = imported_segments = 0

    with SessionLocal() as session:
        for folder in root.iter(f"{KML_NS}Folder"):
            trip_name, waypoints = parse_folder(folder)
            dated = [w for w in waypoints if w[3] is not None]
            if len(dated) < 2:
                print(f"  ! skipping {trip_name!r}: not enough dated waypoints")
                continue

            start_ts = int(min(w[3] for w in dated).timestamp())
            end_ts = int((max(w[3] for w in dated) + timedelta(days=1)).timestamp())

            if has_existing_coverage(session, start_ts, end_ts):
                skipped_trips += 1
                print(f"  - skipping {trip_name!r} ({dated[0][3].date()} - {dated[-1][3].date()}): already covered")
                continue

            visit_rows: list[tuple[int, str]] = []  # (visit.id, original KML waypoint name)
            for i, (name, lat, lon, date) in enumerate(waypoints):
                if date is None:
                    continue
                # Spread same-day waypoints a few minutes apart so ordering
                # stays well-defined even at day-level date resolution.
                # No real dwell-time data in KML (just a date, not a duration) -
                # a short placeholder rather than a plausible-looking but
                # fabricated one, so it barely dents Insights' time totals.
                ts = int(date.timestamp()) + i
                visit = Visit(start_ts=ts, end_ts=ts + 60, lat=lat, lon=lon, point_count=1, source="kml_import")
                session.add(visit)
                session.flush()
                place = resolve_place(session, lat, lon)
                if place is not None:
                    visit.place_id = place.id
                visit_rows.append((visit.id, name))
                imported_visits += 1

            visits = session.query(Visit).filter(Visit.id.in_([v for v, _ in visit_rows])).order_by(Visit.start_ts).all()

            # Trip boundaries come directly from this folder, not the
            # gap/radius heuristic in _rebuild_trips - that heuristic assumes
            # continuous tracking with "at home" visits breaking up separate
            # excursions, which sparse KML waypoints don't have. Two real
            # trips a couple of weeks apart were merging into one nonsensical
            # multi-week "trip" before this was made explicit per-folder.
            trip = Trip(start_ts=visits[0].start_ts, end_ts=visits[-1].end_ts, source="kml_import")
            trip.primary_city, trip.primary_country, trip.primary_country_code = _farthest_city_country(visits)
            session.add(trip)
            session.flush()
            for v in visits:
                v.trip_id = trip.id

            names_by_id = dict(visit_rows)
            for prev, nxt in zip(visits, visits[1:]):
                distance_km = haversine_m(prev.lat, prev.lon, nxt.lat, nxt.lon) / 1000.0
                if distance_km < 1:
                    continue  # same stop recorded twice - no real leg between them
                mode = infer_mode(names_by_id[prev.id], names_by_id[nxt.id], distance_km)
                session.add(
                    TripSegment(
                        start_ts=prev.start_ts,
                        end_ts=nxt.start_ts,
                        mode=mode,
                        distance_m=distance_km * 1000,
                        duration_s=max(nxt.start_ts - prev.start_ts, 1),
                        source="kml_import",
                    )
                )
                imported_segments += 1

            imported_trips += 1
            print(f"  + imported {trip_name!r} ({dated[0][3].date()} - {dated[-1][3].date()}), {len(waypoints)} waypoints")

        session.commit()
        print("Recomputing trips across all sources...")
        _rebuild_trips(session)
        session.commit()

    print(
        f"Done. {imported_trips} trips imported, {skipped_trips} skipped as already covered, "
        f"{imported_visits} visits, {imported_segments} segments."
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-travellerspoint-export.kml>")
        sys.exit(1)
    run(Path(sys.argv[1]))
