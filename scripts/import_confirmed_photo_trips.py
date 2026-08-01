"""One-off import of the specific historic trips confirmed by hand after
reviewing scripts/import_google_photos.py's --geocode-folders report: real
destinations whose Google Photos folder had no GPS at all (older cameras,
pre-dating phone GPS tagging), but a clear place name + date(s) in the
folder title, manually verified against the automated geocode guesses
(several of which were wrong - short/generic names with no location hint
matched random unrelated places worldwide, e.g. "Sammy" -> Tokyo,
"Summerhill" -> Toronto instead of the real Summerhill Caravan Park,
Narberth).

Each entry gets its coordinates from a fresh, specific Nominatim search
(not the earlier blind folder-name guess) except Summerhill, whose real
address/coordinates were given directly. Creates one Visit per date range
(source="photo_import", same as the GPS-based import), then rebuilds
trips.

Run once - no de-duplication, safe to re-run only against a fresh/restored
database.

Usage:
    python scripts/import_confirmed_photo_trips.py [--apply]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.geocoding import search_places  # noqa: E402
from app.models import Trip, Visit  # noqa: E402
from app.processing import _geocode_visits, _rebuild_trips  # noqa: E402

# (search query, [(start_date, end_date), ...]) - search query is what gets
# geocoded fresh; Summerhill skips search entirely since its exact address
# was given directly rather than guessed.
GEOCODE_TRIPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Budapest, Hungary", [("2004-11-17", "2004-11-19")]),
    ("Paris, France", [("2005-02-21", "2005-02-21")]),
    ("Dominican Republic", [("2003-05-07", "2003-05-22")]),
    ("New Zealand", [("2004-02-27", "2004-03-12")]),
    ("Canada", [("2004-09-22", "2004-09-30")]),
    ("St Andrews, United Kingdom", [("2004-05-26", "2004-05-28")]),
    ("Coniston, United Kingdom", [("2003-10-10", "2003-10-12")]),
    ("Longleat Safari Park, Warminster, United Kingdom", [
        ("2002-03-01", "2002-03-01"),
        ("2003-04-04", "2003-04-05"),
        ("2005-02-09", "2005-02-09"),
    ]),
    ("Eden Project, United Kingdom", [("2005-02-22", "2005-02-23")]),
    ("Benidoleig, Spain", [
        ("2006-02-15", "2006-02-21"),
        ("2007-10-24", "2007-11-02"),
        ("2011-07-25", "2011-07-31"),
    ]),
]

# Given directly, not geocoded - see module docstring on why (the blind
# folder-name search wrongly matched Toronto).
SUMMERHILL_LAT, SUMMERHILL_LON = 51.7366129, -4.6771971
SUMMERHILL_DATES = [
    ("2002-10-21", "2002-10-26"),
    ("2004-05-28", "2004-05-31"),
    ("2004-07-24", "2004-07-31"),
    ("2005-06-09", "2005-06-13"),
    ("2005-07-29", "2005-08-02"),
    ("2005-10-21", "2005-10-24"),
    ("2006-04-12", "2006-04-16"),
    ("2006-07-28", "2006-07-30"),
    ("2007-03-15", "2007-03-19"),
    ("2007-08-28", "2007-09-03"),
]


def _ts(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _overlapping_trip(session, start_ts: int, end_ts: int) -> Trip | None:
    """Distance-based dedup (see import_google_photos.py's own version)
    doesn't work for these entries: a country-level search point ("Canada",
    "New Zealand") can be hundreds of km from where a real, already-
    imported trip's actual visits sit, so a plain radius check never
    catches the overlap. Confirmed live this was a real problem, not
    theoretical: three of the first ten confirmed trips here (New Zealand,
    Canada, Dominican Republic) turned out to already exist as proper,
    better-detailed trips from an earlier Travellerspoint KML import, and
    the distance check missed every one. A date-range overlap against
    *any* existing trip, regardless of source or distance, is a much
    blunter but far more reliable signal that this is the same real trip
    already captured elsewhere."""
    return (
        session.query(Trip)
        .filter(Trip.start_ts <= end_ts, Trip.end_ts >= start_ts)
        .first()
    )


def run(apply: bool) -> None:
    init_db()
    session = SessionLocal()
    created = skipped = 0
    try:
        for query, date_ranges in GEOCODE_TRIPS:
            results = search_places(query)
            if not results:
                print(f"! No geocode match for {query!r} - skipped, add manually")
                continue
            top = results[0]
            print(f"{query!r} -> {top['name']} ({top['lat']:.4f}, {top['lon']:.4f})")
            for start_date, end_date in date_ranges:
                start_ts, end_ts = _ts(start_date), _ts(end_date) + 86399
                existing = _overlapping_trip(session, start_ts, end_ts)
                if existing is not None:
                    print(f"  {start_date} - {end_date}  SKIPPED - overlaps existing trip {existing.id} ({existing.primary_city}, {existing.primary_country})")
                    skipped += 1
                    continue
                print(f"  {start_date} - {end_date}")
                if apply:
                    session.add(
                        Visit(start_ts=start_ts, end_ts=end_ts, lat=top["lat"], lon=top["lon"], point_count=1, source="photo_import")
                    )
                created += 1

        print(f"\n'Summerhill Caravan Park, Narberth' -> given directly ({SUMMERHILL_LAT}, {SUMMERHILL_LON})")
        for start_date, end_date in SUMMERHILL_DATES:
            start_ts, end_ts = _ts(start_date), _ts(end_date) + 86399
            existing = _overlapping_trip(session, start_ts, end_ts)
            if existing is not None:
                print(f"  {start_date} - {end_date}  SKIPPED - overlaps existing trip {existing.id} ({existing.primary_city}, {existing.primary_country})")
                skipped += 1
                continue
            print(f"  {start_date} - {end_date}")
            if apply:
                session.add(
                    Visit(start_ts=start_ts, end_ts=end_ts, lat=SUMMERHILL_LAT, lon=SUMMERHILL_LON, point_count=1, source="photo_import")
                )
            created += 1

        if apply:
            session.commit()
            print("\nGeocoding new visits...")
            _geocode_visits(session)
            session.commit()
            print("Recomputing trips...")
            _rebuild_trips(session)
            session.commit()
        else:
            print(f"\nDry run - {created} visits would be created. Re-run with --apply for real.")
    finally:
        session.close()

    print(f"\nDone. {created} visits {'created' if apply else 'would be created'}, {skipped} skipped (already covered by an existing trip).")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
