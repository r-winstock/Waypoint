"""One-off cleanup for Place rows that predate the geocoding.py dedup fix
(see PLACE_DEDUP_RADIUS_M / _find_duplicate_place there for why this
happened): GPS drift across repeat visits to the same real place rounded to a
different coordinate cell each time, missing the exact-match cache and
creating a fresh Place row - ten separate "Ashmead Road, Bedford" rows for one
house, for example.

Groups existing places by (name, city), then within each group clusters by
mutual proximity (PLACE_DEDUP_RADIUS_M, same threshold the prospective fix
uses) rather than merging the whole name+city group outright - a city can
have two genuinely distinct branches of the same shop, which must stay
separate. For each cluster of 2+, keeps the place with the most visits
attached (most representative row, and the one least likely to be an
orphaned one-off), repoints every Visit.place_id in the cluster to it, and
deletes the rest.

City/country grouping for Trip.primary_city is unaffected by this merge
(duplicates within a cluster already share the same name+city), so trips
don't need recomputing afterwards.

Usage:
    python scripts/dedupe_places.py [--apply]

Without --apply, prints the plan only (dry run).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Place, Visit  # noqa: E402
from app.processing import haversine_m  # noqa: E402

PLACE_DEDUP_RADIUS_M = 300.0


def _cluster(places: list[Place]) -> list[list[Place]]:
    """Greedy proximity clustering: each place joins the first existing
    cluster whose centroid it's within radius of, else starts a new one."""
    clusters: list[list[Place]] = []
    for place in places:
        for cluster in clusters:
            centroid_lat = sum(p.lat_round for p in cluster) / len(cluster)
            centroid_lon = sum(p.lon_round for p in cluster) / len(cluster)
            if haversine_m(centroid_lat, centroid_lon, place.lat_round, place.lon_round) <= PLACE_DEDUP_RADIUS_M:
                cluster.append(place)
                break
        else:
            clusters.append([place])
    return clusters


def run(apply: bool) -> None:
    init_db()
    with SessionLocal() as session:
        groups: dict[tuple[str, str | None], list[Place]] = defaultdict(list)
        for place in session.query(Place).filter(Place.name.isnot(None)).all():
            groups[(place.name, place.city)].append(place)

        merged_places = 0
        repointed_visits = 0

        for (name, city), places in groups.items():
            if len(places) < 2:
                continue
            for cluster in _cluster(places):
                if len(cluster) < 2:
                    continue
                visit_counts = {
                    p.id: session.query(Visit).filter(Visit.place_id == p.id).count() for p in cluster
                }
                canonical = max(cluster, key=lambda p: visit_counts[p.id])
                duplicates = [p for p in cluster if p.id != canonical.id]

                print(
                    f"{name!r} ({city}): merging {[p.id for p in duplicates]} "
                    f"into {canonical.id} ({sum(visit_counts.values())} visits total)"
                )

                if apply:
                    for dup in duplicates:
                        count = (
                            session.query(Visit)
                            .filter(Visit.place_id == dup.id)
                            .update({Visit.place_id: canonical.id})
                        )
                        repointed_visits += count
                        session.delete(dup)
                    merged_places += len(duplicates)

        if apply:
            session.commit()
            print(f"\nDone. Merged {merged_places} duplicate places, repointed {repointed_visits} visits.")
        else:
            print("\nDry run - no changes made. Re-run with --apply to actually merge.")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
