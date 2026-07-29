"""Pre-seeds Place rows from a resolved-place-cache export so
apply_stationary_visits.py never has to hit Nominatim for a coordinate
that's already been geocoded elsewhere (e.g. on a dev copy of this same
database).

Only inserts a place when nothing already exists at that exact rounded
coordinate - never overwrites an existing row, so it's safe to run against
a database that already has some overlapping history. Genuinely idempotent:
running it twice just finds everything already seeded the second time and
inserts nothing more.

Usage:
    python scripts/seed_resolved_places.py resolved_place_cache.json [--apply]

Without --apply, prints the plan only (dry run) against the first 10 entries.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Place  # noqa: E402


def run(cache_path: Path, apply: bool) -> None:
    init_db()
    entries = json.loads(cache_path.read_text(encoding="utf-8"))
    print(f"{len(entries)} resolved places in cache" + ("" if apply else " (dry run - first 10 only)"))

    seeded = skipped = 0
    with SessionLocal() as session:
        for e in entries if apply else entries[:10]:
            existing = (
                session.query(Place)
                .filter(Place.lat_round == e["lat_round"], Place.lon_round == e["lon_round"])
                .one_or_none()
            )
            if existing is not None:
                skipped += 1
                continue
            print(f"  seed {e['lat_round']}, {e['lon_round']} -> {e['name']!r} / {e['city']!r}")
            if not apply:
                continue
            session.add(
                Place(
                    lat_round=e["lat_round"],
                    lon_round=e["lon_round"],
                    google_place_id=e["google_place_id"],
                    name=e["name"],
                    category=e["category"] or "Other places",
                    city=e["city"],
                    country=e["country"],
                    name_local=e["name_local"],
                    city_local=e["city_local"],
                    country_code=e["country_code"],
                    raw_json=e["raw_json"],
                    manually_corrected=bool(e["manually_corrected"]),
                )
            )
            seeded += 1

        if apply:
            session.commit()
            print(f"Done. Seeded {seeded}, skipped {skipped} (already present).")
        else:
            print("\nDry run complete (first 10 only) - re-run with --apply for the full set.")


if __name__ == "__main__":
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not positional:
        print(f"Usage: {sys.argv[0]} <path-to-resolved-place-cache.json> [--apply]")
        sys.exit(1)
    run(Path(positional[0]), apply="--apply" in sys.argv)
