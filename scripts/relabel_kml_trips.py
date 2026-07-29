"""Backfills Trip.name onto already-imported kml_import trips.

import_travellerspoint_kml.py already parses each KML <Folder>'s own name
(e.g. "New Zealand 2004") via parse_folder(), but until now only ever used
it for log messages - it was never persisted to the Trip row. That name is
a far more reliable trip label than primary_city/primary_country (a
geometric best-guess at the "real" destination among a trip's waypoints,
which picks the wrong place whenever a connecting airport happens to measure
marginally farther from home than the actual destination - confirmed live
for a Nassau layover and Auckland Airport's own suburb). A name the trip's
own creator chose doesn't have that failure mode at all.

Re-parses the same KML export used for the original import and matches each
folder to its corresponding already-imported Trip by calendar date, not an
exact start_ts/end_ts match - the actual stored Trip.start_ts/end_ts come
from the first/last created Visit row's own timestamps (visits[0].start_ts,
visits[-1].end_ts in run()), which include a same-day ordering offset (each
same-day waypoint's timestamp is nudged a few seconds by its index within
the folder - see run()'s own comment on this). A trip's start_ts and the
folder's own earliest dated waypoint always fall on the same calendar day
regardless of that offset, and likewise for end_ts/the latest dated
waypoint - matching on date, not the literal second, is what actually lines
up with what's on disk.

Purely a label update - never touches start_ts/end_ts/visits - so it's safe
to run repeatedly (fully idempotent).

Usage:
    python scripts/relabel_kml_trips.py "/path/to/trips.kml" [--apply]

Without --apply, prints the plan only (dry run).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Trip  # noqa: E402
from import_travellerspoint_kml import KML_NS, clean_trip_name, parse_folder  # noqa: E402


def run(kml_path: Path, apply: bool) -> None:
    init_db()
    tree = ET.parse(kml_path)
    root = tree.getroot()

    with SessionLocal() as session:
        kml_trips = session.query(Trip).filter(Trip.source == "kml_import").all()

        checked = changed = unmatched = 0
        for folder in root.iter(f"{KML_NS}Folder"):
            trip_name, waypoints = parse_folder(folder)
            dated = [w for w in waypoints if w[3] is not None]
            if len(dated) < 2:
                continue
            start_date = min(w[3] for w in dated).date()
            end_date = max(w[3] for w in dated).date()

            trip = next(
                (
                    t
                    for t in kml_trips
                    if datetime.fromtimestamp(t.start_ts, tz=timezone.utc).date() == start_date
                    and datetime.fromtimestamp(t.end_ts, tz=timezone.utc).date() == end_date
                ),
                None,
            )
            checked += 1
            if trip is None:
                unmatched += 1
                print(f"  ! no matching trip found for {trip_name!r} ({start_date} - {end_date})")
                continue

            new_name = clean_trip_name(trip_name)
            if new_name == trip.name:
                continue
            changed += 1
            print(f"  trip {trip.id}: name {trip.name!r} -> {new_name!r}")
            if apply:
                trip.name = new_name

        if apply:
            session.commit()
            print(f"\nDone. Checked {checked} folders, updated {changed}, {unmatched} unmatched.")
        else:
            print(f"\nDry run - checked {checked} folders, {changed} would change, {unmatched} unmatched. Re-run with --apply to update.")


if __name__ == "__main__":
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not positional:
        print(f"Usage: {sys.argv[0]} <path-to-travellerspoint-export.kml> [--apply]")
        sys.exit(1)
    run(Path(positional[0]), apply="--apply" in sys.argv)
