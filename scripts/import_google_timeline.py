"""Imports a Google Timeline export's semanticSegments directly into
Waypoint's Visit/TripSegment tables, bypassing /api/owntracks entirely -
Google's own export already did the stay-point clustering and activity
classification that pipeline exists to redo from raw pings.

Deliberately ignores the export's top-level `rawSignals` array (the raw GPS/
WiFi/accelerometer log that makes up most of the file's size) - streamed via
ijson so it's never even parsed, let alone loaded into memory.

Imported rows are written with source="google_import", which
app/processing.py's scheduler-driven rebuild never touches (it only ever
deletes/rebuilds source="owntracks" rows) - see app/models.py's Visit/
TripSegment.source comments for why that separation exists.

Usage:
    python scripts/import_google_timeline.py "/path/to/Timeline (....json)"

Run against a fresh/empty database - it does not de-duplicate against
existing rows, so running it twice against the same database double-imports
everything.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.geocoding import resolve_place  # noqa: E402
from app.models import TripSegment, Visit  # noqa: E402
from app.processing import _rebuild_trips  # noqa: E402

COMMIT_EVERY = 200

# Google's activity topCandidate.type -> our travel-mode taxonomy. Only
# merges true near-duplicates of the same mode (WALKING/ON_FOOT/RUNNING are
# all just "on foot" at different paces) - taxi, train, bus, subway, tram
# each stay distinct since they're meaningfully different trips to review.
MODE_MAP = {
    "WALKING": "walking",
    "ON_FOOT": "walking",
    "RUNNING": "walking",
    "ON_BICYCLE": "cycling",
    "IN_PASSENGER_VEHICLE": "driving",
    "IN_ROAD_VEHICLE": "driving",
    "IN_VEHICLE": "driving",
    "IN_TWO_WHEELER_VEHICLE": "driving",
    "IN_TAXI": "taxi",
    "IN_BUS": "bus",
    "IN_TRAIN": "train",
    "IN_RAIL_VEHICLE": "train",
    "IN_SUBWAY": "subway",
    "IN_TRAM": "tram",
    "IN_FERRY": "ferry",
    "BOATING": "boating",
    "FLYING": "flying",
    # Not real travel - no segment created: STILL, TILTING, UNKNOWN,
    # UNKNOWN_ACTIVITY_TYPE, EXITING_VEHICLE.
}


def parse_ts(iso_str: str) -> int:
    return int(datetime.fromisoformat(iso_str).timestamp())


def parse_latlng(s: str) -> tuple[float, float]:
    lat_str, lon_str = s.split(",")
    return float(lat_str.strip().rstrip("°")), float(lon_str.strip().rstrip("°"))


def import_visit(session, seg: dict) -> bool:
    top = seg.get("visit", {}).get("topCandidate")
    if not top or "placeLocation" not in top:
        return False
    lat, lon = parse_latlng(top["placeLocation"]["latLng"])
    visit = Visit(
        start_ts=parse_ts(seg["startTime"]),
        end_ts=parse_ts(seg["endTime"]),
        lat=lat,
        lon=lon,
        point_count=1,
        source="google_import",
    )
    session.add(visit)
    session.flush()
    place = resolve_place(session, lat, lon, google_place_id=top.get("placeId"))
    if place is not None:
        visit.place_id = place.id
    return True


def import_activity(session, seg: dict) -> bool:
    activity = seg.get("activity", {})
    top = activity.get("topCandidate", {})
    mode = MODE_MAP.get(top.get("type"))
    if mode is None:
        return False
    distance_m = activity.get("distanceMeters") or 0.0
    start_ts = parse_ts(seg["startTime"])
    end_ts = parse_ts(seg["endTime"])
    duration_s = end_ts - start_ts
    if duration_s <= 0:
        return False
    session.add(
        TripSegment(
            start_ts=start_ts,
            end_ts=end_ts,
            mode=mode,
            distance_m=distance_m,
            duration_s=duration_s,
            source="google_import",
        )
    )
    return True


def run(json_path: Path) -> None:
    init_db()
    session = SessionLocal()
    visits = segments = skipped = 0
    t0 = time.monotonic()

    try:
        with open(json_path, "rb") as f:
            for seg in ijson.items(f, "semanticSegments.item"):
                try:
                    if "visit" in seg:
                        ok = import_visit(session, seg)
                        visits += ok
                        skipped += not ok
                    elif "activity" in seg:
                        ok = import_activity(session, seg)
                        segments += ok
                        skipped += not ok
                    else:
                        # timelinePath entries are Google's own low-confidence
                        # fallback for stretches it couldn't classify - naively
                        # turning those into segments (an earlier version of
                        # this script did) produced fabricated travel that
                        # duplicated/overlapped Google's own confident activity
                        # segments (e.g. a real 13-minute drive also showing up
                        # as a bogus "cycling" leg) and phantom multi-hour
                        # "walking" out of GPS jitter while stationary at home.
                        # A gap in the Day timeline is more honest than
                        # confidently-wrong data.
                        skipped += 1
                except (KeyError, ValueError, TypeError) as e:
                    skipped += 1
                    print(f"  ! skipped malformed segment: {e}", flush=True)

                total = visits + segments
                if total and total % COMMIT_EVERY == 0:
                    session.commit()
                    elapsed = time.monotonic() - t0
                    print(f"  ... {visits} visits, {segments} segments, {skipped} skipped ({elapsed:.0f}s)", flush=True)

            session.commit()

        print("Recomputing trips from imported + existing visits...", flush=True)
        _rebuild_trips(session)
        session.commit()

    finally:
        session.close()

    print(f"Done. {visits} visits, {segments} segments imported, {skipped} skipped.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-google-timeline-export.json>")
        sys.exit(1)
    run(Path(sys.argv[1]))
