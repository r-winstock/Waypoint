"""Creates Visit rows from a pre-extracted candidates file produced by
backfill_stationary_visits.py's find_candidates() - see that script's own
docstring for the full rationale (stationary timelinePath segments from a
Google Timeline export that never became Visit rows).

Split out so the 150MB+ source export never needs to leave the machine that
already has it: find_candidates() there does the one-time parse of the full
export and writes out just the small (start_ts, end_ts, lat, lon) tuples it
found - typically a few hundred KB - which is all this script needs to
create the actual Visit rows and geocode them against a target database.

Purely additive and non-idempotent, same as backfill_stationary_visits.py -
only run once per candidates file.

Usage:
    python scripts/apply_stationary_visits.py candidates.json [--apply]

Without --apply, prints the plan only (dry run) against the first 10 candidates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.geocoding import resolve_place  # noqa: E402
from app.models import Visit  # noqa: E402
from app.processing import _rebuild_trips  # noqa: E402

COMMIT_EVERY = 200


def run(candidates_path: Path, apply: bool) -> None:
    init_db()
    candidates = json.loads(candidates_path.read_text())
    print(f"{len(candidates)} stationary visit candidates loaded" + ("" if apply else " (dry run - first 10 only)"))

    created = 0
    with SessionLocal() as session:
        for c in candidates if apply else candidates[:10]:
            start_ts, end_ts, lat, lon = c["start_ts"], c["end_ts"], c["lat"], c["lon"]
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
        print(f"Usage: {sys.argv[0]} <path-to-candidates.json> [--apply]")
        sys.exit(1)
    run(Path(positional[0]), apply="--apply" in sys.argv)
