"""Imports geotagged photos from a Google Photos Takeout export (the
`<filename>.supplemental-metadata.json` sidecar files found alongside each
photo/video) into Waypoint's Visit table, to recover trips/visits from
before phone-based GPS tracking (OwnTracks/Google Timeline) existed at all.

Surveyed live against a real ~35,000-photo, 1948-2026 archive: 38% of
photos carry real GPS coordinates in their sidecar JSON - plenty to work
with, and (per the source library's own year-by-year photo counts) heaviest
in exactly the pre-2013 period this app's other import sources don't cover.

A single photo is one moment, not a continuous track - unlike OwnTracks
pings, there's no raw trace to cluster via app/processing.py's stay-point
algorithm (source="owntracks" only, see that module's own docstring on
why). Instead this clusters geotagged photos directly: consecutive photos
(sorted by time) join the same cluster while both the time gap and the
distance moved stay under CLUSTER_MAX_GAP_S/CLUSTER_MAX_DIST_M, becoming
one Visit each with source="photo_import" - the same "write Visit rows
directly, let _rebuild_trips() do the rest" pattern as
scripts/import_google_timeline.py's own "visit" segments.

Skips a cluster entirely if an existing Visit (any source) already covers
that time+place - the point of this script is recovering trips no other
data source captured, not duplicating ones OwnTracks/Google Timeline/KML
import already have better (denser, more precise) data for.

Folders whose name carries a parseable date (or date range) but have zero
geotagged photos are reported at the end, not auto-imported: the folder
name is often just an event title ("Xmas", "Weekend Fun"), not a real
place, and guessing wrong would plant a bogus Visit in someone's actual
history - left for a human (or a separate, more careful pass) to review.

Usage:
    python scripts/import_google_photos.py "//redwood.home.lan/media/photos" [--apply] [--geocode-folders]

Without --apply, prints the plan only (dry run): cluster count, estimated
new visits, and the folders-needing-review report - no database writes.
--geocode-folders additionally attempts a best-effort Nominatim search on
each review folder's own name (e.g. "Budapest", stripped of its date) -
reported for a human to pick through, never auto-imported: with no GPS at
all to bias the search, a short/generic folder name is genuinely ambiguous
worldwide, and an event name ("Kittens", "Xmas") isn't a real place to
begin with. Slow (~1 request/second, Nominatim's own usage policy) - only
worth running once you've already reviewed the plain folder list.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.geocoding import search_places  # noqa: E402
from app.models import Trip, Visit  # noqa: E402
from app.processing import _geocode_visits, _rebuild_trips, haversine_m  # noqa: E402

# Category strings search_places() falls back to when Nominatim didn't
# confidently tag the result as anything specific - a hit still worth
# reporting (better than nothing) but not one to trust without a human
# actually reading it, unlike a hit resolved to a real category (Culture,
# Hotels, Food and drink, ...).
GENERIC_CATEGORIES = {"Other places", "Streets and roads"}

# A gap this long between two geotagged photos ends the current cluster,
# even if they're at the same spot - two separate visits to a place days
# apart shouldn't merge into one. Generous enough that a multi-day stay at
# one location (a week's camping trip, photographed a few times a day)
# still reads as a single Visit, matching how a long Home stay already
# does today.
CLUSTER_MAX_GAP_S = 6 * 3600

# Consecutive photos further apart than this are treated as different
# places, not GPS jitter around the same spot - 1km is coarse (a phone's
# own GPS is usually far more precise than this) but deliberately so: a
# photo's location is a single fix, not an averaged cluster of pings the
# way an OwnTracks stay-point already is, so erring generous avoids
# splitting one real visit into several over ordinary GPS wobble.
CLUSTER_MAX_DIST_M = 1000.0

# A candidate cluster is skipped if an existing Visit (any source) already
# overlaps its time span and sits within this distance - the two are
# almost certainly the same real visit, already captured with better
# (denser, more precise) data than a handful of photo GPS fixes can offer.
DEDUPE_MAX_DIST_M = 2000.0

DATE_RANGE_RE = re.compile(r"\((\d{2}-\d{2}-\d{4})(?:\s*-\s*(\d{2}-\d{2}-\d{4}))?\)")

COMMIT_EVERY = 50


@dataclass
class PhotoPoint:
    tst: int
    lat: float
    lon: float
    folder: str


def _iter_geotagged_photos(root: Path) -> list[PhotoPoint]:
    points: list[PhotoPoint] = []
    bad = 0
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        for sidecar in folder.glob("*.supplemental-metadata.json"):
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bad += 1
                continue
            ts = data.get("photoTakenTime", {}).get("timestamp")
            if not ts:
                bad += 1
                continue
            geo = data.get("geoData") or {}
            lat, lon = geo.get("latitude"), geo.get("longitude")
            if not lat and not lon:
                geo = data.get("geoDataExif") or {}
                lat, lon = geo.get("latitude"), geo.get("longitude")
            if not lat and not lon:
                continue  # no real GPS on this one - not an error, just untagged
            points.append(PhotoPoint(tst=int(ts), lat=lat, lon=lon, folder=folder.name))
    if bad:
        print(f"  ({bad} sidecar files skipped - missing/unparseable timestamp)")
    return points


@dataclass
class Cluster:
    start_ts: int
    end_ts: int
    lat: float
    lon: float
    photo_count: int
    folders: set[str]


def _cluster_photos(points: list[PhotoPoint]) -> list[Cluster]:
    points = sorted(points, key=lambda p: p.tst)
    clusters: list[Cluster] = []
    run: list[PhotoPoint] = []

    def flush():
        if not run:
            return
        lat = sum(p.lat for p in run) / len(run)
        lon = sum(p.lon for p in run) / len(run)
        clusters.append(
            Cluster(
                start_ts=run[0].tst,
                end_ts=run[-1].tst,
                lat=lat,
                lon=lon,
                photo_count=len(run),
                folders={p.folder for p in run},
            )
        )

    for p in points:
        if run:
            gap_s = p.tst - run[-1].tst
            dist_m = haversine_m(run[-1].lat, run[-1].lon, p.lat, p.lon)
            if gap_s > CLUSTER_MAX_GAP_S or dist_m > CLUSTER_MAX_DIST_M:
                flush()
                run = []
        run.append(p)
    flush()
    return clusters


def _already_covered(session, cluster: Cluster) -> bool:
    nearby = (
        session.query(Visit)
        .filter(Visit.end_ts >= cluster.start_ts, Visit.start_ts <= cluster.end_ts)
        .all()
    )
    if any(haversine_m(v.lat, v.lon, cluster.lat, cluster.lon) <= DEDUPE_MAX_DIST_M for v in nearby):
        return True
    # Distance-based dedup alone missed real duplicates confirmed live: a
    # cluster's own centroid can sit well outside DEDUPE_MAX_DIST_M of any
    # single kml_import visit (e.g. a brief connecting-flight stop hundreds
    # of km from that trip's other stops) while still falling entirely
    # within a trip already fully captured by that earlier import. Scoped
    # to kml_import specifically, not every trip source: a kml_import
    # trip's own visits are sparse waypoints (see app/processing.py's own
    # reasoning for excluding it from the gap/radius heuristic), so it's
    # the one source where a cluster can genuinely fall inside a real trip
    # without ever landing near any single one of its recorded stops.
    return (
        session.query(Trip)
        .filter(Trip.source == "kml_import", Trip.start_ts <= cluster.end_ts, Trip.end_ts >= cluster.start_ts)
        .first()
        is not None
    )


def _folders_needing_review(root: Path, geotagged_folders: set[str]) -> list[str]:
    review = []
    for folder in sorted(p.name for p in root.iterdir() if p.is_dir()):
        if folder in geotagged_folders:
            continue
        if DATE_RANGE_RE.search(folder):
            review.append(folder)
    return review


# Strips every parenthetical group, not just the date - several folder
# names carry a second one too ("Christmas Party (09-12-2004) (Pete)",
# "Simon & Nic's Wedding (Chris A) (18-10-2002 - 20-10-2002)"), and a
# leftover "(Pete)" in the search query would only hurt the geocode match.
PAREN_RE = re.compile(r"\([^)]*\)")


def _clean_folder_name(folder: str) -> str:
    return PAREN_RE.sub("", folder).strip()


def _geocode_review_folders(review: list[str]) -> None:
    """Best-effort geocode of each review folder's own name (a folder
    called "Kittens" or "Xmas" isn't a real place and won't match anything
    useful; one called "Budapest" or "Paris" should) - reported for a human
    to pick through, never auto-imported. No location hint is available
    (that's the whole reason these folders have no GPS to begin with), so a
    short/generic name is genuinely ambiguous worldwide - flagging whether
    Nominatim resolved a real category (vs its generic fallback) is a weak
    but useful signal for which hits are worth a second look first."""
    print(f"\nGeocoding {len(review)} folder names (best-effort, ~1/sec - this will take a while)...", flush=True)
    confident, uncertain, no_match = [], [], []
    for i, folder in enumerate(review, start=1):
        name = _clean_folder_name(folder)
        results = search_places(name) if name else []
        if not results:
            no_match.append(folder)
        else:
            top = results[0]
            line = f"  {folder!r} -> {top['name']}" + (f", {top['city']}" if top["city"] else "") + (f", {top['country']}" if top["country"] else "")
            (uncertain if top["category"] in GENERIC_CATEGORIES else confident).append(line)
        if i % 20 == 0:
            print(f"  ... {i}/{len(review)} checked", flush=True)

    if confident:
        print(f"\n{len(confident)} folders geocoded to a real category - probably worth importing:")
        for line in confident:
            print(line)
    if uncertain:
        print(f"\n{len(uncertain)} folders matched something, but only a generic category - check these carefully:")
        for line in uncertain:
            print(line)
    if no_match:
        print(f"\n{len(no_match)} folders had no geocode match at all (likely just an event name, not a place):")
        for folder in no_match:
            print(f"  {folder!r}")


def run(root: Path, apply: bool, geocode_folders: bool) -> None:
    print(f"Scanning {root} ...", flush=True)
    points = _iter_geotagged_photos(root)
    print(f"{len(points)} geotagged photos found", flush=True)

    clusters = _cluster_photos(points)
    print(f"{len(clusters)} candidate visit clusters", flush=True)

    init_db()
    session = SessionLocal()
    created = skipped_duplicate = 0
    t0 = time.monotonic()

    try:
        for i, cluster in enumerate(clusters, start=1):
            if _already_covered(session, cluster):
                skipped_duplicate += 1
                continue
            if apply:
                session.add(
                    Visit(
                        start_ts=cluster.start_ts,
                        end_ts=cluster.end_ts,
                        lat=cluster.lat,
                        lon=cluster.lon,
                        point_count=cluster.photo_count,
                        source="photo_import",
                    )
                )
            created += 1
            if apply and created % COMMIT_EVERY == 0:
                session.commit()
                elapsed = time.monotonic() - t0
                print(f"  ... {i}/{len(clusters)} clusters, {created} visits so far ({elapsed:.0f}s)", flush=True)

        if apply:
            session.commit()
            print("Geocoding new visits...", flush=True)
            _geocode_visits(session)
            session.commit()
            print("Recomputing trips from imported + existing visits...", flush=True)
            _rebuild_trips(session)
            session.commit()
        else:
            print("\nDry run - no database writes. Re-run with --apply to import for real.")

        geotagged_folders = {f for c in clusters for f in c.folders}
        review = _folders_needing_review(root, geotagged_folders)
        if review:
            print(f"\n{len(review)} dated folders have NO geotagged photos - not imported, worth a manual look:")
            for name in review:
                print(f"  - {name}")
            if geocode_folders:
                _geocode_review_folders(review)
            else:
                print("(re-run with --geocode-folders to attempt matching these names to real places)")

    finally:
        session.close()

    print(f"\nDone. {created} visits {'created' if apply else 'would be created'}, {skipped_duplicate} skipped (already covered by existing data).")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path-to-photos-root> [--apply] [--geocode-folders]")
        sys.exit(1)
    run(Path(sys.argv[1]), apply="--apply" in sys.argv, geocode_folders="--geocode-folders" in sys.argv)
