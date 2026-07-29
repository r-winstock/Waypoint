"""Backfills Visit rows for stationary timelinePath segments that the Google
Timeline import currently discards entirely.

Motivated by a real trip that never closed: a 120-day "trip" whose visits
were actually a genuine Italy holiday, a Cotswold outing, and ordinary daily
life all glued together, because the trip-boundary heuristic never saw a
single Visit close enough to home to break the run. Checking the raw export
directly showed why - on the days in question there's no explicit "visit"
for the long stretches spent at home at all. Google represents some
stationary periods as a `timelinePath` (a raw point trail) rather than a
confident `visit`, and import_google_timeline.py's own comment on that
branch explains why it's skipped outright: an earlier version of this
script turned every timelinePath into a travel segment, which produced
"phantom multi-hour walking" out of GPS jitter while genuinely stationary.
That fix was half right - it stopped fabricating travel, but left the other
half of the same problem unaddressed: a real stay with nothing confidently
classified is not represented as a Visit either, so it's invisible to
_rebuild_trips' home-radius check.

Scanning the full export found this isn't a one-off: 4,678 of 13,767
timelinePath segments (34%, ~390 days of accumulated time) have every point
within 200m of their own centroid - i.e. genuinely stationary, not
unconfident movement. This script re-scans the export for exactly those
segments and creates a real Visit at the centroid coordinate for each one it
finds, geocoded the same way import_google_timeline.py's own import_visit
does. Genuinely non-stationary timelinePath segments (spread >= 200m) are
left alone, matching the existing "don't fabricate movement" policy.

Purely additive - never touches an existing Visit/TripSegment row, so it's
safe to run against a database that's already been through
import_google_timeline.py. Recomputes source="computed" trips afterwards
(_rebuild_trips already only ever touches that source) so previously-merged
"mega trips" split back into their real separate trips once the missing
home stays are filled in.

Not idempotent - re-running it against the same database after --apply will
create a second Visit for every stationary segment already backfilled. Only
run it once per Timeline export.

Usage:
    python scripts/backfill_stationary_visits.py "/path/to/Timeline (....json)" [--apply]

Without --apply, prints the plan only (dry run) against the first 10 candidates.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.geocoding import resolve_place  # noqa: E402
from app.models import Visit  # noqa: E402
from app.processing import _rebuild_trips, haversine_m  # noqa: E402

# Widened from an initial 200m after a confirmed real miss: a genuine
# evening at home came back with a 324m spread (sparse points, more GPS
# jitter than a tight 200m cutoff allowed), while nearby non-stationary
# segments that same day measured 1.9km/2.7km - genuinely local movement,
# correctly still excluded. The full distribution has no sharp cliff
# (200m: 44% of segments, 400m: 57%, 1000m: 61%), so there's no single
# "correct" value - 400m is chosen to comfortably clear the confirmed real
# case with some margin, well short of where real short errands start
# (a drive to even a nearby shop typically covers much more than this).
STATIONARY_RADIUS_M = 400.0
COMMIT_EVERY = 200


def parse_ts(iso_str: str) -> int:
    return int(datetime.fromisoformat(iso_str).timestamp())


def parse_point(p: dict) -> tuple[float, float]:
    lat_str, lon_str = p["point"].replace("°", "").split(",")
    return float(lat_str.strip()), float(lon_str.strip())


def stationary_centroid(points: list[dict]) -> tuple[float, float, float] | None:
    coords = [parse_point(p) for p in points]
    lat = sum(c[0] for c in coords) / len(coords)
    lon = sum(c[1] for c in coords) / len(coords)
    spread = max(haversine_m(lat, lon, c[0], c[1]) for c in coords)
    return lat, lon, spread


def find_candidates(json_path: Path) -> list[tuple[int, int, float, float]]:
    candidates = []
    with open(json_path, "rb") as f:
        for seg in ijson.items(f, "semanticSegments.item"):
            points = seg.get("timelinePath")
            # A single-point timelinePath is trivially "stationary" (spread=0,
            # nothing to compare it against) - previously required >=2 points
            # to compute a spread at all, which silently discarded 798 real
            # segments across the export, several of them genuine at-home
            # stays with only one GPS fix logged for the whole window.
            if not points:
                continue
            lat, lon, spread = stationary_centroid(points)
            if spread >= STATIONARY_RADIUS_M:
                continue
            start_ts = parse_ts(seg["startTime"])
            end_ts = parse_ts(seg["endTime"])
            if end_ts <= start_ts:
                continue
            candidates.append((start_ts, end_ts, lat, lon))
    return candidates


def run(json_path: Path, apply: bool) -> None:
    init_db()
    candidates = find_candidates(json_path)
    print(f"{len(candidates)} stationary timelinePath segments found" + ("" if apply else " (dry run - first 10 only)"))

    created = 0
    with SessionLocal() as session:
        for start_ts, end_ts, lat, lon in candidates if apply else candidates[:10]:
            print(f"  visit {start_ts}-{end_ts} ({(end_ts - start_ts) / 3600:.1f}h) at ({lat:.5f}, {lon:.5f})")
            if not apply:
                continue
            visit = Visit(start_ts=start_ts, end_ts=end_ts, lat=lat, lon=lon, point_count=1, source="google_import")
            session.add(visit)
            session.flush()
            place = resolve_place(session, lat, lon)
            if place is not None:
                visit.place_id = place.id
            created += 1
            if created % COMMIT_EVERY == 0:
                session.commit()
                print(f"  ...{created}/{len(candidates)} committed")

        if apply:
            session.commit()
            print("Recomputing trips across all sources...")
            _rebuild_trips(session)
            session.commit()
            print(f"Done. {created} visits created.")
        else:
            print("\nDry run complete (first 10 only) - re-run with --apply for the full set.")


if __name__ == "__main__":
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not positional:
        print(f"Usage: {sys.argv[0]} <path-to-timeline-export.json> [--apply]")
        sys.exit(1)
    run(Path(positional[0]), apply="--apply" in sys.argv)
