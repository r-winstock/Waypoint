"""Backfills real GPS points for imported history from a Google Timeline
export's rawSignals array - the raw GPS/WiFi/accelerometer log that
scripts/import_google_timeline.py deliberately skips (semanticSegments
already did the stay-point/activity classification that pipeline exists to
redo). Confirmed live this data is worth having anyway: a driving segment
built from just its two endpoint visits gets snapped by OSRM to whatever
route it judges shortest/fastest between them, which isn't always the road
actually taken (confirmed against Google's own Timeline app showing the
correct route for the same day, built from this exact signal data) - a real
GPS trace lets the Day map draw the recorded path directly instead of
guessing.

Only rawSignals.position entries are used (real lat/lon/timestamp, the
same shape OwnTracks pings already are) - activityRecord and wifiScan
entries carry no coordinate of their own and are skipped.

Written with source="google_raw", never source="owntracks" - see
LocationPoint's docstring in app/models.py for why that distinction is
load-bearing: app/processing.py's stay-point rebuild only ever clusters
source="owntracks" rows, so these backfilled points are inert for
Visit/TripSegment classification and exist purely for the Day map's raw-
GPS-trace rendering (app/api/day.py's points query has no source filter,
by design).

Usage:
    python scripts/import_raw_signals.py "/path/to/Timeline (....json)"

Safe to re-run - deletes any existing source="google_raw" rows first, so
re-running against an updated export replaces rather than duplicates them.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import LocationPoint  # noqa: E402

COMMIT_EVERY = 5000


def parse_ts(iso_str: str) -> int:
    return int(datetime.fromisoformat(iso_str).timestamp())


def parse_latlng(s: str) -> tuple[float, float]:
    lat_str, lon_str = s.split(",")
    return float(lat_str.strip().rstrip("°")), float(lon_str.strip().rstrip("°"))


def run(export_path: str) -> None:
    init_db()
    with SessionLocal() as session:
        deleted = session.query(LocationPoint).filter(LocationPoint.source == "google_raw").delete()
        session.commit()
        if deleted:
            print(f"Removed {deleted} existing google_raw points before re-importing.")

        imported = skipped = 0
        with open(export_path, "rb") as f:
            for item in ijson.items(f, "rawSignals.item"):
                position = item.get("position")
                if not position or "LatLng" not in position or "timestamp" not in position:
                    skipped += 1
                    continue
                try:
                    lat, lon = parse_latlng(position["LatLng"])
                    tst = parse_ts(position["timestamp"])
                except (ValueError, KeyError):
                    skipped += 1
                    continue

                session.add(
                    LocationPoint(
                        tid="goog",
                        lat=lat,
                        lon=lon,
                        alt=float(position["altitudeMeters"]) if "altitudeMeters" in position else None,
                        acc=float(position["accuracyMeters"]) if "accuracyMeters" in position else None,
                        vel=float(position["speedMetersPerSecond"]) if "speedMetersPerSecond" in position else None,
                        tst=tst,
                        source="google_raw",
                    )
                )
                imported += 1
                if imported % COMMIT_EVERY == 0:
                    session.commit()
                    print(f"...{imported} imported so far")

        session.commit()
        print(f"\nDone. Imported {imported} raw GPS points, skipped {skipped} non-position signals.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/import_raw_signals.py \"/path/to/Timeline (....json)\"")
        sys.exit(1)
    run(sys.argv[1])
