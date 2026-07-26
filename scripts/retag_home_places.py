"""One-off retag for Place rows resolved before geocoding.py's automatic
Home-tagging existed (see _tag_home there) - the app already knows where
home is via the home_lat/home_lon/home_radius_m settings (the same ones
_rebuild_trips uses to decide what counts as "away"), so any already-
resolved place within that radius still stuck on the generic "Other places"
fallback gets relabelled "Home" here instead of only ever applying to
places resolved from now on.

Usage:
    python scripts/retag_home_places.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, get_setting, init_db  # noqa: E402
from app.geocoding import _haversine_m  # noqa: E402
from app.models import Place  # noqa: E402


def run() -> None:
    init_db()
    with SessionLocal() as session:
        home_lat = get_setting(session, "home_lat", "")
        home_lon = get_setting(session, "home_lon", "")
        if not home_lat or not home_lon:
            print("No home location configured - nothing to do.")
            return
        home_lat_f, home_lon_f = float(home_lat), float(home_lon)
        radius_m = float(get_setting(session, "home_radius_m", "500"))

        retagged = 0
        for place in session.query(Place).filter(
            Place.category == "Other places", Place.manually_corrected.is_(False)
        ):
            if _haversine_m(place.lat_round, place.lon_round, home_lat_f, home_lon_f) <= radius_m:
                print(f"  {place.id}: {place.name!r} ({place.city}) -> Home")
                place.category = "Home"
                retagged += 1

        session.commit()
        print(f"\nDone. Retagged {retagged} place(s) as Home.")


if __name__ == "__main__":
    run()
