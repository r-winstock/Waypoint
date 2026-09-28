"""One-off data cleanup: merges already-imported google_import TripSegment
rows that Google's own Timeline export fragmented into several consecutive
same-mode entries with nothing real between them. Confirmed live: a single
~38-minute walk on 26 Sept came through as three separate entries
(15min/3min/20min, timestamps touching exactly end-to-end, no visit in
between) - see app/google_timeline_import.py's import_activity(), which now
prevents this on future imports; this script cleans up history imported
before that fix existed.

Two consecutive TripSegment rows (ordered by start_ts, source=google_import)
merge when: same mode, no Visit (any source) starts strictly between them,
and the gap between the first's end_ts and the second's start_ts is at most
MAX_MERGE_GAP_S (matches the live-import threshold - generous enough for
rounding, not so generous it merges two genuinely separate same-mode trips
either side of a real gap).

Usage:
    python scripts/merge_fragmented_segments.py           # dry run, reports only
    python scripts/merge_fragmented_segments.py --apply    # applies the merge
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.google_timeline_import import MAX_MERGE_GAP_S
from app.models import CachedRoute, TripSegment, Visit


def run(apply: bool) -> None:
    session = SessionLocal()

    segments = session.query(TripSegment).filter(TripSegment.source == "google_import").order_by(TripSegment.start_ts).all()
    visit_starts = sorted(v.start_ts for v in session.query(Visit.start_ts).all())

    def visit_between(start_ts: int, end_ts: int) -> bool:
        return any(start_ts < vs < end_ts for vs in visit_starts)

    merged_groups = 0
    rows_deleted = 0
    routes_deleted = 0

    i = 0
    while i < len(segments):
        run_start = i
        current = segments[i]
        total_distance = current.distance_m
        j = i + 1
        while (
            j < len(segments)
            and segments[j].mode == current.mode
            and segments[j].start_ts - segments[j - 1].end_ts <= MAX_MERGE_GAP_S
            and not visit_between(segments[j - 1].end_ts - 1, segments[j].start_ts + 1)
        ):
            total_distance += segments[j].distance_m
            j += 1

        if j - run_start > 1:
            winner = segments[run_start]
            losers = segments[run_start + 1 : j]
            new_end_ts = segments[j - 1].end_ts
            print(
                f"  merge {j - run_start} {winner.mode} segments: "
                f"{winner.start_ts} -> {new_end_ts} "
                f"(ids {[s.id for s in segments[run_start:j]]}, total {total_distance/1000:.1f}km)"
            )
            if apply:
                winner.end_ts = new_end_ts
                winner.distance_m = total_distance
                winner.duration_s = new_end_ts - winner.start_ts
                for loser in losers:
                    cached = session.get(CachedRoute, loser.id)
                    if cached is not None:
                        session.delete(cached)
                        routes_deleted += 1
                    session.delete(loser)
            merged_groups += 1
            rows_deleted += len(losers)

        i = j

    if apply:
        session.commit()
        print(f"\nApplied: {merged_groups} groups merged, {rows_deleted} fragment rows deleted, {routes_deleted} stale cached routes deleted")
    else:
        print(f"\nDry run: would merge {merged_groups} groups, delete {rows_deleted} fragment rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply the merge (default: dry run)")
    args = parser.parse_args()
    run(apply=args.apply)
