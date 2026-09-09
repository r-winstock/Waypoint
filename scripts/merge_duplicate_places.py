"""One-off data cleanup: merges duplicate Place rows that share the same
google_place_id, accumulated from before resolve_place() looked up by
placeId first (see app/geocoding.py's resolve_place, fixed in v0.32.9) -
each import cycle's coordinate-rounding drift used to create a fresh row
per real-world place instead of reusing the existing one for that placeId.
Confirmed live: 69 groups, up to 7 rows deep.

For each google_place_id with more than one Place row: picks a winner (the
manually_corrected row if exactly one exists, else the lowest id - same
deterministic tie-break resolve_place() itself now uses), repoints every
Visit pointing at a loser row onto the winner, then deletes the loser rows.

A group with more than one manually_corrected row is ambiguous (two
separate corrections may disagree) - still merged onto the lowest-id
manually_corrected row, but flagged in the report so it can be reviewed
afterwards rather than silently trusted.

Usage:
    python scripts/merge_duplicate_places.py           # dry run, reports only
    python scripts/merge_duplicate_places.py --apply    # applies the merge
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func

from app.db import SessionLocal
from app.models import Place, Visit


def find_duplicate_groups(session) -> list[list[Place]]:
    dupe_ids = (
        session.query(Place.google_place_id)
        .filter(Place.google_place_id.isnot(None))
        .group_by(Place.google_place_id)
        .having(func.count(Place.id) > 1)
        .all()
    )
    return [
        session.query(Place).filter(Place.google_place_id == gpid).order_by(Place.id).all()
        for (gpid,) in dupe_ids
    ]


def pick_winner(rows: list[Place]) -> Place:
    corrected = [r for r in rows if r.manually_corrected]
    if len(corrected) == 1:
        return corrected[0]
    # Zero or multiple manually_corrected rows - fall back to the oldest
    # (lowest id, rows is already id-ordered), same tie-break resolve_place()
    # itself uses so the two stay consistent.
    return rows[0]


def has_conflicting_names(rows: list[Place]) -> bool:
    """Multiple manually_corrected rows sharing one google_place_id are
    usually the same real place independently corrected more than once (safe
    to merge onto any of them) - but confirmed live, one group was a genuine
    McDonald's-vs-Sainsbury's conflict, ~55m apart, sharing a placeId Google
    itself mismatched for one visit. Auto-merging that would silently
    relabel one business as the other, so any group where the corrected
    rows disagree on name gets skipped entirely rather than merged."""
    corrected_names = {r.name for r in rows if r.manually_corrected}
    return len(corrected_names) > 1


def run(apply: bool) -> None:
    session = SessionLocal()
    groups = find_duplicate_groups(session)
    print(f"{len(groups)} duplicate google_place_id groups found")

    total_visits_repointed = 0
    total_rows_deleted = 0
    redundant_corrections = []
    skipped_conflicts = []

    for rows in groups:
        corrected_count = sum(1 for r in rows if r.manually_corrected)

        if corrected_count > 1 and has_conflicting_names(rows):
            skipped_conflicts.append(rows)
            print(f"  SKIPPED (conflicting names): google_place_id {rows[0].google_place_id}")
            for r in rows:
                print(f"    id={r.id} name={r.name!r} manually_corrected={r.manually_corrected}")
            continue

        winner = pick_winner(rows)
        losers = [r for r in rows if r.id != winner.id]
        if corrected_count > 1:
            redundant_corrections.append(rows)

        for loser in losers:
            visits = session.query(Visit).filter(Visit.place_id == loser.id).all()
            flag = " [redundant duplicate correction]" if corrected_count > 1 else ""
            print(f"  {winner.name!r} (winner id={winner.id}) <- id={loser.id} ({len(visits)} visits){flag}")
            if apply:
                for v in visits:
                    v.place_id = winner.id
                session.delete(loser)
            total_visits_repointed += len(visits)
            total_rows_deleted += 1

    if apply:
        session.commit()
        print(f"\nApplied: {total_visits_repointed} visits repointed, {total_rows_deleted} duplicate rows deleted")
    else:
        print(f"\nDry run: would repoint {total_visits_repointed} visits, delete {total_rows_deleted} duplicate rows")

    if skipped_conflicts:
        print(f"\n{len(skipped_conflicts)} group(s) SKIPPED - conflicting names, needs manual review:")
        for rows in skipped_conflicts:
            print(f"  google_place_id group ({rows[0].google_place_id}):")
            for r in rows:
                print(f"    id={r.id} name={r.name!r} manually_corrected={r.manually_corrected}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply the merge (default: dry run)")
    args = parser.parse_args()
    run(apply=args.apply)
