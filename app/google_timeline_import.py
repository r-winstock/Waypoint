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

Lives in app/ (not scripts/) so it ships inside the running container and
can be driven from the API (see app/api/import_timeline.py) as well as from
the command line (scripts/import_google_timeline.py, now a thin wrapper
around run() below) - the manual/precise-cutoff workflow that wrapper
supports is still needed for edge cases like backfilling a specific gap,
which the API's auto-detected cutoff doesn't attempt.

since_ts restricts the import to segments starting on/after that epoch
second - added specifically for backfilling a recent tracking gap (a phone
that stopped reporting to OwnTracks for a stretch, recovered separately in
Google's own Timeline) without re-running the full historical import and
duplicating years of already-imported data. A fresh on-device Timeline
export still contains the user's whole retained history, not just new
segments, so filtering here (before any row is written) is what keeps a
gap-backfill (or the API's routine monthly catch-up) safe to run against a
live, already-populated database. Run without since_ts only against a
fresh/empty database - it does not de-duplicate against existing rows, so
running it twice over the same range double-imports everything.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import ijson

from app.db import SessionLocal, init_db
from app.geocoding import resolve_place
from app.models import TripSegment, Visit
from app.processing import _rebuild_trips, _remove_contained_google_visits

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


#  A skipped/unclassified entry (timelinePath, an unmapped activity type)
# between two same-mode activities doesn't reset the merge chain (see run()'s
# loop) since it usually represents Google being briefly unsure, not a real
# stop - but it can occasionally span real idle time, so merging still
# requires the two activities to be genuinely back-to-back, not just
# unseparated by a visit. Confirmed live: the real fragmentation case this
# exists for touches exactly (0s gap); 2 minutes is generous headroom for
# rounding without risking merging two actually-separate same-mode trips
# either side of a real gap.
MAX_MERGE_GAP_S = 120


def import_activity(session, seg: dict, last_segment: TripSegment | None) -> tuple[TripSegment | None, bool]:
    """Returns (the TripSegment this activity now belongs to, was_merged).
    Google's own segmentation routinely chops one continuous journey into
    several consecutive same-mode activity entries with no stop between them
    - confirmed live: a single ~38-minute walk came through as three separate
    entries (15min/3min/20min, timestamps touching exactly end-to-end, no
    visit in between). last_segment (None unless the immediately preceding
    processed entry was an activity of the SAME mode with nothing - no real
    visit - between them; see run()'s loop for how that's tracked) lets this
    extend that segment instead of creating a fragment."""
    activity = seg.get("activity", {})
    top = activity.get("topCandidate", {})
    mode = MODE_MAP.get(top.get("type"))
    if mode is None:
        return None, False
    distance_m = activity.get("distanceMeters") or 0.0
    start_ts = parse_ts(seg["startTime"])
    end_ts = parse_ts(seg["endTime"])
    duration_s = end_ts - start_ts
    if duration_s <= 0:
        return None, False

    if last_segment is not None and last_segment.mode == mode and start_ts - last_segment.end_ts <= MAX_MERGE_GAP_S:
        last_segment.end_ts = end_ts
        last_segment.distance_m += distance_m
        last_segment.duration_s = last_segment.end_ts - last_segment.start_ts
        return last_segment, True

    new_segment = TripSegment(
        start_ts=start_ts,
        end_ts=end_ts,
        mode=mode,
        distance_m=distance_m,
        duration_s=duration_s,
        source="google_import",
    )
    session.add(new_segment)
    return new_segment, False


def run(
    json_path: Path,
    since_ts: int | None = None,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> dict:
    """Returns {"visits": n, "segments": n, "skipped": n, "phantom_removed": n}.
    progress_callback(visits, segments, skipped), if given, is called every
    COMMIT_EVERY imported rows - the API's status-polling endpoint uses this
    to show live progress on what would otherwise be a silent multi-minute
    upload."""
    init_db()
    session = SessionLocal()
    visits = segments = skipped = merged = 0
    # The immediately-preceding processed entry, IF it was an activity with
    # nothing - no real visit - since it, so import_activity() can extend it
    # instead of fragmenting one journey into several same-mode rows (see
    # that function's docstring). A skipped/unclassified entry (timelinePath,
    # an unmapped activity type) leaves this unchanged - it isn't a real
    # stop, just Google being unsure for a moment - only an actual "visit"
    # entry resets it, even one that fails to resolve a place.
    last_activity_segment: TripSegment | None = None
    t0 = time.monotonic()

    try:
        with open(json_path, "rb") as f:
            for seg in ijson.items(f, "semanticSegments.item"):
                # since_ts filtering happens before anything else, on the raw
                # segment's own startTime - a segment entirely before the
                # requested window is silently ignored (not counted as
                # "skipped", which is reserved for genuinely malformed/
                # unclassifiable segments within the window being imported).
                if since_ts is not None:
                    start_time = seg.get("startTime")
                    if start_time and parse_ts(start_time) < since_ts:
                        continue
                try:
                    if "visit" in seg:
                        ok = import_visit(session, seg)
                        visits += ok
                        skipped += not ok
                        last_activity_segment = None
                    elif "activity" in seg:
                        result_segment, was_merged = import_activity(session, seg, last_activity_segment)
                        if result_segment is None:
                            skipped += 1
                        else:
                            segments += not was_merged
                            merged += was_merged
                            last_activity_segment = result_segment
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
                    if progress_callback is not None:
                        progress_callback(visits, segments, skipped)

            session.commit()

        print("Removing low-confidence phantom visits (Google's own fallback data)...", flush=True)
        phantom_removed = _remove_contained_google_visits(session)
        session.commit()
        print(f"  removed {phantom_removed} phantom visits", flush=True)

        print("Recomputing trips from imported + existing visits...", flush=True)
        _rebuild_trips(session)
        session.commit()

    finally:
        session.close()

    print(f"Done. {visits} visits, {segments} segments imported ({merged} merged into them), {skipped} skipped.")
    return {"visits": visits, "segments": segments, "merged": merged, "skipped": skipped, "phantom_removed": phantom_removed}
